import json
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest

from src.core.config import settings
from src.core.db import (
    close_cycle,
    get_cycle,
    get_image_extraction,
    get_transcription,
    reserve_image_extraction,
    reserve_transcription,
    save_cycle_messages,
    set_image_extraction_status,
    set_transcription_status,
)
from src.workers import audio_worker, ia_worker, image_worker


pytestmark = pytest.mark.postgres


class QueueTransport:
    """Deterministic Redis-compatible transport for recovery publication tests."""

    def __init__(
        self,
        queued_jobs: list[dict[str, Any]] | None = None,
        queued_queue: str = "ia_queue",
    ) -> None:
        self.queues: dict[str, list[str]] = {}
        self.published: list[tuple[str, dict[str, Any]]] = []
        self.fail_cycle_id: str | None = None
        if queued_jobs:
            self.queues[queued_queue] = [
                json.dumps(job, sort_keys=True) for job in queued_jobs
            ]

    async def lrange(self, queue: str, start: int, end: int) -> list[str]:
        del start, end
        return list(self.queues.get(queue, []))

    async def lrem(self, queue: str, count: int, value: str) -> int:
        del count
        values = self.queues.get(queue, [])
        try:
            values.remove(value)
        except ValueError:
            return 0
        return 1

    async def rpush(self, queue: str, *values: str) -> int:
        for raw in values:
            job = json.loads(raw)
            if (
                self.fail_cycle_id is not None
                and job.get("cycle_id") == self.fail_cycle_id
            ):
                raise RuntimeError("synthetic queue publish failure")
            self.queues.setdefault(queue, []).append(raw)
            self.published.append((queue, job))
        return len(self.queues[queue])


def make_ia_worker(queue: QueueTransport) -> ia_worker.IAWorker:
    worker = object.__new__(ia_worker.IAWorker)
    worker.redis = queue
    worker.queue = "ia_queue"
    worker.max_retries = settings.max_retry_attempts
    return worker


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["audio", "image"])
async def test_media_recovery_is_due_only_and_queue_idempotent(kind: str):
    message_ids = {
        "due": f"{kind}-recovery-due",
        "queued": f"{kind}-recovery-queued",
        "future": f"{kind}-recovery-future",
    }
    if kind == "audio":
        reserve = reserve_transcription
        set_status = set_transcription_status
        get_content = get_transcription
        table = "message_transcriptions"
        worker: Any = audio_worker.AudioTranscriptionWorker(
            QueueTransport(
                [
                    {
                        "message_id": message_ids["queued"],
                        "conversation_id": "safe-ticket",
                        "attempt": 1,
                    }
                ],
                queued_queue="audio_transcription_queue",
            )
        )
    else:
        reserve = reserve_image_extraction
        set_status = set_image_extraction_status
        get_content = get_image_extraction
        table = "message_image_extractions"
        worker = image_worker.ImageExtractionWorker(
            QueueTransport(
                [
                    {
                        "message_id": message_ids["queued"],
                        "conversation_id": "safe-ticket",
                        "attempt": 1,
                    }
                ],
                queued_queue="image_extraction_queue",
            )
        )

    for message_id in message_ids.values():
        assert await reserve(message_id, "safe-ticket", "test-model")
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            f"""
            UPDATE {table}
            SET enqueued_at = CURRENT_TIMESTAMP - INTERVAL '10 minutes'
            WHERE message_id IN (%s, %s)
            """,
            (message_ids["due"], message_ids["queued"]),
        )
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    assert await set_status(
        message_ids["future"],
        "pending",
        next_attempt_at=future,
        expected_statuses=("pending",),
    )

    assert await worker.recover_stale_jobs() == 1
    queue = worker.redis
    queued_ids = {
        json.loads(raw)["message_id"]
        for raw in await queue.lrange(worker.queue, 0, -1)
    }
    assert queued_ids == {message_ids["due"], message_ids["queued"]}
    assert (await get_content(message_ids["due"]))["enqueued_at"] is not None
    assert (await get_content(message_ids["queued"]))["enqueued_at"] is not None
    assert await worker.recover_stale_jobs() == 0
    future_row = await get_content(message_ids["future"])
    assert future_row is not None
    assert future_row["status"] == "pending"
    assert future_row["next_attempt_at"] is not None


@pytest.mark.asyncio
async def test_audio_transient_dead_letter_recovery_keeps_postgres_pending_and_safety_copy():
    message_id = "audio-transient-dead-letter-db"
    assert await reserve_transcription(message_id, "safe-ticket", "test-model")
    assert await set_transcription_status(
        message_id,
        "failed",
        error_message="transient_audio_failure:http_429",
        expected_statuses=("pending",),
    )

    dead_letter = json.dumps(
        {
            "message_id": message_id,
            "conversation_id": "safe-ticket",
            "attempt": 3,
        }
    )
    queue = QueueTransport(
        [{"message_id": message_id, "conversation_id": "safe-ticket", "attempt": 3}],
        queued_queue="audio_transcription_dead_letter",
    )
    queue.queues["audio_transcription_dead_letter"] = [dead_letter]
    worker = audio_worker.AudioTranscriptionWorker(queue)

    assert await worker.recover_transient_dead_letters() == 1
    row = await get_transcription(message_id)
    assert row is not None
    assert row["status"] == "pending"
    assert row["next_attempt_at"] is not None
    assert {
        json.loads(raw)["message_id"]
        for raw in await queue.lrange("audio_transcription_queue", 0, -1)
    } == {message_id}
    assert await queue.lrange("audio_transcription_dead_letter", 0, -1) == [dead_letter]


@pytest.mark.asyncio
async def test_successful_image_recovery_wakes_only_its_blocked_cycle_without_ia_queue(monkeypatch):
    target, _ = await close_cycle(
        conversation_id="recovery-image-target",
        protocol="test-protocol",
        closed_at="2026-08-09T12:00:00Z",
        close_event_key="close-image-target",
    )
    unrelated, _ = await close_cycle(
        conversation_id="recovery-image-unrelated",
        protocol="test-protocol",
        closed_at="2026-08-09T12:00:00Z",
        close_event_key="close-image-unrelated",
    )
    await save_cycle_messages(
        str(target["public_id"]),
        [
            {
                "message_id": "image-recovery-target",
                "type": "image",
                "timestamp": "2026-08-09T11:00:00Z",
            }
        ],
    )
    await save_cycle_messages(
        str(unrelated["public_id"]),
        [
            {
                "message_id": "image-recovery-unrelated",
                "type": "image",
                "timestamp": "2026-08-09T11:00:00Z",
            }
        ],
    )
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            """
            INSERT INTO message_image_extractions (
                message_id, conversation_id, model, status, attempt_count,
                created_at, updated_at
            ) VALUES
                ('image-recovery-target', 'recovery-image-target', 'vision', 'failed', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                ('image-recovery-unrelated', 'recovery-image-unrelated', 'vision', 'failed', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (settings.max_retry_attempts, settings.max_retry_attempts),
        )
        connection.execute(
            """
            UPDATE conversation_processing_cycles
            SET status = 'media_blocked', next_attempt_at = NULL
            WHERE public_id IN (%s, %s)
            """,
            (target["public_id"], unrelated["public_id"]),
        )

    queue = QueueTransport()
    await make_ia_worker(queue)._reconcile_cycles()
    assert [job for name, job in queue.published if name == "ia_queue"] == []
    assert (await get_cycle(str(target["public_id"])))['status'] == "media_blocked"

    assert await reserve_image_extraction(
        "image-recovery-target", "recovery-image-target", "vision"
    )
    monkeypatch.setattr(
        image_worker,
        "extract_image_message",
        lambda _message_id: "safe recovered image text",
    )
    await image_worker.ImageExtractionWorker(queue).process_job(
        {
            "message_id": "image-recovery-target",
            "conversation_id": "recovery-image-target",
            "attempt": 0,
        }
    )
    target_image = await get_image_extraction("image-recovery-target")
    assert target_image is not None
    assert target_image["status"] == "completed"

    await make_ia_worker(queue)._reconcile_cycles()
    assert [job for name, job in queue.published if name == "ia_queue"] == []
    assert (await get_cycle(str(target["public_id"])))['status'] == "waiting_media"
    assert (await get_cycle(str(unrelated["public_id"])))['status'] == "media_blocked"
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM ia_classifications"
        ).fetchone() == (0,)
