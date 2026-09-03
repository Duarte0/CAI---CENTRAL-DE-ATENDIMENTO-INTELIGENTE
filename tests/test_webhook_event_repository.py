import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from src.core.config import settings
from src.core.webhook_event_repository import (
    cleanup_expired_webhook_event_keys,
    count_expired_webhook_event_keys,
    import_legacy_webhook_event_keys,
    record_webhook_event,
)


pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_concurrent_identical_events_have_one_postgresql_winner():
    digest = "a" * 64

    decisions = await asyncio.gather(
        *[record_webhook_event(digest) for _ in range(10)]
    )

    assert sum(decision.accepted for decision in decisions) == 1
    assert Counter(decision.outcome for decision in decisions) == {
        "accepted": 1,
        "duplicate": 9,
    }


@pytest.mark.asyncio
async def test_unexpired_and_expired_event_contract():
    digest = "b" * 64

    first = await record_webhook_event(digest)
    duplicate = await record_webhook_event(digest)
    assert first.outcome == "accepted"
    assert duplicate.outcome == "duplicate"

    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            """
            UPDATE webhook_event_keys
            SET first_seen_at = CURRENT_TIMESTAMP - INTERVAL '2 hours',
                expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second'
            WHERE event_digest = %s
            """,
            (digest,),
        )

    decisions = await asyncio.gather(
        record_webhook_event(digest), record_webhook_event(digest)
    )
    assert Counter(decision.outcome for decision in decisions) == {
        "expired_replaced": 1,
        "duplicate": 1,
    }


@pytest.mark.asyncio
async def test_invalid_digest_is_rejected_before_database_access():
    for digest in ("", "not-a-digest", "A" * 64, "a" * 63):
        with pytest.raises(ValueError, match="SHA-256"):
            await record_webhook_event(digest)


@pytest.mark.asyncio
async def test_legacy_import_is_idempotent_and_cleanup_is_bounded():
    digest = "c" * 64
    imported = await import_legacy_webhook_event_keys([(digest, 300)])
    repeated = await import_legacy_webhook_event_keys([(digest, 300)])
    assert imported == 1
    assert repeated == 0

    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            """
            INSERT INTO webhook_event_keys (
                event_digest, first_seen_at, expires_at
            ) VALUES (%s, %s, %s), (%s, %s, %s)
            """,
            (
                "d" * 64,
                datetime.now(timezone.utc) - timedelta(hours=2),
                datetime.now(timezone.utc) - timedelta(minutes=30),
                "e" * 64,
                datetime.now(timezone.utc) - timedelta(hours=2),
                datetime.now(timezone.utc) - timedelta(minutes=15),
            ),
        )

    assert await count_expired_webhook_event_keys() == 2
    first_cleanup = await cleanup_expired_webhook_event_keys(batch_size=1)
    assert (first_cleanup.before_count, first_cleanup.removed_count) == (2, 1)
    assert first_cleanup.after_count == 1
    second_cleanup = await cleanup_expired_webhook_event_keys(batch_size=1)
    assert (second_cleanup.before_count, second_cleanup.removed_count) == (1, 1)
    assert second_cleanup.after_count == 0
