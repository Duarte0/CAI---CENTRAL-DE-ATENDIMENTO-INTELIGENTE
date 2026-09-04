"""Inventory and, after a reviewed sunset, retire IA Redis compatibility views.

The application no longer owns these keys after issue 0054.  This command is
maintenance-only: dry-run is bounded and value-free, while apply requires the
reviewed report, an observation window, an explicit historical decision and a
second value fingerprint check.  It never changes PostgreSQL and it never
touches a Redis family outside ``ia_status:*`` and ``ia_result:*``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Mapping, Protocol, cast

from src.core.classification_repository import classification_exists
from src.core.db import (
    close_database,
    initialize_database,
)
from scripts.redis_maintenance_client import create_redis_client
from scripts.redis_residue_cleanup import collect_postgres_snapshot


COMPATIBILITY_PATTERNS: tuple[str, ...] = ("ia_status:*", "ia_result:*")
DEFAULT_MAX_KEYS = 1_000
DEFAULT_OBSERVATION_SECONDS = 86_400
APPLY_CONFIRMATION = "retire-ia-redis-compatibility"
REPORT_VERSION = 1
_SCAN_COUNT = 100


class CompatibilityRedisClient(Protocol):
    async def aclose(self) -> None: ...

    async def delete(self, key: str) -> int: ...

    async def get(self, name: str) -> str | None: ...

    def scan_iter(self, *, match: str, count: int) -> AsyncIterator[object]: ...

    async def ttl(self, key: str) -> int: ...

    async def type(self, key: str) -> str: ...


class CompatibilitySafetyError(RuntimeError):
    """Raised when the reviewed compatibility retirement is not safe."""


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _entry_digest(key: str, value: str | None) -> str:
    return _digest(f"{key}\x00{value or ''}")


def _ttl_bucket(ttl: int) -> str:
    if ttl == -2:
        return "missing_or_expired"
    if ttl == -1:
        return "no_expiry"
    if ttl < 3_600:
        return "under_1h"
    if ttl < 86_400:
        return "1h_to_24h"
    return "over_24h"


def _parse_object(raw_value: str | None) -> tuple[dict[str, Any] | None, str]:
    if raw_value is None:
        return None, "missing_or_expired"
    try:
        parsed: Any = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return None, "invalid_json"
    if not isinstance(parsed, dict):
        return None, "non_object"
    return cast(dict[str, Any], parsed), "valid_object"


def _legacy_created_at(result: Mapping[str, Any]) -> str | None:
    value = result.get("processed_at")
    return value if isinstance(value, str) and value.strip() else None


async def _scan_keys(
    redis: CompatibilityRedisClient, pattern: str, max_keys: int
) -> tuple[str, ...]:
    keys: list[str] = []
    async for raw_key in redis.scan_iter(match=pattern, count=_SCAN_COUNT):
        if not isinstance(raw_key, str):
            raise CompatibilitySafetyError(f"Redis returned a non-text key for {pattern}")
        if len(keys) >= max_keys:
            raise CompatibilitySafetyError(
                f"scan limit exceeded for {pattern}; increase --max-keys only after review"
            )
        keys.append(raw_key)
    return tuple(sorted(set(keys)))


@dataclass(frozen=True)
class CompatibilityFamilyInventory:
    pattern: str
    keys: tuple[str, ...] = field(repr=False)
    entry_digests: Mapping[str, str] = field(repr=False)
    redis_types: Mapping[str, int]
    ttl_buckets: Mapping[str, int]
    payload_dispositions: Mapping[str, int]
    durable_matches: int = 0

    def report(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "count": len(self.keys),
            "redis_types": dict(sorted(self.redis_types.items())),
            "ttl_buckets": dict(sorted(self.ttl_buckets.items())),
            "payload_dispositions": dict(sorted(self.payload_dispositions.items())),
            "durable_matches": self.durable_matches,
            "key_digests": sorted(_digest(key) for key in self.keys),
            "entry_digests": sorted(self.entry_digests.values()),
        }


@dataclass(frozen=True)
class CompatibilityInventory:
    families: Mapping[str, CompatibilityFamilyInventory]

    @property
    def count(self) -> int:
        return sum(len(family.keys) for family in self.families.values())

    @property
    def missing_durable_matches(self) -> int:
        return sum(
            family.payload_dispositions.get("missing_durable_match", 0)
            for family in self.families.values()
        )

    def report(self) -> dict[str, Any]:
        return {
            "families": {
                pattern: family.report()
                for pattern, family in self.families.items()
            },
            "total_keys": self.count,
            "missing_durable_matches": self.missing_durable_matches,
            "report_contains_values": False,
        }


async def _family_inventory(
    redis: CompatibilityRedisClient,
    pattern: str,
    max_keys: int,
) -> CompatibilityFamilyInventory:
    keys = await _scan_keys(redis, pattern, max_keys)
    redis_types: Counter[str] = Counter()
    ttl_buckets: Counter[str] = Counter()
    dispositions: Counter[str] = Counter()
    entry_digests: dict[str, str] = {}
    durable_matches = 0

    for key in keys:
        redis_types[await redis.type(key)] += 1
        ttl = await redis.ttl(key)
        ttl_buckets[_ttl_bucket(ttl)] += 1
        raw_value = await redis.get(key)
        entry_digests[key] = _entry_digest(key, raw_value)
        parsed, disposition = _parse_object(raw_value)
        if pattern == "ia_result:*":
            if parsed is None:
                dispositions[disposition] += 1
                continue
            conversation_id = key.removeprefix("ia_result:")
            created_at = _legacy_created_at(parsed)
            if not conversation_id or created_at is None:
                dispositions["missing_identity_or_timestamp"] += 1
                continue
            if await classification_exists(conversation_id, created_at):
                dispositions["durable_match"] += 1
                durable_matches += 1
            else:
                dispositions["missing_durable_match"] += 1
        else:
            dispositions[disposition] += 1

    return CompatibilityFamilyInventory(
        pattern=pattern,
        keys=keys,
        entry_digests=entry_digests,
        redis_types=redis_types,
        ttl_buckets=ttl_buckets,
        payload_dispositions=dispositions,
        durable_matches=durable_matches,
    )


async def collect_inventory(
    redis: CompatibilityRedisClient,
    *,
    max_keys_per_pattern: int = DEFAULT_MAX_KEYS,
) -> CompatibilityInventory:
    """Collect bounded metadata and durable-match classifications only."""
    if max_keys_per_pattern < 1:
        raise ValueError("max_keys_per_pattern must be positive")
    families = {
        pattern: await _family_inventory(redis, pattern, max_keys_per_pattern)
        for pattern in COMPATIBILITY_PATTERNS
    }
    return CompatibilityInventory(families)


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _read_report(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompatibilitySafetyError(f"cannot read dry-run report: {path}") from exc
    if not isinstance(value, dict) or value.get("report_version") != REPORT_VERSION:
        raise CompatibilitySafetyError("report is not a supported compatibility report")
    return cast(dict[str, Any], value)


def _reviewed_entries(report: Mapping[str, Any]) -> dict[str, set[str]]:
    raw_inventory = report.get("redis")
    if not isinstance(raw_inventory, Mapping):
        raise CompatibilitySafetyError("dry-run report has no Redis inventory")
    raw_families = raw_inventory.get("families")
    if not isinstance(raw_families, Mapping):
        raise CompatibilitySafetyError("dry-run report has no family inventory")
    reviewed: dict[str, set[str]] = {}
    for pattern in COMPATIBILITY_PATTERNS:
        raw_family = raw_families.get(pattern)
        if not isinstance(raw_family, Mapping):
            raise CompatibilitySafetyError(f"dry-run report is missing {pattern}")
        raw_digests = raw_family.get("entry_digests")
        if not isinstance(raw_digests, list) or not all(
            isinstance(value, str) for value in raw_digests
        ):
            raise CompatibilitySafetyError(f"dry-run report has invalid entries for {pattern}")
        reviewed[pattern] = set(cast(list[str], raw_digests))
    return reviewed


def _require_observation(report: Mapping[str, Any], completed_at: str) -> None:
    raw_observation = report.get("observation")
    if not isinstance(raw_observation, Mapping):
        raise CompatibilitySafetyError("dry-run report has no observation window")
    started_raw = raw_observation.get("started_at")
    required_raw = raw_observation.get("required_seconds")
    if not isinstance(started_raw, str) or not isinstance(required_raw, int):
        raise CompatibilitySafetyError("dry-run report has invalid observation metadata")
    try:
        started = datetime.fromisoformat(started_raw)
        completed = datetime.fromisoformat(completed_at)
    except ValueError as exc:
        raise CompatibilitySafetyError("observation timestamps must be ISO-8601") from exc
    if started.tzinfo is None or completed.tzinfo is None:
        raise CompatibilitySafetyError("observation timestamps must include a timezone")
    if completed < started + timedelta(seconds=required_raw):
        raise CompatibilitySafetyError("the complete compatibility TTL observation window has not elapsed")


async def _run_dry_run(
    report_path: Path,
    max_keys: int,
    observation_started_at: str | None,
    required_observation_seconds: int,
) -> None:
    redis_client = create_redis_client()
    redis = cast(CompatibilityRedisClient, redis_client)
    try:
        await initialize_database()
        try:
            inventory = await collect_inventory(
                redis, max_keys_per_pattern=max_keys
            )
        finally:
            await close_database()
        postgres = await collect_postgres_snapshot()
        started_at = observation_started_at or datetime.now(timezone.utc).isoformat()
        report = {
            "report_version": REPORT_VERSION,
            "mode": "dry-run",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "observation": {
                "started_at": started_at,
                "required_seconds": required_observation_seconds,
            },
            "redis": inventory.report(),
            "postgres": postgres.report(),
            "historical_disposition": (
                "all valid ia_result payloads have a durable PostgreSQL match"
                if inventory.missing_durable_matches == 0
                else "review missing_durable_match entries before any deletion"
            ),
            "decision": (
                "No application producer remains; retain keys until the complete "
                "observation window and explicit apply review."
            ),
        }
        _write_report(report_path, report)
        print(json.dumps(report, indent=2, ensure_ascii=False))
    finally:
        await redis_client.aclose()


async def _run_apply(
    report_path: Path,
    max_keys: int,
    observation_completed_at: str,
    historical_decision: str,
) -> None:
    report = _read_report(report_path)
    if report.get("mode") != "dry-run":
        raise CompatibilitySafetyError("--apply requires a dry-run report")
    _require_observation(report, observation_completed_at)
    if historical_decision != "all-valid-results-durable":
        raise CompatibilitySafetyError(
            "--apply requires --historical-decision all-valid-results-durable"
        )
    raw_redis = report.get("redis")
    if not isinstance(raw_redis, Mapping) or raw_redis.get("missing_durable_matches") != 0:
        raise CompatibilitySafetyError(
            "valid legacy results without a durable match block compatibility retirement"
        )
    reviewed = _reviewed_entries(report)
    redis_client = create_redis_client()
    redis = cast(CompatibilityRedisClient, redis_client)
    try:
        await initialize_database()
        try:
            before = await collect_inventory(redis, max_keys_per_pattern=max_keys)
        finally:
            await close_database()
        postgres_before = await collect_postgres_snapshot()
        for pattern in COMPATIBILITY_PATTERNS:
            family = before.families[pattern]
            current_entries = set(family.entry_digests.values())
            if not current_entries.issubset(reviewed[pattern]):
                raise CompatibilitySafetyError(
                    f"{pattern} changed since dry-run; create and review a new report"
                )
        deleted = 0
        for pattern in COMPATIBILITY_PATTERNS:
            for key in before.families[pattern].keys:
                deleted += await redis.delete(key)
        await initialize_database()
        try:
            after = await collect_inventory(redis, max_keys_per_pattern=max_keys)
        finally:
            await close_database()
        postgres_after = await collect_postgres_snapshot()
        remaining = after.count
        if remaining:
            raise CompatibilitySafetyError(
                f"compatibility keys remain after apply: {remaining}"
            )
        result = {
            "report_version": REPORT_VERSION,
            "mode": "apply-result",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "deleted": deleted,
            "before": before.report(),
            "after": after.report(),
            "postgres_before": postgres_before.report(),
            "postgres_after": postgres_after.report(),
            "decision": (
                "Only ia_status:* and ia_result:* were removed; PostgreSQL, "
                "processed:*, queues and ia_processing were not touched."
            ),
        }
        _write_report(report_path, {**report, "apply": result})
        print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        await redis_client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--max-keys", type=int, default=DEFAULT_MAX_KEYS)
    parser.add_argument("--observation-started-at")
    parser.add_argument(
        "--observation-required-seconds",
        type=int,
        default=DEFAULT_OBSERVATION_SECONDS,
    )
    parser.add_argument("--observation-completed-at")
    parser.add_argument(
        "--historical-decision",
        choices=("all-valid-results-durable", "retain-unmatched"),
    )
    args = parser.parse_args()
    if args.max_keys < 1:
        parser.error("--max-keys must be positive")
    if args.observation_required_seconds < 0:
        parser.error("--observation-required-seconds must not be negative")
    if args.apply and not args.confirm:
        parser.error("--apply requires --confirm after reviewing --dry-run output")
    if args.apply and not args.observation_completed_at:
        parser.error("--apply requires --observation-completed-at")
    if args.apply and not args.historical_decision:
        parser.error("--apply requires --historical-decision")
    if args.dry_run:
        asyncio.run(
            _run_dry_run(
                args.report,
                args.max_keys,
                args.observation_started_at,
                args.observation_required_seconds,
            )
        )
    else:
        asyncio.run(
            _run_apply(
                args.report,
                args.max_keys,
                args.observation_completed_at,
                args.historical_decision,
            )
        )


if __name__ == "__main__":
    main()
