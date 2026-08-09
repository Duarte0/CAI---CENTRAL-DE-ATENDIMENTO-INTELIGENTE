import hashlib
import hmac
import json

import httpx
import pytest

from src.api import routes
from src.api.routes import app


async def post_webhook(path: str, body: bytes, **kwargs: object) -> httpx.Response:
    app.dependency_overrides[routes.get_redis] = lambda: object()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.post(path, content=body, **kwargs)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/webhook/debug", "/debug/webhook"])
async def test_obsolete_diagnostic_routes_are_not_served(path):
    marker = "raw-webhook-marker"
    response = await post_webhook(
        path,
        json.dumps({"event": "diagnostic", "marker": marker}).encode(),
    )

    assert response.status_code == 404
    assert marker not in response.text


@pytest.mark.asyncio
async def test_production_webhook_keeps_sanitized_ignored_response_with_hmac(
    monkeypatch, caplog
):
    secret = "test-secret"
    monkeypatch.setattr("src.api.middleware.settings.webhook_secret", secret)
    marker = "raw-webhook-marker"
    body = json.dumps(
        {
            "event": "unsupported.event",
            "data": {"id": "ticket-1", "sensitive": marker},
        }
    ).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    response = await post_webhook(
        "/webhook/digisac",
        body,
        headers={"X-Digisac-Signature": f"sha256={signature}"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": "unsupported_event"}
    assert marker not in response.text
    assert marker not in caplog.text


@pytest.mark.asyncio
async def test_invalid_production_signature_stops_before_parsing(monkeypatch):
    monkeypatch.setattr("src.api.middleware.settings.webhook_secret", "test-secret")

    async def parse_must_not_run(_request):
        raise AssertionError("invalid signatures must not reach payload parsing")

    monkeypatch.setattr(routes, "parse_webhook_payload", parse_must_not_run)
    marker = "raw-webhook-marker"
    response = await post_webhook(
        "/webhook/digisac",
        json.dumps({"event": "message.created", "marker": marker}).encode(),
        headers={"X-Digisac-Signature": "invalid"},
    )

    assert response.status_code == 401
    assert marker not in response.text
