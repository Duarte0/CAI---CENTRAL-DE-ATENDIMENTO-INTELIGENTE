import json

import pytest

from scripts.retire_legacy_ia_queue import (
    inventory_legacy_ia_queue,
    retire_validated_legacy_ia_queue_entries,
)


class FakeRedis:
    def __init__(self, values: list[str]) -> None:
        self.values = list(values)

    async def llen(self, _key: str) -> int:
        return len(self.values)

    async def lrange(self, _key: str, start: int, end: int) -> list[str]:
        return self.values[start : end + 1]

    async def lrem(self, _key: str, count: int, value: str) -> int:
        removed = 0
        retained: list[str] = []
        for candidate in self.values:
            if candidate == value and removed < count:
                removed += 1
                continue
            retained.append(candidate)
        self.values = retained
        return removed


@pytest.mark.asyncio
async def test_inventory_reports_duplicates_and_retains_unknown_or_malformed():
    valid = json.dumps({"cycle_id": "known", "conversation_id": "ticket"})
    unknown = json.dumps({"cycle_id": "unknown"})
    redis = FakeRedis([valid, valid, unknown, "not-json"])

    async def lookup(ids: list[str]):
        assert ids == ["known", "unknown"]
        return {"known": {"status": "pending", "next_attempt_at": None}}

    inventory = await inventory_legacy_ia_queue(
        redis, cycle_lookup=lookup, max_items=10
    )

    report = inventory.report()
    assert report["physical_entries"] == 4
    assert report["unique_cycle_ids"] == 2
    assert report["duplicate_entries"] == 1
    assert report["malformed_entries"] == 1
    assert report["unknown_cycle_entries"] == 1
    assert report["validated_entries_eligible_for_retirement"] == 2

    assert await retire_validated_legacy_ia_queue_entries(redis, inventory) == 2
    assert redis.values == [unknown, "not-json"]


@pytest.mark.asyncio
async def test_apply_refuses_a_truncated_inventory():
    redis = FakeRedis(
        [json.dumps({"cycle_id": "known"}) for _ in range(3)]
    )

    async def lookup(_ids: list[str]):
        return {"known": {"status": "pending"}}

    inventory = await inventory_legacy_ia_queue(
        redis, cycle_lookup=lookup, max_items=2
    )

    assert inventory.truncated is True
    with pytest.raises(RuntimeError, match="truncated"):
        await retire_validated_legacy_ia_queue_entries(redis, inventory)
    assert len(redis.values) == 3
