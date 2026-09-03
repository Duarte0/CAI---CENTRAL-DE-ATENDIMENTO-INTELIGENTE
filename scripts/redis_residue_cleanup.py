"""Inventory and remove only reviewed, orphaned Redis key families.

The command is deliberately plan-based: a dry-run report records key digests,
not key names or values, and an apply run refuses keys that appeared after the
report. PostgreSQL is inspected before every apply and is never modified.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Mapping, Protocol, Sequence, cast

from src.core.db import close_database, get_database_pool, initialize_database
from src.core.redis_client import create_redis_client


class RedisMaintenanceClient(Protocol):
    async def delete(self, key: str) -> int:
        ...

    async def llen(self, key: str) -> int:
        ...

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        ...

    def scan_iter(self, *, match: str, count: int) -> AsyncIterator[object]:
        ...

    async def ttl(self, key: str) -> int:
        ...

    async def type(self, key: str) -> str:
        ...


class RedisSafetyError(RuntimeError):
    """Raised when an inventory cannot be safely bounded or applied."""


@dataclass(frozen=True)
class KeyFamilySpec:
    pattern: str
    owner: str
    classification: str


ORPHANED_KEY_SPECS: tuple[KeyFamilySpec, ...] = (
    KeyFamilySpec(
        "buffer:*",
        "removed Redis-buffer finalization path",
        "orphaned",
    ),
    KeyFamilySpec(
        "ticket_close_scheduled:*",
        "removed ticket debounce scheduler",
        "orphaned",
    ),
    KeyFamilySpec(
        "ticket_last_message_at:*",
        "removed ticket debounce state",
        "orphaned",
    ),
    KeyFamilySpec(
        "ticket_protocol:*",
        "removed ticket debounce state",
        "orphaned",
    ),
    KeyFamilySpec(
        "ticket_classify_after:*",
        "removed ticket debounce scheduler",
        "orphaned",
    ),
    KeyFamilySpec(
        "ticket_close_task:*",
        "removed ticket debounce scheduler",
        "orphaned",
    ),
)

ACTIVE_QUEUE_KEYS: tuple[str, ...] = (
    "ia_queue",
    "ia_dead_letter",
    "audio_transcription_queue",
    "audio_transcription_dead_letter",
    "image_extraction_queue",
    "image_extraction_dead_letter",
)

PRESERVED_KEY_PATTERNS: tuple[str, ...] = (
    "processed:*",
)

# These families have a separate sunset contract in issue 0054.  They remain
# visible in this general inventory so a queue cleanup cannot accidentally
# delete them, but retirement is owned by the dedicated compatibility command.
RETIRED_COMPATIBILITY_KEY_PATTERNS: tuple[str, ...] = (
    "ia_status:*",
    "ia_result:*",
)

_SCAN_COUNT = 100
_REPORT_VERSION = 1


@dataclass(frozen=True)
class KeyFamilyInventory:
    pattern: str
    owner: str
    classification: str
    deletable: bool
    keys: tuple[str, ...] = field(repr=False)
    redis_types: dict[str, int] = field(default_factory=lambda: dict[str, int]())
    ttl_counts: dict[int, int] = field(default_factory=lambda: dict[int, int]())

    @property
    def count(self) -> int:
        return len(self.keys)

    def report(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "owner": self.owner,
            "classification": self.classification,
            "deletable": self.deletable,
            "count": self.count,
            "redis_types": dict(sorted(self.redis_types.items())),
            "ttl_counts": {
                str(ttl): count for ttl, count in sorted(self.ttl_counts.items())
            },
            "key_digests": sorted(_key_digest(key) for key in self.keys),
        }


@dataclass(frozen=True)
class ProcessingInventory:
    redis_type: str
    ttl: int
    item_count: int
    item_digest: str | None
    classification: str = "inconclusive"
    deletable: bool = False

    def report(self) -> dict[str, Any]:
        return {
            "key": "ia_processing",
            "redis_type": self.redis_type,
            "ttl": self.ttl,
            "item_count": self.item_count,
            "item_digest": self.item_digest,
            "classification": self.classification,
            "deletable": self.deletable,
            "decision": "retain until recoverable work is disproven",
        }


@dataclass(frozen=True)
class RedisInventory:
    families: Mapping[str, KeyFamilyInventory]
    protected: Mapping[str, KeyFamilyInventory]
    retired_compatibility: Mapping[str, KeyFamilyInventory]
    processing: ProcessingInventory
    queue_lengths: Mapping[str, int]

    def report(self) -> dict[str, Any]:
        return {
            "families": {
                pattern: family.report() for pattern, family in self.families.items()
            },
            "protected": {
                pattern: family.report() for pattern, family in self.protected.items()
            },
            "retired_compatibility": {
                pattern: family.report()
                for pattern, family in self.retired_compatibility.items()
            },
            "ia_processing": self.processing.report(),
            "queue_lengths": dict(self.queue_lengths),
        }


@dataclass(frozen=True)
class PostgresSnapshot:
    cycle_statuses: Mapping[str, int]
    audio_statuses: Mapping[str, int]
    image_statuses: Mapping[str, int]
    counts: Mapping[str, int]

    def report(self) -> dict[str, Any]:
        return {
            "cycle_statuses": dict(sorted(self.cycle_statuses.items())),
            "audio_statuses": dict(sorted(self.audio_statuses.items())),
            "image_statuses": dict(sorted(self.image_statuses.items())),
            "counts": dict(sorted(self.counts.items())),
        }


def _key_digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


async def _scan_keys(
    redis: RedisMaintenanceClient, pattern: str, max_keys: int
) -> tuple[str, ...]:
    keys: list[str] = []
    async for raw_key in redis.scan_iter(match=pattern, count=_SCAN_COUNT):
        if not isinstance(raw_key, str):
            raise RedisSafetyError(f"Redis returned a non-text key for {pattern}")
        if len(keys) >= max_keys:
            raise RedisSafetyError(
                f"scan limit exceeded for {pattern}; rerun with an explicit "
                "reviewed bound"
            )
        keys.append(raw_key)
    return tuple(sorted(set(keys)))


async def _family_inventory(
    redis: RedisMaintenanceClient,
    spec: KeyFamilySpec,
    max_keys: int,
    *,
    keys: tuple[str, ...] | None = None,
) -> KeyFamilyInventory:
    found_keys = (
        keys if keys is not None else await _scan_keys(redis, spec.pattern, max_keys)
    )
    redis_types: Counter[str] = Counter()
    ttl_counts: Counter[int] = Counter()
    for key in found_keys:
        redis_types[await redis.type(key)] += 1
        ttl_counts[await redis.ttl(key)] += 1
    return KeyFamilyInventory(
        pattern=spec.pattern,
        owner=spec.owner,
        classification=(
            spec.classification
            if found_keys or spec.classification != "orphaned"
            else "absent"
        ),
        deletable=bool(found_keys) and spec.classification == "orphaned",
        keys=found_keys,
        redis_types=redis_types,
        ttl_counts=ttl_counts,
    )


async def _exact_inventory(
    redis: RedisMaintenanceClient, key: str, max_keys: int
) -> KeyFamilyInventory:
    spec = KeyFamilySpec(key, "current Redis contract", "active")
    return await _family_inventory(redis, spec, max_keys)


async def _inspect_processing(
    redis: RedisMaintenanceClient, max_keys: int
) -> ProcessingInventory:
    keys = await _scan_keys(redis, "ia_processing", max_keys)
    if not keys:
        return ProcessingInventory("none", -2, 0, None)
    redis_type = await redis.type("ia_processing")
    ttl = await redis.ttl("ia_processing")
    if redis_type != "list":
        return ProcessingInventory(redis_type, ttl, 0, None)
    items = await redis.lrange("ia_processing", 0, 0)
    digest = _key_digest(items[0]) if items else None
    return ProcessingInventory(
        redis_type, ttl, await redis.llen("ia_processing"), digest
    )


async def collect_inventory(
    redis: RedisMaintenanceClient, *, max_keys_per_pattern: int = 1000
) -> RedisInventory:
    """Collect bounded, value-free Redis inventory for the cleanup plan."""
    if max_keys_per_pattern < 1:
        raise ValueError("max_keys_per_pattern must be positive")
    families = {
        spec.pattern: await _family_inventory(redis, spec, max_keys_per_pattern)
        for spec in ORPHANED_KEY_SPECS
    }
    protected: dict[str, KeyFamilyInventory] = {}
    for key in ACTIVE_QUEUE_KEYS:
        protected[key] = await _exact_inventory(redis, key, max_keys_per_pattern)
    for pattern in PRESERVED_KEY_PATTERNS:
        protected[pattern] = await _family_inventory(
            redis,
            KeyFamilySpec(pattern, "current Redis contract", "active"),
            max_keys_per_pattern,
        )
    retired_compatibility: dict[str, KeyFamilyInventory] = {}
    for pattern in RETIRED_COMPATIBILITY_KEY_PATTERNS:
        retired_compatibility[pattern] = await _family_inventory(
            redis,
            KeyFamilySpec(
                pattern,
                "issue 0054 dedicated sunset procedure",
                "retired-compatibility",
            ),
            max_keys_per_pattern,
        )
    queue_lengths = {key: await redis.llen(key) for key in ACTIVE_QUEUE_KEYS}
    return RedisInventory(
        families=families,
        protected=protected,
        retired_compatibility=retired_compatibility,
        processing=await _inspect_processing(redis, max_keys_per_pattern),
        queue_lengths=queue_lengths,
    )


async def delete_orphaned_keys(
    redis: RedisMaintenanceClient,
    inventory: RedisInventory,
    *,
    allowed_key_digests: Mapping[str, Sequence[str]] | None = None,
) -> int:
    """Delete only keys in the explicit orphan allowlist, one key at a time."""
    deleted = 0
    for spec in ORPHANED_KEY_SPECS:
        family = inventory.families[spec.pattern]
        if family.classification not in {"orphaned", "absent"}:
            raise RedisSafetyError(
                f"refusing {spec.pattern}: classification={family.classification}"
            )
        expected = (
            set(allowed_key_digests.get(spec.pattern, ()))
            if allowed_key_digests is not None
            else None
        )
        for key in family.keys:
            if expected is not None and _key_digest(key) not in expected:
                raise RedisSafetyError(
                    f"new key appeared outside reviewed report: {spec.pattern}"
                )
            deleted += await redis.delete(key)
    return deleted


def _status_counts(connection: Any, table: str) -> dict[str, int]:
    rows = connection.execute(
        f"SELECT status, count(*) FROM {table} GROUP BY status"
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _postgres_snapshot_sync() -> PostgresSnapshot:
    pool = get_database_pool()
    with pool.connection() as connection:
        cycle_statuses = _status_counts(connection, "conversation_processing_cycles")
        audio_statuses = _status_counts(connection, "message_transcriptions")
        image_statuses = _status_counts(connection, "message_image_extractions")
        row = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM conversation_processing_cycles),
                (SELECT count(*) FROM conversation_processing_cycles
                 WHERE enqueued_at IS NOT NULL),
                (SELECT count(*) FROM conversation_processing_cycles
                 WHERE next_attempt_at > now()),
                (SELECT count(*) FROM conversation_processing_cycles
                 WHERE lease_expires_at > now()),
                (SELECT count(*) FROM message_transcriptions
                 WHERE next_attempt_at > now()),
                (SELECT count(*) FROM message_image_extractions
                 WHERE next_attempt_at > now()),
                (SELECT count(*) FROM message_image_extractions
                 WHERE status = 'pending'),
                (SELECT count(*) FROM message_image_extractions
                 WHERE status = 'failed'),
                (SELECT count(*) FROM message_image_extractions
                 WHERE status = 'completed')
            """
        ).fetchone()
    if row is None:
        raise RedisSafetyError("PostgreSQL snapshot returned no row")
    counts = {
        "cycles": int(row[0]),
        "cycle_publication_markers": int(row[1]),
        "future_cycle_attempts": int(row[2]),
        "active_cycle_leases": int(row[3]),
        "future_audio_attempts": int(row[4]),
        "future_image_attempts": int(row[5]),
        "pending_images": int(row[6]),
        "failed_images": int(row[7]),
        "completed_images": int(row[8]),
    }
    return PostgresSnapshot(cycle_statuses, audio_statuses, image_statuses, counts)


async def collect_postgres_snapshot() -> PostgresSnapshot:
    """Inspect durable states without changing PostgreSQL."""
    await initialize_database()
    try:
        return await asyncio.to_thread(_postgres_snapshot_sync)
    finally:
        await close_database()


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _read_report(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RedisSafetyError(f"cannot read dry-run report: {path}") from exc
    if not isinstance(value, dict):
        raise RedisSafetyError("report is not a supported Redis dry-run report")
    report = cast(dict[str, Any], value)
    if report.get("report_version") != _REPORT_VERSION:
        raise RedisSafetyError("report is not a supported Redis dry-run report")
    return report


def _reviewed_digests(report: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    raw_redis = report.get("redis")
    if not isinstance(raw_redis, Mapping):
        raise RedisSafetyError("dry-run report has no Redis inventory")
    redis_inventory = cast(Mapping[str, Any], raw_redis)
    raw_families = redis_inventory.get("families")
    if not isinstance(raw_families, Mapping):
        raise RedisSafetyError("dry-run report has no Redis family inventory")
    families = cast(Mapping[str, Any], raw_families)
    reviewed: dict[str, tuple[str, ...]] = {}
    for spec in ORPHANED_KEY_SPECS:
        raw_family = families.get(spec.pattern)
        if not isinstance(raw_family, Mapping):
            raise RedisSafetyError(f"dry-run report is missing {spec.pattern}")
        family = cast(Mapping[str, Any], raw_family)
        raw_digests = family.get("key_digests")
        if not isinstance(raw_digests, list):
            raise RedisSafetyError(
                f"dry-run report has invalid digests for {spec.pattern}"
            )
        digests: list[str] = []
        for digest_value in cast(list[object], raw_digests):
            if not isinstance(digest_value, str):
                raise RedisSafetyError(
                    f"dry-run report has invalid digests for {spec.pattern}"
                )
            digests.append(digest_value)
        reviewed[spec.pattern] = tuple(digests)
    return reviewed


async def _run_dry_run(report_path: Path, max_keys: int) -> None:
    redis_client = create_redis_client()
    redis = cast(RedisMaintenanceClient, redis_client)
    try:
        inventory = await collect_inventory(redis, max_keys_per_pattern=max_keys)
        postgres = await collect_postgres_snapshot()
        report = {
            "report_version": _REPORT_VERSION,
            "mode": "dry-run",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "redis": inventory.report(),
            "postgres": postgres.report(),
            "decision": (
                "Review the orphaned families and PostgreSQL/queue counts before "
                "using --apply --confirm. ia_processing is retained."
            ),
        }
        _write_report(report_path, report)
        print(json.dumps(report, indent=2, ensure_ascii=False))
    finally:
        await redis_client.aclose()


async def _run_apply(report_path: Path, max_keys: int) -> None:
    report = _read_report(report_path)
    if report.get("mode") != "dry-run":
        raise RedisSafetyError("--apply requires a dry-run report")
    reviewed = _reviewed_digests(report)
    redis_client = create_redis_client()
    redis = cast(RedisMaintenanceClient, redis_client)
    try:
        before_inventory = await collect_inventory(redis, max_keys_per_pattern=max_keys)
        before_postgres = await collect_postgres_snapshot()
        for spec in ORPHANED_KEY_SPECS:
            current = {
                _key_digest(key) for key in before_inventory.families[spec.pattern].keys
            }
            expected = set(reviewed[spec.pattern])
            if not current.issubset(expected):
                raise RedisSafetyError(
                    f"{spec.pattern} changed since dry-run; create and review a new report"
                )
        deleted = await delete_orphaned_keys(
            redis, before_inventory, allowed_key_digests=reviewed
        )
        after_inventory = await collect_inventory(redis, max_keys_per_pattern=max_keys)
        remaining = sum(family.count for family in after_inventory.families.values())
        if remaining:
            raise RedisSafetyError(
                f"orphaned key families remain after apply: {remaining}"
            )
        after_postgres = await collect_postgres_snapshot()
        result = {
            "report_version": _REPORT_VERSION,
            "mode": "apply-result",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "deleted": deleted,
            "before": {
                "redis": before_inventory.report(),
                "postgres": before_postgres.report(),
            },
            "after": {
                "redis": after_inventory.report(),
                "postgres": after_postgres.report(),
            },
            "decision": "Active queues, dead letters, ia_processing, and PostgreSQL were retained.",
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
    parser.add_argument("--max-keys", type=int, default=1000)
    args = parser.parse_args()
    if args.apply and not args.confirm:
        parser.error("--apply requires --confirm after reviewing --dry-run output")
    if args.max_keys < 1:
        parser.error("--max-keys must be positive")
    if args.dry_run:
        asyncio.run(_run_dry_run(args.report, args.max_keys))
    else:
        asyncio.run(_run_apply(args.report, args.max_keys))


if __name__ == "__main__":
    main()
