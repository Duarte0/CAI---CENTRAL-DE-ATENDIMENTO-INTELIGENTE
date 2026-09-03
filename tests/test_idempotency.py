from pathlib import Path

import pytest

from src.core import webhook_event_repository
from src.utils.idempotency import IdempotencyService


@pytest.mark.asyncio
async def test_try_mark_processed_delegates_to_postgresql_boundary(monkeypatch):
    decisions = iter([True, False])

    async def fake_mark(_event_id):
        return next(decisions)

    monkeypatch.setattr(webhook_event_repository, "try_mark_webhook_event", fake_mark)
    service = IdempotencyService()

    assert await service.try_mark_processed("event-1") is True
    assert await service.try_mark_processed("event-1") is False


def test_event_id_uses_message_id_and_content():
    first = {
        "event": "message.created",
        "conversation_id": "c",
        "message_id": "1",
        "content": "A",
    }
    second = {**first, "message_id": "2"}
    assert IdempotencyService.generate_event_id(
        first
    ) != IdempotencyService.generate_event_id(second)


def test_active_idempotency_service_has_no_redis_dependency():
    source = Path("src/utils/idempotency.py").read_text(encoding="utf-8")

    assert "redis" not in source.lower()
