from types import SimpleNamespace
import json
import time

import pytest

from src.core.config import settings
from src.core.db import (
    get_image_extraction,
    reserve_image_extraction,
)
from src.workers import image_worker


class FakeResponse:
    def __init__(self, *, payload=None, content=b"", headers=None, status_code=200):
        self._payload = payload
        self.content = content
        self.headers = headers or {}
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content="Empresa ACME. Guia de R$ 250,00, vencimento 30/07."
                    ),
                )
            ]
        )


class RequestTooLargeError(Exception):
    status_code = 413


class RateLimitError(Exception):
    status_code = 429


def test_retry_after_parses_provider_seconds_and_minutes():
    assert image_worker._retry_after_from_text(
        "Please try again in 13.17s."
    ) == pytest.approx(13.17)
    assert image_worker._retry_after_from_text(
        "Please try again in 12m51.552s."
    ) == pytest.approx(771.552)


def test_retry_delay_never_runs_before_provider_window(monkeypatch):
    monkeypatch.setattr(settings, "image_retry_base_seconds", 2.0)
    monkeypatch.setattr(settings, "image_retry_max_delay_seconds", 900.0)
    monkeypatch.setattr(settings, "image_retry_provider_margin_seconds", 1.0)
    error = image_worker.TransientImageExtractionError(
        "rate limited", retry_after_seconds=13.17
    )

    assert image_worker._retry_delay(error, 2) == pytest.approx(14.17)


def test_extract_image_calls_groq_vision_with_data_uri(monkeypatch):
    responses = iter(
        [
            FakeResponse(
                payload={
                    "file": {
                        "url": "https://files.example/image.png",
                        "mimetype": "image/png",
                    }
                }
            ),
            FakeResponse(
                content=b"png-bytes",
                headers={"content-length": "9"},
            ),
        ]
    )
    monkeypatch.setattr(image_worker.requests, "get", lambda *_a, **_k: next(responses))
    monkeypatch.setattr(settings, "digisac_api_key", "digisac-test")
    monkeypatch.setattr(settings, "groq_api_key", "groq-test")
    monkeypatch.setattr(settings, "image_vision_model", "vision-test")
    completions = FakeCompletions()
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )

    text = image_worker.extract_image_message("image-1", client=client)

    assert text.startswith("Empresa ACME")
    assert completions.kwargs["model"] == "vision-test"
    assert completions.kwargs["reasoning_format"] == "hidden"
    assert (
        completions.kwargs["max_completion_tokens"]
        == settings.image_vision_max_completion_tokens
    )
    assert "max_tokens" not in completions.kwargs
    image_part = completions.kwargs["messages"][0]["content"][1]
    assert image_part["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


def test_image_vision_completion_budget_defaults_below_groq_tpm_limit():
    assert settings.image_vision_max_completion_tokens == 5000


def test_extract_image_reduces_completion_budget_to_fit_tpm(monkeypatch):
    responses = iter(
        [
            FakeResponse(
                payload={
                    "file": {
                        "url": "https://files.example/image.png",
                        "mimetype": "image/png",
                    }
                }
            ),
            FakeResponse(content=b"png-bytes"),
        ]
    )
    monkeypatch.setattr(image_worker.requests, "get", lambda *_a, **_k: next(responses))
    monkeypatch.setattr(settings, "digisac_api_key", "digisac-test")
    monkeypatch.setattr(settings, "groq_api_key", "groq-test")
    monkeypatch.setattr(settings, "image_vision_max_completion_tokens", 8000)
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs["max_completion_tokens"])
            if len(calls) == 1:
                raise RequestTooLargeError(
                    "tokens per minute (TPM): Limit 8000, Requested 10608"
                )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="Comprovante de pagamento."),
                    )
                ]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    assert image_worker.extract_image_message("image-1", client=client)
    assert calls == [8000, 5392]


def test_extract_image_reduces_again_for_tokens_already_used(monkeypatch):
    responses = iter(
        [
            FakeResponse(
                payload={
                    "file": {
                        "url": "https://files.example/image.png",
                        "mimetype": "image/png",
                    }
                }
            ),
            FakeResponse(content=b"png-bytes"),
        ]
    )
    monkeypatch.setattr(image_worker.requests, "get", lambda *_a, **_k: next(responses))
    monkeypatch.setattr(settings, "digisac_api_key", "digisac-test")
    monkeypatch.setattr(settings, "groq_api_key", "groq-test")
    monkeypatch.setattr(settings, "image_vision_max_completion_tokens", 8000)
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs["max_completion_tokens"])
            if len(calls) == 1:
                raise RequestTooLargeError(
                    "tokens per minute (TPM): Limit 8000, Requested 10608"
                )
            if len(calls) == 2:
                raise RateLimitError(
                    "Limit 8000, Used 3875, Requested 8000. "
                    "Please try again in 29.0625s."
                )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="Comprovante de pagamento."),
                    )
                ]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    assert image_worker.extract_image_message("image-1", client=client)
    assert calls == [8000, 5392, 1417]


def test_extract_image_rejects_non_image_mimetype(monkeypatch):
    monkeypatch.setattr(
        image_worker.requests,
        "get",
        lambda *_a, **_k: FakeResponse(
            payload={
                "file": {
                    "url": "https://files.example/file.pdf",
                    "mimetype": "application/pdf",
                }
            }
        ),
    )
    monkeypatch.setattr(settings, "digisac_api_key", "digisac-test")
    monkeypatch.setattr(settings, "groq_api_key", "groq-test")

    with pytest.raises(RuntimeError, match="not an image"):
        image_worker.extract_image_message("document-1")


def test_extract_image_rejects_oversized_download(monkeypatch):
    responses = iter(
        [
            FakeResponse(
                payload={
                    "file": {
                        "url": "https://files.example/image.png",
                        "mimetype": "image/png",
                    }
                }
            ),
            FakeResponse(content=b"12345"),
        ]
    )
    monkeypatch.setattr(image_worker.requests, "get", lambda *_a, **_k: next(responses))
    monkeypatch.setattr(settings, "digisac_api_key", "digisac-test")
    monkeypatch.setattr(settings, "groq_api_key", "groq-test")
    monkeypatch.setattr(settings, "image_max_bytes", 4)

    with pytest.raises(RuntimeError, match="exceeds"):
        image_worker.extract_image_message("image-1")


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_worker_persists_successful_vision_response(monkeypatch):
    await reserve_image_extraction("image-1", "ticket-1", "vision-test")
    monkeypatch.setattr(
        image_worker,
        "extract_image_message",
        lambda _message_id: "Comprovante pago no valor de R$ 250,00.",
    )

    class Redis:
        async def rpush(self, *_args):
            raise AssertionError("successful job must not be requeued")

        async def lrange(self, *_args):
            return []

        async def lrem(self, *_args):
            raise AssertionError("there are no dead letters to remove")

    worker = image_worker.ImageExtractionWorker(Redis())
    await worker.process_job(
        {
            "message_id": "image-1",
            "conversation_id": "ticket-1",
            "attempt": 0,
        }
    )

    row = await get_image_extraction("image-1")
    assert row is not None
    assert row["status"] == "completed"
    assert row["text"] == "Comprovante pago no valor de R$ 250,00."


@pytest.mark.asyncio
async def test_transient_rate_limit_retries_beyond_global_attempt_limit(monkeypatch):
    transitions = []

    async def set_status(message_id, status, **kwargs):
        transitions.append((message_id, status, kwargs))
        return object()

    monkeypatch.setattr(image_worker, "set_image_extraction_status", set_status)
    monkeypatch.setattr(
        image_worker,
        "extract_image_message",
        lambda _message_id: (_ for _ in ()).throw(
            image_worker.TransientImageExtractionError(
                "Groq 429", retry_after_seconds=13.17
            )
        ),
    )
    monkeypatch.setattr(settings, "image_retry_base_seconds", 2.0)
    monkeypatch.setattr(settings, "image_retry_max_delay_seconds", 900.0)
    monkeypatch.setattr(settings, "image_retry_provider_margin_seconds", 1.0)

    class Redis:
        def __init__(self):
            self.published = []

        async def rpush(self, queue, raw):
            self.published.append((queue, json.loads(raw)))

        async def lrange(self, *_args):
            return []

        async def lrem(self, *_args):
            return 0

    redis = Redis()
    worker = image_worker.ImageExtractionWorker(redis)
    before = time.time()
    await worker.process_job(
        {
            "message_id": "image-rate-limit",
            "conversation_id": "ticket-1",
            "attempt": settings.max_retry_attempts,
        }
    )

    assert [item[1] for item in transitions] == ["processing", "pending"]
    assert redis.published == []
    retry_transition = transitions[1][2]
    assert retry_transition["next_attempt_at"].timestamp() >= before + 14.17
    assert worker.rate_limited_until >= before + 14.17


@pytest.mark.asyncio
async def test_recovers_legacy_transient_dead_letter_without_removing_safety_copy(
    monkeypatch,
):
    dead_letter = json.dumps(
        {
            "message_id": "image-dead",
            "conversation_id": "ticket-1",
            "attempt": 2,
        }
    )

    async def get_row(_message_id):
        return {
            "message_id": "image-dead",
            "conversation_id": "ticket-1",
            "model": "vision-test",
            "status": "failed",
            "error_message": (
                "Groq vision request failed: Error code: 429 - "
                "Please try again in 13.17s."
            ),
        }

    async def reserve(*_args):
        return True

    transitions = []

    async def set_status(message_id, status, **kwargs):
        transitions.append((message_id, status, kwargs))
        return object()

    monkeypatch.setattr(image_worker, "get_image_extraction", get_row)
    monkeypatch.setattr(image_worker, "reserve_image_extraction", reserve)
    monkeypatch.setattr(image_worker, "set_image_extraction_status", set_status)
    monkeypatch.setattr(settings, "image_retry_provider_margin_seconds", 1.0)

    class Redis:
        def __init__(self):
            self.published = []
            self.removed = []

        async def lrange(self, queue, _start, _end):
            assert queue == "image_extraction_dead_letter"
            return [dead_letter]

        async def rpush(self, queue, raw):
            self.published.append((queue, json.loads(raw)))

        async def lrem(self, *args):
            self.removed.append(args)
            return 1

    redis = Redis()
    worker = image_worker.ImageExtractionWorker(redis)
    before = time.time()
    assert await worker.recover_transient_dead_letters() == 1

    assert redis.removed == []
    assert redis.published == []
    assert transitions[0][0:2] == ("image-dead", "pending")
    scheduled = transitions[0][2]["next_attempt_at"].timestamp()
    assert scheduled >= before + 8.0
    assert scheduled < before + 14.17
