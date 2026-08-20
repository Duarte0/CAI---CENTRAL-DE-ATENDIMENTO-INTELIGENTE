import json
import subprocess
import time

import pytest

from src.core.config import settings
from src.workers import audio_worker


class Response:
    def __init__(
        self,
        *,
        status_code=200,
        payload=None,
        content=b"audio",
        text="",
        headers=None,
    ):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content
        self.text = text
        self.headers = headers or {}

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


def test_transient_http_error_does_not_include_provider_response_body():
    response = Response(
        status_code=503,
        text="provider secret response that must not be persisted",
    )

    with pytest.raises(audio_worker.TransientTranscriptionError) as error:
        audio_worker._raise_for_status(response, "Groq transcription")

    assert "provider secret response" not in str(error.value)


@pytest.mark.asyncio
async def test_transient_retry_persists_only_safe_error_metadata(
    monkeypatch, caplog
):
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
                "raw provider payload with secret-token",
                retry_after_seconds=0,
            )
        ),
    )

    class Redis:
        async def lrange(self, *_args):
            return []

        async def lrem(self, *_args):
            return 0

        async def rpush(self, *_args):
            return 1

    await audio_worker.AudioTranscriptionWorker(Redis()).process_job(
        {"message_id": "audio-safe-error", "attempt": 3}
    )

    error_message = transitions[1][2]["error_message"]
    assert error_message.startswith("transient_audio_failure:")
    assert "secret-token" not in error_message
    assert "raw provider payload" not in caplog.text


@pytest.mark.asyncio
async def test_permanent_audio_failure_dead_letters_once_with_incremented_attempt(
    monkeypatch,
):
    transitions = []

    async def set_status(message_id, status, **kwargs):
        transitions.append((message_id, status, kwargs))
        return object()

    monkeypatch.setattr(audio_worker, "set_transcription_status", set_status)
    monkeypatch.setattr(
        audio_worker,
        "transcribe_message",
        lambda _message_id: (_ for _ in ()).throw(
            RuntimeError("signed-download-url and raw provider payload")
        ),
    )

    class Redis:
        def __init__(self):
            self.dead_letters = []

        async def lrange(self, queue, *_args):
            if queue == "audio_transcription_dead_letter":
                return list(self.dead_letters)
            return []

        async def lrem(self, queue, _count, raw):
            if queue == "audio_transcription_dead_letter":
                self.dead_letters = [item for item in self.dead_letters if item != raw]
            return 0

        async def rpush(self, queue, raw):
            assert queue == "audio_transcription_dead_letter"
            self.dead_letters.append(raw)
            return len(self.dead_letters)

    redis = Redis()
    await audio_worker.AudioTranscriptionWorker(redis).process_job(
        {"message_id": "audio-permanent", "attempt": 0}
    )

    assert transitions[1][1] == "failed"
    assert transitions[1][2]["error_message"] == "audio_transcription_failed"
    assert len(redis.dead_letters) == 1
    assert json.loads(redis.dead_letters[0])["attempt"] == 1


@pytest.mark.asyncio
async def test_dead_letter_recovery_collapses_duplicate_queue_and_safety_entries(
    monkeypatch,
):
    job = json.dumps(
        {"message_id": "audio-duplicate", "conversation_id": "ticket-1", "attempt": 3}
    )
    transitions = []

    async def get_row(_message_id):
        return {
            "message_id": "audio-duplicate",
            "conversation_id": "ticket-1",
            "model": "whisper-test",
            "status": "failed",
            "error_message": "Groq transcription returned transient HTTP 429",
        }

    async def set_status(message_id, status, **kwargs):
        transitions.append((message_id, status, kwargs))
        return object()

    monkeypatch.setattr(audio_worker, "get_transcription", get_row)
    monkeypatch.setattr(audio_worker, "set_transcription_status", set_status)

    class Redis:
        def __init__(self):
            self.queues = {"audio_transcription_queue": [job, job]}
            self.queues["audio_transcription_dead_letter"] = [job, job]

        async def lrange(self, queue, _start, _end):
            return list(self.queues[queue])

        async def lrem(self, queue, count, raw):
            items = self.queues[queue]
            removed = 0
            remaining = []
            for item in items:
                if item == raw and (count == 0 or removed < count):
                    removed += 1
                else:
                    remaining.append(item)
            self.queues[queue] = remaining
            return removed

        async def rpush(self, queue, raw):
            self.queues[queue].append(raw)
            return len(self.queues[queue])

    redis = Redis()
    worker = audio_worker.AudioTranscriptionWorker(redis)

    assert await worker.recover_transient_dead_letters() == 0
    assert len(redis.queues["audio_transcription_queue"]) == 1
    assert len(redis.queues["audio_transcription_dead_letter"]) == 1
    assert transitions == []


@pytest.mark.asyncio
async def test_dead_letter_recovery_does_not_retry_non_transient_failure(monkeypatch):
    dead_letter = json.dumps(
        {"message_id": "audio-permanent-dead", "conversation_id": "ticket-1"}
    )
    transitions = []

    async def get_row(_message_id):
        return {
            "message_id": "audio-permanent-dead",
            "conversation_id": "ticket-1",
            "model": "whisper-test",
            "status": "failed",
            "error_message": "audio_file_missing",
        }

    async def set_status(*args, **kwargs):
        transitions.append((args, kwargs))
        return object()

    monkeypatch.setattr(audio_worker, "get_transcription", get_row)
    monkeypatch.setattr(audio_worker, "set_transcription_status", set_status)

    class Redis:
        async def lrange(self, queue, *_args):
            if queue == "audio_transcription_dead_letter":
                return [dead_letter]
            return []

        async def lrem(self, *_args):
            raise AssertionError("a non-transient dead-letter must be retained")

        async def rpush(self, *_args):
            raise AssertionError("a non-transient dead-letter must not be retried")

    assert await audio_worker.AudioTranscriptionWorker(
        Redis()
    ).recover_transient_dead_letters() == 0
    assert transitions == []


@pytest.mark.asyncio
async def test_success_removes_matching_dead_letter_after_persisted_completion(
    monkeypatch,
):
    transitions = []
    dead_letter = json.dumps({"message_id": "audio-recovered"})
    unrelated = json.dumps({"message_id": "audio-other"})

    async def set_status(message_id, status, **kwargs):
        transitions.append((message_id, status, kwargs))
        return object()

    monkeypatch.setattr(audio_worker, "set_transcription_status", set_status)
    monkeypatch.setattr(audio_worker, "transcribe_message", lambda _id: "persisted text")

    class Redis:
        def __init__(self):
            self.dead_letters = [dead_letter, unrelated]

        async def lrange(self, queue, *_args):
            if queue == "audio_transcription_dead_letter":
                return list(self.dead_letters)
            return []

        async def lrem(self, queue, _count, raw):
            assert queue == "audio_transcription_dead_letter"
            self.dead_letters = [item for item in self.dead_letters if item != raw]
            return 1

        async def rpush(self, *_args):
            raise AssertionError("a successful transcription must not be dead-lettered")

    redis = Redis()
    await audio_worker.AudioTranscriptionWorker(redis).process_job(
        {"message_id": "audio-recovered", "attempt": 4}
    )

    assert [status for _, status, _ in transitions] == ["processing", "completed"]
    assert redis.dead_letters == [unrelated]


@pytest.mark.asyncio
async def test_empty_transcription_is_not_completed_or_removed_from_dead_letter(
    monkeypatch,
):
    transitions = []
    dead_letter = json.dumps({"message_id": "audio-empty"})

    async def set_status(message_id, status, **kwargs):
        transitions.append((message_id, status, kwargs))
        return object()

    monkeypatch.setattr(audio_worker, "set_transcription_status", set_status)
    monkeypatch.setattr(audio_worker, "transcribe_message", lambda _id: "  ")

    class Redis:
        def __init__(self):
            self.dead_letters = [dead_letter]

        async def lrange(self, queue, *_args):
            if queue == "audio_transcription_dead_letter":
                return list(self.dead_letters)
            return []

        async def lrem(self, queue, _count, raw):
            assert queue == "audio_transcription_dead_letter"
            self.dead_letters = [item for item in self.dead_letters if item != raw]
            return 1

        async def rpush(self, queue, raw):
            assert queue == "audio_transcription_dead_letter"
            self.dead_letters.append(raw)
            return len(self.dead_letters)

    redis = Redis()
    await audio_worker.AudioTranscriptionWorker(redis).process_job(
        {"message_id": "audio-empty", "attempt": 2}
    )

    assert [status for _, status, _ in transitions] == ["processing", "failed"]
    assert len(redis.dead_letters) == 1


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

    async def set_status(message_id, status, **kwargs):
        transitions.append((message_id, status, kwargs))
        return object()

    monkeypatch.setattr(audio_worker, "get_transcription", get_row)
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
