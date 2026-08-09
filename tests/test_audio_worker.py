import subprocess

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
