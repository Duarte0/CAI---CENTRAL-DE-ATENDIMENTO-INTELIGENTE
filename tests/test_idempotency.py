import pytest

from src.utils.idempotency import IdempotencyService


class MemoryRedis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def setex(self, key, ttl, value):
        self.values[key] = value

    async def exists(self, key):
        return int(key in self.values)


@pytest.mark.asyncio
async def test_try_mark_processed_is_atomic():
    service = IdempotencyService(MemoryRedis())

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
