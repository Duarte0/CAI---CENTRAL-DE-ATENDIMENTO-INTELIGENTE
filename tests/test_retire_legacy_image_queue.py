import json
from datetime import datetime, timezone

import pytest

from scripts import retire_legacy_image_queue as cutover


class Redis:
    def __init__(self, queues):
        self.queues = {key: list(values) for key, values in queues.items()}
        self.removed = []

    async def llen(self, key):
        return len(self.queues.get(key, []))

    async def lrange(self, key, start, end):
        values = list(self.queues.get(key, []))
        return values[start : end + 1 if end >= 0 else None]

    async def lrem(self, key, count, value):
        removed = 0
        remaining = []
        for item in self.queues.get(key, []):
            if item == value and (count == 0 or removed < count):
                removed += 1
            else:
                remaining.append(item)
        self.queues[key] = remaining
        self.removed.extend((key, value) for _ in range(removed))
        return removed


def raw(message_id):
    return json.dumps({"message_id": message_id})


@pytest.mark.asyncio
async def test_inventory_is_bounded_and_groups_duplicate_image_entries():
    redis = Redis(
        {
            cutover.LEGACY_IMAGE_QUEUE: [raw("image-1"), raw("image-1")],
            cutover.LEGACY_IMAGE_DEAD_LETTER: ["malformed", raw("unknown")],
        }
    )

    async def lookup(message_ids):
        assert message_ids == ["image-1", "unknown"]
        return {
            "image-1": {
                "status": "pending",
                "attempt_count": 1,
                "next_attempt_at": None,
            }
        }

    inventory = await cutover.inventory_legacy_image_lists(
        redis, image_lookup=lookup
    )

    assert inventory.physical_entries == 4
    assert inventory.unique_message_ids == 2
    assert inventory.duplicate_entries == 1
    assert inventory.malformed_counts[cutover.LEGACY_IMAGE_DEAD_LETTER] == 1
    assert inventory.unknown_counts[cutover.LEGACY_IMAGE_DEAD_LETTER] == 1
    assert inventory.report()["decision"].startswith("apply may")


@pytest.mark.asyncio
async def test_apply_removes_only_validated_safe_entries_and_is_repeatable():
    redis = Redis(
        {
            cutover.LEGACY_IMAGE_QUEUE: [raw("pending"), raw("unknown")],
            cutover.LEGACY_IMAGE_DEAD_LETTER: [
                raw("completed"),
                raw("transient"),
                "malformed",
            ],
        }
    )
    rows = {
        "pending": {"status": "pending"},
        "completed": {"status": "completed"},
        "transient": {
            "status": "failed",
            "error_message": "Groq vision request failed: HTTP 429",
        },
    }

    async def lookup(message_ids):
        return {
            message_id: rows[message_id]
            for message_id in message_ids
            if message_id in rows
        }

    inventory = await cutover.inventory_legacy_image_lists(
        redis, image_lookup=lookup
    )
    assert await cutover.retire_validated_legacy_image_entries(redis, inventory) == 2
    assert redis.queues[cutover.LEGACY_IMAGE_QUEUE] == [raw("unknown")]
    assert redis.queues[cutover.LEGACY_IMAGE_DEAD_LETTER] == [
        raw("transient"),
        "malformed",
    ]

    repeated = await cutover.inventory_legacy_image_lists(
        redis, image_lookup=lookup
    )
    assert await cutover.retire_validated_legacy_image_entries(redis, repeated) == 0


@pytest.mark.asyncio
async def test_apply_refuses_incomplete_inventory():
    redis = Redis(
        {cutover.LEGACY_IMAGE_QUEUE: [raw("image-1"), raw("image-2")]}
    )

    async def lookup(_message_ids):
        return {"image-1": {"status": "pending"}}

    inventory = await cutover.inventory_legacy_image_lists(
        redis, image_lookup=lookup, max_items=1
    )
    with pytest.raises(RuntimeError, match="truncated"):
        await cutover.retire_validated_legacy_image_entries(redis, inventory)


@pytest.mark.asyncio
async def test_transient_dead_letter_import_is_durable_and_keeps_evidence(monkeypatch):
    redis = Redis(
        {
            cutover.LEGACY_IMAGE_QUEUE: [],
            cutover.LEGACY_IMAGE_DEAD_LETTER: [raw("image-429")],
        }
    )
    inventory = await cutover.inventory_legacy_image_lists(
        redis,
        image_lookup=lambda _ids: _transient_rows(),
    )
    transitions = []

    async def set_status(message_id, status, **kwargs):
        transitions.append((message_id, status, kwargs))
        return datetime.now(timezone.utc)

    monkeypatch.setattr(cutover, "set_image_extraction_status", set_status)
    assert await cutover.recover_legacy_transient_dead_letters(inventory) == 1
    assert transitions[0][0:2] == ("image-429", "pending")
    assert redis.queues[cutover.LEGACY_IMAGE_DEAD_LETTER] == [raw("image-429")]


async def _transient_rows():
    return {
        "image-429": {
            "status": "failed",
            "error_message": "Groq vision request failed: HTTP 429",
        }
    }
