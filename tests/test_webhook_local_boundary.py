from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import requests


SMOKE_PATH = Path(__file__).with_name("test_webhook_local.py")


def load_smoke_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("webhook_local_smoke", SMOKE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_importing_live_smoke_does_not_make_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(*args: Any, **kwargs: Any) -> None:
        pytest.fail("live webhook smoke request ran during import")

    monkeypatch.setattr(requests, "post", fail_if_called)
    load_smoke_module()


def test_explicit_smoke_preserves_payload_and_reports_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_smoke_module()
    calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    class FakeResponse:
        status_code = 202

        def json(self) -> dict[str, bool]:
            return {"accepted": True}

    def post(
        url: str, *, json: dict[str, Any], headers: dict[str, str]
    ) -> FakeResponse:
        calls.append((url, json, headers))
        return FakeResponse()

    monkeypatch.setattr(module.requests, "post", post)

    assert module.main() == 0

    assert len(calls) == 1
    url, payload, headers = calls[0]
    assert url == "http://localhost:8000/webhook/digisac"
    assert payload == {
        "event": "message.created",
        "conversation_id": "conv-123",
        "message_id": payload["message_id"],
        "content": "Olá, não consigo fazer login no sistema",
        "sender_id": "customer-456",
        "timestamp": "2026-07-17T14:30:00Z",
    }
    assert payload["message_id"].startswith("msg-")
    assert headers == {"Content-Type": "application/json"}
    output = capsys.readouterr().out
    assert "Status: 202" in output
    assert "Resposta: {'accepted': True}" in output


@pytest.mark.parametrize(
    ("exception", "message"),
    [
        (requests.ConnectionError("connection refused"), "connection refused"),
        (requests.Timeout("request timed out"), "request timed out"),
    ],
)
def test_explicit_smoke_reports_request_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exception: requests.RequestException,
    message: str,
) -> None:
    module = load_smoke_module()

    def post(*args: Any, **kwargs: Any) -> None:
        raise exception

    monkeypatch.setattr(module.requests, "post", post)

    assert module.main() == 1

    captured = capsys.readouterr()
    assert "Smoke check failed" in captured.err
    assert message in captured.err


def test_explicit_smoke_reports_non_success_response(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_smoke_module()

    class FakeResponse:
        status_code = 401

        def json(self) -> dict[str, str]:
            return {"detail": "invalid signature"}

    def post(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr(module.requests, "post", post)

    assert module.main() == 1

    captured = capsys.readouterr()
    assert "Status: 401" in captured.out
    assert "Resposta: {'detail': 'invalid signature'}" in captured.out
    assert "HTTP 401" in captured.err
