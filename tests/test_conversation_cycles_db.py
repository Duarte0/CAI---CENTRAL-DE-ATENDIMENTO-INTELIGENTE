import asyncio

import psycopg
import pytest

from src.core.config import settings
from src.core.db import (
    claim_cycle,
    close_cycle,
    create_open_cycle,
    get_cycle,
    get_recoverable_cycles,
    save_cycle_messages,
    wake_unblocked_media_cycles,
)


pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_duplicate_open_and_close_are_idempotent():
    opened = await asyncio.gather(
        *[
            create_open_cycle(
                conversation_id="ticket-cycle",
                started_at="2026-07-28T11:00:00Z",
                open_event_key="open-event",
                start_strategy="test",
            )
            for _ in range(5)
        ]
    )
    assert len({str(item[0]["public_id"]) for item in opened}) == 1
    closed = await asyncio.gather(
        *[
            close_cycle(
                conversation_id="ticket-cycle",
                protocol="123",
                closed_at="2026-07-28T12:00:00Z",
                close_event_key="close-event",
            )
            for _ in range(5)
        ]
    )
    assert len({str(item[0]["public_id"]) for item in closed}) == 1
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM conversation_processing_cycles"
        ).fetchone() == (1,)


@pytest.mark.asyncio
async def test_reopen_creates_second_sequence_and_message_cannot_overlap():
    first, _ = await create_open_cycle(
        conversation_id="ticket-reopen",
        started_at="2026-07-28T10:00:00Z",
        open_event_key="open-1",
        start_strategy="test",
    )
    await close_cycle(
        conversation_id="ticket-reopen",
        protocol="one",
        closed_at="2026-07-28T11:00:00Z",
        close_event_key="close-1",
    )
    second, created = await create_open_cycle(
        conversation_id="ticket-reopen",
        started_at="2026-07-28T12:00:00Z",
        open_event_key="open-2",
        start_strategy="test",
    )
    assert created is True
    assert second["sequence_number"] == 2
    accepted, conflicts = await save_cycle_messages(
        str(first["public_id"]),
        [
            {
                "message_id": "message-exclusive",
                "type": "chat",
                "timestamp": "2026-07-28T10:30:00Z",
            }
        ],
    )
    assert accepted == ["message-exclusive"]
    accepted, conflicts = await save_cycle_messages(
        str(second["public_id"]),
        [
            {
                "message_id": "message-exclusive",
                "type": "chat",
                "timestamp": "2026-07-28T12:30:00Z",
            }
        ],
    )
    assert accepted == []
    assert conflicts == ["message-exclusive"]


@pytest.mark.asyncio
async def test_cycle_claim_uses_lease():
    cycle, _ = await close_cycle(
        conversation_id="ticket-lease",
        protocol="123",
        closed_at="2026-07-28T12:00:00Z",
        close_event_key="close-lease",
    )
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            """
            UPDATE conversation_processing_cycles
            SET next_attempt_at = CURRENT_TIMESTAMP - INTERVAL '1 second'
            WHERE public_id = %s
            """,
            (cycle["public_id"],),
        )
    first = await claim_cycle(
        str(cycle["public_id"]), owner="worker-1", lease_seconds=300
    )
    second = await claim_cycle(
        str(cycle["public_id"]), owner="worker-2", lease_seconds=300
    )
    assert first is not None
    assert second is None
    assert (await get_cycle(str(cycle["public_id"])))["lease_owner"] == "worker-1"


@pytest.mark.asyncio
async def test_cycle_publication_claim_is_atomic():
    cycle, _ = await close_cycle(
        conversation_id="ticket-publication",
        protocol="123",
        closed_at="2026-07-28T12:00:00Z",
        close_event_key="close-publication",
    )
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            """
            UPDATE conversation_processing_cycles
            SET next_attempt_at = CURRENT_TIMESTAMP - INTERVAL '1 second',
                enqueued_at = NULL
            WHERE public_id = %s
            """,
            (cycle["public_id"],),
        )
    claims = await asyncio.gather(
        *[get_recoverable_cycles(limit=10) for _ in range(5)]
    )
    assert sum(len(items) for items in claims) == 1


@pytest.mark.asyncio
async def test_media_blocked_cycle_wakes_after_image_is_recovered():
    cycle, _ = await close_cycle(
        conversation_id="ticket-blocked",
        protocol="123",
        closed_at="2026-07-28T12:00:00Z",
        close_event_key="close-blocked",
    )
    await save_cycle_messages(
        str(cycle["public_id"]),
        [
            {
                "message_id": "image-blocked",
                "type": "image",
                "timestamp": "2026-07-28T11:00:00Z",
            }
        ],
    )
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            """
            INSERT INTO message_image_extractions (
                message_id, conversation_id, model, status, attempt_count,
                created_at, updated_at
            ) VALUES (
                'image-blocked', 'ticket-blocked', 'vision', 'failed', 3,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            UPDATE conversation_processing_cycles
            SET status = 'media_blocked', next_attempt_at = NULL
            WHERE public_id = %s
            """,
            (cycle["public_id"],),
        )
    assert await wake_unblocked_media_cycles(max_attempts=3) == []
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            """
            UPDATE message_image_extractions
            SET status = 'pending', next_attempt_at = CURRENT_TIMESTAMP
            WHERE message_id = 'image-blocked'
            """
        )
    awakened = await wake_unblocked_media_cycles(max_attempts=3)
    assert [str(item["public_id"]) for item in awakened] == [
        str(cycle["public_id"])
    ]
