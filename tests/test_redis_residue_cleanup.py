import json
import hashlib
from typing import AsyncIterator

import pytest

from scripts.redis_residue_cleanup import (
    ACTIVE_QUEUE_KEYS,
    PRESERVED_KEY_PATTERNS,
    RedisSafetyError,
    collect_inventory,
    delete_orphaned_keys,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str | list[str]] = {
            "buffer:old": "legacy-buffer",
            "ticket_protocol:old": "legacy-protocol",
            "ia_processing": [json.dumps({"conversation_id": "old"})],
            "ia_queue": ["ia-job"],
            "ia_dead_letter": ["ia-dead-letter"],
            "audio_transcription_queue": [],
            "audio_transcription_dead_letter": [],
            "image_extraction_queue": ["image-job"],
            "image_extraction_dead_letter": ["image-dead-letter"],
            "processed:event": "1",
            "ia_status:ticket": "completed",
            "ia_result:ticket": "{}",
        }

    async def scan_iter(self, *, match: str, count: int) -> AsyncIterator[str]:
        del count
        from fnmatch import fnmatch

        for key in sorted(self.values):
            if fnmatch(key, match):
                yield key

    async def type(self, key: str) -> str:
        value = self.values[key]
        return "list" if isinstance(value, list) else "string"

    async def ttl(self, key: str) -> int:
        del key
        return -1

    async def llen(self, key: str) -> int:
        value = self.values.get(key)
        return len(value) if isinstance(value, list) else 0

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        value = self.values[key]
        assert isinstance(value, list)
        selected = value[start:] if end == -1 else value[start : end + 1]
        return [str(item) for item in selected]

    async def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)


@pytest.mark.asyncio
async def test_inventory_classifies_orphans_and_preserves_active_contracts():
    inventory = await collect_inventory(FakeRedis())

    assert inventory.families["buffer:*"].classification == "orphaned"
    assert inventory.families["buffer:*"].deletable is True
    assert inventory.families["ticket_protocol:*"].deletable is True
    assert inventory.processing.classification == "inconclusive"
    assert inventory.processing.deletable is False
    assert inventory.processing.item_count == 1

    for queue in ACTIVE_QUEUE_KEYS:
        assert inventory.protected[queue].classification == "active"
        assert inventory.protected[queue].deletable is False
    for pattern in PRESERVED_KEY_PATTERNS:
        assert inventory.protected[pattern].deletable is False


@pytest.mark.asyncio
async def test_deletion_is_allowlisted_repeatable_and_does_not_touch_active_keys():
    redis = FakeRedis()
    inventory = await collect_inventory(redis)

    first = await delete_orphaned_keys(redis, inventory)
    second = await delete_orphaned_keys(redis, await collect_inventory(redis))

    assert first == 2
    assert second == 0
    assert "buffer:old" not in redis.values
    assert "ticket_protocol:old" not in redis.values
    assert "ia_processing" in redis.values
    assert all(queue in redis.values for queue in ACTIVE_QUEUE_KEYS)
    assert "processed:event" in redis.values
    assert "ia_status:ticket" in redis.values
    assert "ia_result:ticket" in redis.values


@pytest.mark.asyncio
async def test_inventory_has_a_bounded_scan_limit():
    redis = FakeRedis()
    redis.values["buffer:second"] = "legacy-buffer"

    with pytest.raises(RedisSafetyError, match="scan limit"):
        await collect_inventory(redis, max_keys_per_pattern=1)


@pytest.mark.asyncio
async def test_reviewed_plan_rejects_a_new_orphan_key():
    redis = FakeRedis()
    redis.values["buffer:new"] = "legacy-buffer"
    changed_inventory = await collect_inventory(redis)
    reviewed = {"buffer:*": (hashlib.sha256("buffer:old".encode("utf-8")).hexdigest(),)}

    with pytest.raises(RedisSafetyError, match="new key appeared"):
        await delete_orphaned_keys(
            redis, changed_inventory, allowed_key_digests=reviewed
        )
