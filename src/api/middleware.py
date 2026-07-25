"""HTTP middleware used by the webhook API."""

import hashlib
import hmac

from fastapi import HTTPException, Request

from src.core.config import settings


async def verify_webhook_signature(request: Request) -> None:
    """Validate the raw-body HMAC when WEBHOOK_SECRET is configured.

    Digisac installations may send either a plain hexadecimal digest or the
    conventional ``sha256=<digest>`` form.
    """
    if not settings.webhook_secret:
        return
    signature = request.headers.get("X-Digisac-Signature")
    if not signature:
        raise HTTPException(
            status_code=401, detail="Missing webhook signature")
    expected = hmac.new(
        settings.webhook_secret.encode(), await request.body(), hashlib.sha256
    ).hexdigest()
    supplied = signature.removeprefix("sha256=")
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401, detail="Invalid webhook signature")
