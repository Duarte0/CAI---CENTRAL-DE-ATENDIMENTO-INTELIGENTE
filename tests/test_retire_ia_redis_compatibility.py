import json
import inspect
from typing import AsyncIterator

import pytest

from src.api import routes
from scripts import retire_ia_redis_compatibility as retirement


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {
            "ia_result:ticket-1": json.dumps(
                {
                    "conversation_id": "ticket-1",
                    "processed_at": "2026-09-03T00:00:00+00:00",
                    "title": "private result that must not reach the report",
                }
            ),
            "ia_result:ticket-2": "not-json",
            "ia_status:ticket-1": json.dumps(
                {"conversation_id": "ticket-1", "status": "completed"}
            ),
        }
        self.ttls = {key: 86_400 for key in self.values}

    async def scan_iter(self, *, match: str, count: int) -> AsyncIterator[str]:
        del count
        from fnmatch import fnmatch

        for key in sorted(self.values):
            if fnmatch(key, match):
                yield key

    async def type(self, key: str) -> str:
        assert key in self.values
        return "string"

    async def ttl(self, key: str) -> int:
        return self.ttls[key]

    async def get(self, name: str) -> str | None:
        return self.values.get(name)

    async def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)


def test_public_status_and_result_routes_do_not_read_redis():
    for handler in (
        routes.conversation_status,
        routes.conversation_result,
        routes.cycle_status,
        routes.cycle_result,
    ):
        source = inspect.getsource(handler)
        assert "redis" not in source.lower()
    assert "get_latest_cycle" in inspect.getsource(routes.conversation_status)
    assert "get_cycle_result" in inspect.getsource(routes.conversation_result)


@pytest.mark.asyncio
async def test_inventory_matches_durable_results_without_exposing_values(monkeypatch):
    async def classification_exists(conversation_id: str, created_at: str) -> bool:
        assert conversation_id == "ticket-1"
        assert created_at == "2026-09-03T00:00:00+00:00"
        return True

    monkeypatch.setattr(retirement, "classification_exists", classification_exists)
    inventory = await retirement.collect_inventory(FakeRedis())
    report = json.dumps(inventory.report(), ensure_ascii=False)

    result = inventory.families["ia_result:*"]
    assert result.payload_dispositions == {
        "durable_match": 1,
        "invalid_json": 1,
    }
    assert result.ttl_buckets == {"over_24h": 2}
    assert inventory.families["ia_status:*"].payload_dispositions == {
        "valid_object": 1,
    }
    assert inventory.missing_durable_matches == 0
    assert "ticket-1" not in report
    assert "private result" not in report
    assert '"report_contains_values": false' in report


def test_apply_requires_full_observation_window():
    with pytest.raises(
        retirement.CompatibilitySafetyError,
        match="observation window has not elapsed",
    ):
        retirement._require_observation(
            {
                "observation": {
                    "started_at": "2026-09-03T00:00:00+00:00",
                    "required_seconds": 86_400,
                }
            },
            "2026-09-03T23:59:59+00:00",
        )
