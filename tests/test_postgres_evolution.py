import asyncio
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from src.core.config import settings
from src.core.db import (
    claim_next_transcription,
    get_transcription,
    insert_classification,
    recover_stale_transcriptions,
    reserve_transcription,
    set_transcription_status,
)
from src.utils.backfill_classification_messages import (
    backfill as backfill_messages,
)
from src.utils.backfill_classification_public_ids import (
    backfill as backfill_public_ids,
)

pytestmark = pytest.mark.postgres


def _classification_kwargs(idempotency_key: str | None = None):
    return {
        "conversation_id": "conversation-evolution",
        "message_ids": ["message-a", "message-b"],
        "created_at": "2026-07-27T12:00:00+00:00",
        "full_context": "Cliente: teste",
        "message_count": 2,
        "result": {
            "intent_type": "question",
            "confidence": 0.8,
            "title": "Teste",
        },
        "model": "test-model",
        "processing_time_ms": 5,
        "prompt_version": "test",
        "idempotency_key": idempotency_key,
    }


@pytest.mark.asyncio
async def test_classification_has_public_uuid_and_normalized_messages():
    identity = await insert_classification(**_classification_kwargs())
    assert identity.public_id is not None
    assert identity.public_id.version == 7
    with psycopg.connect(settings.database_url) as connection:
        row = connection.execute(
            """
            SELECT public_id, message_ids
            FROM ia_classifications
            WHERE id = %s
            """,
            (identity.id,),
        ).fetchone()
        links = connection.execute(
            """
            SELECT message_id, position
            FROM classification_messages
            WHERE classification_id = %s
            ORDER BY position
            """,
            (identity.id,),
        ).fetchall()
    assert row == (identity.public_id, ["message-a", "message-b"])
    assert links == [("message-a", 0), ("message-b", 1)]


@pytest.mark.asyncio
async def test_classification_idempotency_is_atomic_under_concurrency():
    identities = await asyncio.gather(
        *[
            insert_classification(
                **_classification_kwargs("ia:conversation-evolution:task-1")
            )
            for _ in range(10)
        ]
    )
    assert {item.id for item in identities} == {identities[0].id}
    assert {item.public_id for item in identities} == {identities[0].public_id}
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM ia_classifications"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM classification_messages"
        ).fetchone() == (2,)


@pytest.mark.asyncio
async def test_normalization_backfill_is_validated_and_idempotent():
    identity = await insert_classification(**_classification_kwargs())
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            "DELETE FROM classification_messages WHERE classification_id = %s",
            (identity.id,),
        )
    dry_run = await asyncio.to_thread(
        backfill_messages,
        settings.database_url,
    )
    assert dry_run.inconsistent_links == 2
    applied = await asyncio.to_thread(
        backfill_messages,
        settings.database_url,
        apply=True,
        batch_size=1,
    )
    assert applied.inserted_links == 2
    assert applied.inconsistent_links == 0
    repeated = await asyncio.to_thread(
        backfill_messages,
        settings.database_url,
        apply=True,
        batch_size=1,
    )
    assert repeated.inserted_links == 0
    assert repeated.inconsistent_links == 0
    public_ids = await asyncio.to_thread(
        backfill_public_ids,
        settings.database_url,
    )
    assert public_ids.missing_before == 0


@pytest.mark.asyncio
async def test_quality_constraint_rejects_invalid_confidence():
    kwargs = _classification_kwargs()
    kwargs["result"] = {"intent_type": "question", "confidence": 1.5}
    with pytest.raises(psycopg.errors.CheckViolation):
        await insert_classification(**kwargs)


def test_assignment_history_requires_existing_event_key():
    with psycopg.connect(settings.database_url) as connection:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                """
                INSERT INTO ticket_assignment_history (
                    conversation_id, event_timestamp, event_key, created_at
                ) VALUES (
                    'conversation-fk', CURRENT_TIMESTAMP,
                    'missing-event-key', CURRENT_TIMESTAMP
                )
                """
            )


@pytest.mark.asyncio
async def test_media_status_lease_blocks_duplicate_and_stale_completion():
    assert await reserve_transcription(
        "message-lease", "conversation-lease", "test-model"
    )
    old_lease = await set_transcription_status(
        "message-lease", "processing", increment_attempt=True
    )
    assert isinstance(old_lease, datetime)
    assert (
        await set_transcription_status(
            "message-lease", "processing", increment_attempt=True
        )
        is None
    )
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            """
            UPDATE message_transcriptions
            SET created_at = created_at - INTERVAL '10 minutes',
                updated_at = updated_at - INTERVAL '10 minutes'
            WHERE message_id = 'message-lease'
            """
        )
    recovered = await recover_stale_transcriptions(
        lease_seconds=300,
        batch_size=10,
    )
    assert [row["message_id"] for row in recovered] == ["message-lease"]
    assert (
        await set_transcription_status(
            "message-lease",
            "completed",
            text="stale text",
            expected_updated_at=old_lease,
        )
        is None
    )
    new_lease = await set_transcription_status(
        "message-lease", "processing", increment_attempt=True
    )
    assert isinstance(new_lease, datetime)
    assert await set_transcription_status(
        "message-lease",
        "completed",
        text="current text",
        expected_updated_at=new_lease,
    )


@pytest.mark.asyncio
async def test_future_media_schedule_is_claimed_once_when_due():
    assert await reserve_transcription(
        "message-scheduled", "conversation-scheduled", "test-model"
    )
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    assert await set_transcription_status(
        "message-scheduled",
        "pending",
        next_attempt_at=future,
        expected_statuses=("pending",),
    )
    assert await recover_stale_transcriptions(
        lease_seconds=300,
        batch_size=10,
    ) == []
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            """
            UPDATE message_transcriptions
            SET next_attempt_at = CURRENT_TIMESTAMP - INTERVAL '1 second',
                enqueued_at = NULL
            WHERE message_id = 'message-scheduled'
            """
        )
    claims = await asyncio.gather(
        *[
            recover_stale_transcriptions(
                lease_seconds=300,
                batch_size=10,
            )
            for _ in range(5)
        ]
    )
    assert sum(len(items) for items in claims) == 1


@pytest.mark.asyncio
async def test_audio_polling_claim_is_atomic_due_aware_and_lease_owned():
    message_id = "audio-polling-concurrent"
    assert await reserve_transcription(
        message_id, "conversation-audio-polling", "test-model"
    )

    claims = await asyncio.gather(
        *[
            claim_next_transcription(owner=f"audio-worker-{index}", lease_seconds=300)
            for index in range(10)
        ]
    )
    received = [claim for claim in claims if claim is not None]
    assert len(received) == 1
    claimed = received[0]
    assert claimed["message_id"] == message_id
    assert claimed["status"] == "processing"
    assert claimed["attempt_count"] == 1
    assert claimed["lease_owner"].startswith("audio-worker-")
    assert claimed["lease_expires_at"] is not None
    assert await claim_next_transcription(owner="late-worker", lease_seconds=300) is None

    row = await get_transcription(message_id)
    assert row is not None
    assert row["status"] == "processing"
    assert row["enqueued_at"] is None


@pytest.mark.asyncio
async def test_audio_polling_does_not_claim_future_retry_and_recovers_expired_lease():
    future_id = "audio-polling-future"
    stale_id = "audio-polling-stale"
    assert await reserve_transcription(future_id, "conversation", "test-model")
    assert await reserve_transcription(stale_id, "conversation", "test-model")
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    assert await set_transcription_status(
        future_id,
        "pending",
        next_attempt_at=future,
        expected_statuses=("pending",),
    )
    stale_claim = await claim_next_transcription(owner="old-worker", lease_seconds=300)
    assert stale_claim is not None
    assert stale_claim["message_id"] == stale_id
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            """
            UPDATE message_transcriptions
            SET lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second'
            WHERE message_id = %s
            """,
            (stale_id,),
        )

    assert await claim_next_transcription(
        owner="before-due", lease_seconds=300, message_id=future_id
    ) is None
    recovered = await claim_next_transcription(
        owner="new-worker", lease_seconds=300, message_id=stale_id
    )
    assert recovered is not None
    assert recovered["previous_status"] == "processing"
    assert recovered["lease_owner"] == "new-worker"
    assert recovered["attempt_count"] == 2
    assert recovered["error_message"] == "recovered after processing lease expired"


@pytest.mark.asyncio
async def test_audio_completion_requires_current_lease_owner():
    message_id = "audio-polling-owner"
    assert await reserve_transcription(message_id, "conversation", "test-model")
    claim = await claim_next_transcription(owner="current-worker", lease_seconds=300)
    assert claim is not None
    assert (
        await set_transcription_status(
            message_id,
            "completed",
            text="stale completion",
            expected_updated_at=claim["updated_at"],
            expected_lease_owner="other-worker",
        )
        is None
    )
    assert await set_transcription_status(
        message_id,
        "completed",
        text="valid completion",
        expected_updated_at=claim["updated_at"],
        expected_lease_owner="current-worker",
    )
