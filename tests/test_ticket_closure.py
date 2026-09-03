import pytest
from fastapi import Response

from src.api import routes
from src.core import webhook_event_repository


class PersistentRedis:
    """Minimal transport double for the persistent webhook contract."""

    def __init__(self):
        self.values = {}
        self.queues = {}

    async def set(self, key, value, **_kwargs):
        self.values[key] = value
        return True

    async def rpush(self, key, value):
        self.queues.setdefault(key, []).append(value)
        return len(self.queues[key])


async def send(monkeypatch, redis, payload):
    async def fake_parse(_request):
        return payload, None

    monkeypatch.setattr(routes, "parse_webhook_payload", fake_parse)
    monkeypatch.setattr(
        webhook_event_repository,
        "try_mark_webhook_event",
        lambda *_args: _completed(True),
    )
    monkeypatch.setattr(
        routes, "capture_ticket_assignment", lambda *_args: _completed(False)
    )
    return await routes.digisac_webhook(
        request=None, response=Response(), redis=redis
    )


@pytest.mark.asyncio
async def test_ticket_created_always_establishes_persistent_cycle(monkeypatch):
    redis = PersistentRedis()
    cycle = {"public_id": "cycle-open", "conversation_id": "ticket"}
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        return cycle, True

    monkeypatch.setattr(routes, "create_open_cycle", create)

    result = await send(
        monkeypatch,
        redis,
        {
            "event": "ticket.created",
            "data": {"id": "ticket", "createdAt": "2026-07-28T12:00:00Z"},
        },
    )

    assert calls and calls[0]["conversation_id"] == "ticket"
    assert result == {
        "status": "ticket_created",
        "conversation_id": "ticket",
        "cycle_id": "cycle-open",
        "cycle_created": True,
    }
    assert redis.queues == {}


@pytest.mark.asyncio
async def test_message_webhook_does_not_publish_an_ia_cycle(monkeypatch):
    redis = PersistentRedis()
    payload = {
        "event": "message.created",
        "data": {
            "id": "message",
            "ticketId": "ticket",
            "type": "chat",
            "text": "Olá",
            "isFromMe": False,
            "isFromBot": False,
            "timestamp": "2026-07-28T12:00:00Z",
        },
    }

    result = await send(monkeypatch, redis, payload)

    assert result == {
        "status": "received",
        "conversation_id": "ticket",
        "transcription_queued": False,
        "image_extraction_queued": False,
    }
    assert redis.values == {}
    assert redis.queues == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bot_fields",
    [
        {"isFromBot": True, "origin": "bot"},
        {"origin": "bot"},
    ],
)
async def test_bot_message_is_ignored_before_persistent_work(monkeypatch, bot_fields):
    redis = PersistentRedis()
    result = await send(
        monkeypatch,
        redis,
        {
            "event": "message.created",
            "data": {
                "id": "bot-message",
                "ticketId": "ticket",
                "type": "chat",
                "text": "Mensagem automática",
                "isFromMe": True,
                **bot_fields,
            },
        },
    )

    assert result["status"] == "ignored"
    assert result["reason"] in {"is_from_bot", "bot_origin_fallback"}
    assert redis.values == {}
    assert redis.queues == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (
            {"event": "ticket.updated", "data": {"id": "ticket", "isOpen": False}},
            "missing_protocol",
        ),
        (
            {
                "event": "ticket.updated",
                "data": {"isOpen": False, "protocol": "123"},
            },
            "missing_ticket_id",
        ),
    ],
)
async def test_invalid_closed_ticket_is_ignored(monkeypatch, payload, reason):
    redis = PersistentRedis()

    result = await send(monkeypatch, redis, payload)

    assert result["status"] == "ignored"
    assert result["reason"] == reason
    assert redis.queues == {}


@pytest.mark.asyncio
async def test_close_persists_cycle_without_publishing_ia_queue(monkeypatch):
    redis = PersistentRedis()
    cycle = {
        "public_id": "cycle-public",
        "conversation_id": "ticket",
        "protocol": "123",
        "status": "pending",
        "attempt_count": 0,
        "created_at": "2026-07-28T12:00:00+00:00",
        "next_attempt_at": None,
    }
    close_calls = []

    async def close(**kwargs):
        close_calls.append(kwargs)
        return cycle, True

    monkeypatch.setattr(routes, "close_cycle", close)

    result = await send(
        monkeypatch,
        redis,
        {
            "event": "ticket.updated",
            "data": {
                "id": "ticket",
                "isOpen": False,
                "protocol": "123",
                "updatedAt": "2026-07-28T12:00:00Z",
            },
        },
    )

    assert close_calls and close_calls[0]["conversation_id"] == "ticket"
    assert result == {
        "status": "ticket_closed",
        "conversation_id": "ticket",
        "cycle_id": "cycle-public",
        "queued": True,
    }
    assert redis.queues == {}


@pytest.mark.asyncio
async def test_duplicate_close_reuses_cycle_without_ia_publication(monkeypatch):
    redis = PersistentRedis()
    cycle = {
        "public_id": "cycle-public",
        "conversation_id": "ticket",
        "protocol": "123",
        "status": "pending",
        "attempt_count": 0,
        "created_at": "2026-07-28T12:00:00+00:00",
        "next_attempt_at": None,
    }
    close_calls = 0

    async def close(**_kwargs):
        nonlocal close_calls
        close_calls += 1
        return cycle, close_calls == 1

    monkeypatch.setattr(routes, "close_cycle", close)
    payload = {
        "event": "ticket.updated",
        "data": {
            "id": "ticket",
            "isOpen": False,
            "protocol": "123",
            "updatedAt": "2026-07-28T12:00:00Z",
        },
    }

    first = await send(monkeypatch, redis, payload)
    duplicate = await send(monkeypatch, redis, payload)

    assert first["cycle_id"] == duplicate["cycle_id"] == "cycle-public"
    assert first["status"] == "ticket_closed"
    assert duplicate["status"] == "ticket_already_closed"
    assert close_calls == 2
    assert redis.queues == {}


@pytest.mark.asyncio
async def test_reopen_creates_cycle_without_classification_job(monkeypatch):
    redis = PersistentRedis()
    cycle = {"public_id": "cycle-two", "conversation_id": "ticket"}
    create_calls = []

    async def create(**kwargs):
        create_calls.append(kwargs)
        return cycle, True

    monkeypatch.setattr(routes, "create_open_cycle", create)

    result = await send(
        monkeypatch,
        redis,
        {
            "event": "ticket.updated",
            "data": {
                "id": "ticket",
                "isOpen": True,
                "updatedAt": "2026-07-28T13:00:00Z",
            },
        },
    )

    assert create_calls and create_calls[0]["conversation_id"] == "ticket"
    assert result == {
        "status": "ticket_reopened",
        "conversation_id": "ticket",
        "cycle_id": "cycle-two",
        "cycle_created": True,
        "queued": False,
    }
    assert redis.queues == {}


@pytest.mark.asyncio
async def test_persisted_cycle_does_not_require_ia_queue_publication(monkeypatch):
    redis = PersistentRedis()
    cycle = {
        "public_id": "cycle-recoverable",
        "conversation_id": "ticket",
        "protocol": "123",
        "status": "pending",
        "attempt_count": 0,
        "created_at": "2026-07-28T12:00:00+00:00",
        "next_attempt_at": None,
    }
    persisted = []

    async def close(**_kwargs):
        persisted.append(cycle)
        return cycle, True

    monkeypatch.setattr(routes, "close_cycle", close)

    result = await send(
        monkeypatch,
        redis,
        {
            "event": "ticket.updated",
            "data": {"id": "ticket", "isOpen": False, "protocol": "123"},
        },
    )

    assert persisted == [cycle]
    assert result["status"] == "ticket_closed"
    assert result["cycle_id"] == "cycle-recoverable"
    assert result["queued"] is True
    assert redis.queues == {}


async def _completed(value):
    return value
