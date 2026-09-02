import inspect
import subprocess
import time
from datetime import datetime, timezone

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


def _claim(message_id: str, *, attempt_count: int = 1):
    return {
        "message_id": message_id,
        "conversation_id": "ticket-1",
        "model": "whisper-test",
        "status": "processing",
        "attempt_count": attempt_count,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "lease_owner": "test-owner",
        "lease_expires_at": datetime.now(timezone.utc).isoformat(),
    }


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
        with open(command[-1], "wb") as output:
            output.write(b"fake-wav")
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
async def test_worker_claims_durable_row_without_redis(monkeypatch):
    calls = []
    claim = _claim("audio-safe-error", attempt_count=4)

    async def claim_next(**kwargs):
        calls.append(kwargs)
        return claim

    async def set_status(message_id, status, **kwargs):
        calls.append((message_id, status, kwargs))
        return object()

    monkeypatch.setattr(audio_worker, "claim_next_transcription", claim_next)
    monkeypatch.setattr(audio_worker, "set_transcription_status", set_status)
    monkeypatch.setattr(
        audio_worker,
        "transcribe_message",
        lambda _message_id: "safe transcript",
    )

    worker = audio_worker.AudioTranscriptionWorker(owner="test-owner")
    assert not hasattr(worker, "redis")
    await worker.process_job({"message_id": "audio-safe-error"})

    assert calls[0] == {
        "owner": "test-owner",
        "lease_seconds": settings.content_recovery_lease_seconds,
        "message_id": "audio-safe-error",
    }
    assert calls[1][0:2] == ("audio-safe-error", "completed")
    assert calls[1][2]["expected_lease_owner"] == "test-owner"


@pytest.mark.asyncio
async def test_transient_retry_persists_schedule_without_republication(monkeypatch):
    transitions = []

    async def claim_next(**_kwargs):
        return _claim("audio-rate-limit", attempt_count=4)

    async def set_status(message_id, status, **kwargs):
        transitions.append((message_id, status, kwargs))
        return object()

    monkeypatch.setattr(audio_worker, "claim_next_transcription", claim_next)
    monkeypatch.setattr(audio_worker, "set_transcription_status", set_status)
    monkeypatch.setattr(
        audio_worker,
        "transcribe_message",
        lambda _message_id: (_ for _ in ()).throw(
            audio_worker.TransientTranscriptionError(
                "raw provider payload with secret-token",
                retry_after_seconds=13.17,
            )
        ),
    )
    monkeypatch.setattr(settings, "audio_retry_base_seconds", 2.0)
    monkeypatch.setattr(settings, "audio_retry_max_delay_seconds", 900.0)
    monkeypatch.setattr(settings, "audio_retry_provider_margin_seconds", 1.0)

    worker = audio_worker.AudioTranscriptionWorker(owner="test-owner")
    await worker.process_job({"message_id": "audio-rate-limit"})

    assert transitions[0][0:2] == ("audio-rate-limit", "pending")
    retry_at = transitions[0][2]["next_attempt_at"]
    assert retry_at > datetime.now(timezone.utc)
    assert transitions[0][2]["expected_lease_owner"] == "test-owner"
    assert transitions[0][2]["error_message"] == "transient_audio_failure:transient_provider"
    assert "secret-token" not in transitions[0][2]["error_message"]
    assert worker.rate_limited_until > time.time()


@pytest.mark.asyncio
async def test_permanent_failure_is_durable_without_dead_letter_publication(
    monkeypatch,
):
    transitions = []

    async def claim_next(**_kwargs):
        return _claim("audio-permanent")

    async def set_status(message_id, status, **kwargs):
        transitions.append((message_id, status, kwargs))
        return object()

    monkeypatch.setattr(audio_worker, "claim_next_transcription", claim_next)
    monkeypatch.setattr(audio_worker, "set_transcription_status", set_status)
    monkeypatch.setattr(
        audio_worker,
        "transcribe_message",
        lambda _message_id: (_ for _ in ()).throw(
            RuntimeError("signed-download-url and raw provider payload")
        ),
    )

    await audio_worker.AudioTranscriptionWorker(owner="test-owner").process_job(
        {"message_id": "audio-permanent"}
    )

    assert transitions[0][1] == "failed"
    assert transitions[0][2]["error_message"] == "audio_transcription_failed"
    assert transitions[0][2]["expected_lease_owner"] == "test-owner"


@pytest.mark.asyncio
async def test_empty_transcription_is_failed_and_never_completed(monkeypatch):
    transitions = []

    async def claim_next(**_kwargs):
        return _claim("audio-empty")

    async def set_status(message_id, status, **kwargs):
        transitions.append((message_id, status, kwargs))
        return object()

    monkeypatch.setattr(audio_worker, "claim_next_transcription", claim_next)
    monkeypatch.setattr(audio_worker, "set_transcription_status", set_status)
    monkeypatch.setattr(audio_worker, "transcribe_message", lambda _id: "  ")

    await audio_worker.AudioTranscriptionWorker(owner="test-owner").process_job(
        {"message_id": "audio-empty"}
    )

    assert [status for _message_id, status, _kwargs in transitions] == ["failed"]
    assert transitions[0][2]["error_message"] == "audio_transcription_empty"


@pytest.mark.asyncio
async def test_provider_cooldown_is_checked_before_claim(monkeypatch):
    called = False

    async def claim_next(**_kwargs):
        nonlocal called
        called = True
        return _claim("must-not-claim")

    monkeypatch.setattr(audio_worker, "claim_next_transcription", claim_next)
    worker = audio_worker.AudioTranscriptionWorker(owner="test-owner")
    worker.rate_limited_until = time.time() + 60

    assert await worker.poll_once() is False
    assert called is False


def test_active_audio_worker_has_no_redis_list_operations():
    source = inspect.getsource(audio_worker.AudioTranscriptionWorker)
    assert "audio_transcription_queue" not in source
    assert "audio_transcription_dead_letter" not in source
    assert "lpop" not in source
    assert "rpush" not in source
    assert "lrange" not in source
    assert "lrem" not in source
