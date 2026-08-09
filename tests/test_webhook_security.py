import hashlib
import hmac

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.middleware import verify_webhook_signature


def make_request(body: bytes, signature: str) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"x-digisac-signature", signature.encode())],
        },
        receive,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["sha256=", ""])
async def test_signature_accepts_valid_hmac(monkeypatch, prefix):
    monkeypatch.setattr("src.api.middleware.settings.webhook_secret", "secret")
    body = b'{"message":"oi"}'
    digest = hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    await verify_webhook_signature(make_request(body, prefix + digest))


@pytest.mark.asyncio
async def test_signature_rejects_invalid_hmac(monkeypatch):
    monkeypatch.setattr("src.api.middleware.settings.webhook_secret", "secret")

    with pytest.raises(HTTPException) as error:
        await verify_webhook_signature(make_request(b"{}", "bad"))
    assert error.value.status_code == 401
