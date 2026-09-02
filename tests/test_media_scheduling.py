import inspect
import json

import pytest

from src.workers import audio_worker, image_worker


class ExistingQueueRedis:
    def __init__(self, queue: str, message_id: str):
        self.queue = queue
        self.raw = json.dumps(
            {
                "message_id": message_id,
                "conversation_id": "ticket",
                "attempt": 2,
            }
        )

    async def lrange(self, queue, _start, _end):
        assert queue == self.queue
        return [self.raw, self.raw]

    async def rpush(self, *_args):
        raise AssertionError("an existing publication must not be duplicated")


@pytest.mark.asyncio
async def test_image_reconciler_does_not_republish_existing_job(monkeypatch):
    async def claims(**_kwargs):
        return [
            {
                "message_id": "image",
                "conversation_id": "ticket",
                "attempt_count": 2,
                "updated_at": "2026-07-28T12:00:00+00:00",
            }
        ]

    monkeypatch.setattr(
        image_worker, "recover_stale_image_extractions", claims
    )
    worker = image_worker.ImageExtractionWorker(
        ExistingQueueRedis("image_extraction_queue", "image")
    )
    assert await worker.recover_stale_jobs() == 0


def test_audio_reconciler_has_no_redis_transport_dependency():
    source = inspect.getsource(audio_worker.AudioTranscriptionWorker)
    assert "redis" not in source.lower()
