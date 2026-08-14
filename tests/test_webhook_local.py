"""Opt-in smoke check for a deliberately started local CAI API.

This module is discovered by pytest but performs no network I/O during import.
Run it directly when the local API is already running:

    PYTHONPATH=/app python tests/test_webhook_local.py
"""

from __future__ import annotations

import sys
import time
from typing import Any

import requests


WEBHOOK_URL = "http://localhost:8000/webhook/digisac"


def build_payload() -> dict[str, str]:
    """Build the same synthetic payload used by the original smoke check."""
    return {
        "event": "message.created",
        "conversation_id": "conv-123",
        "message_id": f"msg-{int(time.time())}",
        "content": "Olá, não consigo fazer login no sistema",
        "sender_id": "customer-456",
        "timestamp": "2026-07-17T14:30:00Z",
    }


def main() -> int:
    """Send one deliberate local smoke request and report its result."""
    try:
        response = requests.post(
            WEBHOOK_URL,
            json=build_payload(),
            headers={"Content-Type": "application/json"},
        )
    except requests.RequestException as exc:
        print(f"Smoke check failed: {exc}", file=sys.stderr)
        return 1

    print(f"Status: {response.status_code}")
    try:
        response_body: Any = response.json()
    except ValueError:
        response_body = response.text
    print(f"Resposta: {response_body}")

    if not 200 <= response.status_code < 300:
        print(
            f"Smoke check failed: HTTP {response.status_code}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
