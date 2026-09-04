import json
from collections.abc import AsyncIterator

import pytest

from scripts.migrate_legacy_webhook_idempotency import (
    inventory_legacy_webhook_markers,
)


class FakeRedis:
    def __init__(self) -> None:
        self.ttls = {
            f"processed:{'a' * 64}": 300,
            "processed:invalid": 300,
            f"processed:{'b' * 64}": -1,
            f"processed:{'c' * 64}": 0,
        }

    def scan_iter(self, *, match: str, count: int) -> AsyncIterator[object]:
        del match, count

        async def values() -> AsyncIterator[object]:
            for key in sorted(self.ttls):
                yield key

        return values()

    async def ttl(self, key: str) -> int:
        return self.ttls[key]


@pytest.mark.asyncio
async def test_inventory_records_only_live_valid_markers_without_raw_keys():
    report = (await inventory_legacy_webhook_markers(FakeRedis())).report()

    assert report["scanned_keys"] == 4
    assert report["valid_marker_count"] == 1
    assert report["invalid_digest_keys"] == 1
    assert report["expired_or_missing_keys"] == 1
    assert report["no_expiry_keys"] == 1
    assert report["truncated"] is False
    assert report["markers"] == [{"event_digest": "a" * 64, "ttl_seconds": 300}]
    rendered = json.dumps(report)
    assert f"processed:{'a' * 64}" not in rendered


@pytest.mark.asyncio
async def test_inventory_marks_a_scan_over_bound_as_truncated():
    report = await inventory_legacy_webhook_markers(FakeRedis(), max_items=2)

    assert report.truncated is True
    assert report.scanned_keys == 3
