import json
import subprocess
import time

import pytest

from src.core.config import settings
from src.workers import audio_worker


class Response:
    def __init__(self, *, status_code=200, payload=None, content=b"audio"):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_transcribe_message_calls_digisac_ffmpeg_and_groq(monkeypatch):
    monkeypatch.setattr(settings, "digisac_api_key", "digisac-test")
    monkeypatch.setattr(settings, "groq_api_key", "groq-test")
    get_calls = []
    post_call = {}

    def fake_get(url, **kwargs):
        get_calls.append((url, kwargs))
        if "/messages/" in url:
            return Response(
                payload={"data": {"file": {"url": "https://files/audio.oga"}}}
            )
        return Response(content=b"fake-oga")

    def fake_run(command, **_kwargs):
        # ffmpeg is mocked, but its output must exist for the multipart upload.
        open(command[-1], "wb").write(b"fake-wav")
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_post(url, **kwargs):
        post_call.update(url=url, **kwargs)
        return Response(payload={"text": "  áudio transcrito  "})

    monkeypatch.setattr(audio_worker.requests, "get", fake_get)
    monkeypatch.setattr(audio_worker.requests, "post", fake_post)
    monkeypatch.setattr(audio_worker.subprocess, "run", fake_run)

    assert audio_worker.transcribe_message("message-1") == "áudio transcrito"
    assert get_calls[0][1]["params"] == {"include[0]": "file"}
    assert get_calls[0][1]["headers"]["Authorization"] == "Bearer digisac-test"
    assert post_call["data"] == {
        "model": settings.audio_transcription_model,
        "language": "pt",
        "response_format": "json",
    }
    assert post_call["headers"]["Authorization"] == "Bearer groq-test"


@pytest.mark.asyncio
async def test_transient_audio_failure_requeues_beyond_global_attempt_limit(monkeypatch):
    transitions = []

    async def set_status(message_id, status, **kwargs):
        transitions.append((message_id, status, kwargs))
        return object()

    monkeypatch.setattr(audio_worker, "set_transcription_status", set_status)
    monkeypatch.setattr(
        audio_worker,
        "transcribe_message",
        lambda _message_id: (_ for _ in ()).throw(
            audio_worker.TransientTranscriptionError(
                "Groq transcription returned transient HTTP 429",
                retry_after_seconds=13.17,
            )
        ),
    )
    monkeypatch.setattr(settings, "audio_retry_base_seconds", 2.0)
    monkeypatch.setattr(settings, "audio_retry_max_delay_seconds", 900.0)
    monkeypatch.setattr(settings, "audio_retry_provider_margin_seconds", 1.0)

    class Redis:
        def __init__(self):
            self.published = []

        async def lrange(self, *_args):
            return []

        async def lrem(self, *_args):
            return 0

        async def rpush(self, queue, raw):
            self.published.append((queue, json.loads(raw)))

    redis = Redis()
    worker = audio_worker.AudioTranscriptionWorker(redis)
    before = time.time()
    await worker.process_job(
        {
            "message_id": "audio-rate-limit",
            "conversation_id": "ticket-1",
            "attempt": settings.max_retry_attempts,
        }
    )

    assert [item[1] for item in transitions] == ["processing", "pending"]
    assert redis.published[0][0] == "audio_transcription_queue"
    assert redis.published[0][1]["attempt"] == settings.max_retry_attempts + 1
    assert transitions[1][2]["next_attempt_at"].timestamp() >= before + 14.17


@pytest.mark.asyncio
async def test_recovers_transient_audio_dead_letter_and_keeps_safety_copy(
    monkeypatch,
):
    dead_letter = json.dumps(
        {
            "message_id": "audio-dead",
            "conversation_id": "ticket-1",
            "attempt": 3,
        }
    )
    transitions = []

    async def get_row(_message_id):
        return {
            "message_id": "audio-dead",
            "conversation_id": "ticket-1",
            "model": "whisper-test",
            "status": "failed",
            "error_message": "Groq transcription returned transient HTTP 429",
        }

    async def reserve(*_args):
        return True

    async def set_status(message_id, status, **kwargs):
        transitions.append((message_id, status, kwargs))
        return object()

    monkeypatch.setattr(audio_worker, "get_transcription", get_row)
    monkeypatch.setattr(audio_worker, "reserve_transcription", reserve)
    monkeypatch.setattr(audio_worker, "set_transcription_status", set_status)
    monkeypatch.setattr(settings, "audio_retry_base_seconds", 2.0)
    monkeypatch.setattr(settings, "audio_retry_provider_margin_seconds", 1.0)

    class Redis:
        def __init__(self):
            self.published = []
            self.removed = []

        async def lrange(self, queue, _start, _end):
            if queue == "audio_transcription_dead_letter":
                return [dead_letter]
            return []

        async def rpush(self, queue, raw):
            self.published.append((queue, json.loads(raw)))

        async def lrem(self, *args):
            self.removed.append(args)
            return 1

    redis = Redis()
    worker = audio_worker.AudioTranscriptionWorker(redis)
    assert await worker.recover_transient_dead_letters() == 1

    assert transitions[0][0:2] == ("audio-dead", "pending")
    assert redis.published[0][0] == "audio_transcription_queue"
    assert redis.published[0][1]["attempt"] == 3
    assert redis.removed == []
