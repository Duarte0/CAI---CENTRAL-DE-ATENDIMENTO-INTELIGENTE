import pytest
from fastapi import Response

from src.api import routes
from src.core import webhook_event_repository


class Redis:
    def __init__(self):
        self.values = {}
        self.queues = {}

    async def set(self, key, value, **_kwargs):
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
        return len(keys)

    async def rpush(self, key, value):
        self.queues.setdefault(key, []).append(value)
        return len(self.queues[key])


async def send(monkeypatch, redis, payload):
    async def parse(_request):
        return payload, None

    monkeypatch.setattr(routes, "parse_webhook_payload", parse)
    monkeypatch.setattr(
        webhook_event_repository,
        "try_mark_webhook_event",
        lambda *_args: _async(True),
    )
    return await routes.digisac_webhook(request=None, response=Response(), redis=redis)


@pytest.mark.asyncio
async def test_text_webhook_does_not_publish_an_ia_cycle(monkeypatch):
    redis = Redis()
    monkeypatch.setattr(routes, "reserve_transcription", lambda *_args: None)
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
    assert result["status"] == "received"
    assert redis.values == {}
    assert redis.queues == {}


@pytest.mark.asyncio
async def test_close_persists_cycle_without_ia_queue_publication(monkeypatch):
    redis = Redis()
    monkeypatch.setattr(
        routes, "capture_ticket_assignment", lambda *_args: _async(False)
    )
    cycle = {
        "public_id": "cycle-public",
        "conversation_id": "ticket",
        "protocol": "123",
        "status": "pending",
        "attempt_count": 0,
        "created_at": "2026-07-28T12:00:00+00:00",
        "next_attempt_at": None,
    }
    calls = []

    async def close(**kwargs):
        calls.append(kwargs)
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
    assert calls and calls[0]["conversation_id"] == "ticket"
    assert result["cycle_id"] == "cycle-public"
    assert redis.queues == {}


@pytest.mark.asyncio
async def test_reopen_creates_cycle_without_queue(monkeypatch):
    redis = Redis()
    monkeypatch.setattr(
        routes, "capture_ticket_assignment", lambda *_args: _async(False)
    )

    async def opened(**_kwargs):
        return {
            "public_id": "cycle-two",
            "conversation_id": "ticket",
        }, True

    monkeypatch.setattr(routes, "create_open_cycle", opened)
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
    assert result["status"] == "ticket_reopened"
    assert result["cycle_id"] == "cycle-two"
    assert redis.queues == {}


async def _async(value):
    return value
