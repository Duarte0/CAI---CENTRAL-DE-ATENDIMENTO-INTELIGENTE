"""Inventory and boundedly retire legacy IA Redis lists.

The IA worker polls PostgreSQL directly. This command never republishes work:
it compares a bounded Redis-list snapshot with durable cycle rows and, only in
explicit apply mode, removes one validated list item at a time. Unknown and
malformed items are retained for investigation.
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
from typing import Any, Awaitable, Callable, Mapping, Protocol

from src.core.db import (
    close_database,
    get_cycles_by_public_ids,
    initialize_database,
)
from src.core.redis_client import create_redis_client


LEGACY_IA_QUEUE = "ia_queue"
LEGACY_IA_DEAD_LETTER = "ia_dead_letter"
DEFAULT_MAX_ITEMS = 1_000
_APPLY_CONFIRMATION = "retire-legacy-ia-queue"


class LegacyQueueRedis(Protocol):
    async def llen(self, key: str) -> int:
        ...

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        ...

    async def lrem(self, key: str, count: int, value: str) -> int:
        ...


CycleLookup = Callable[[list[str]], Awaitable[dict[str, dict[str, Any]]]]


@dataclass(frozen=True)
class LegacyIAQueueInventory:
    physical_entries: int
    inspected_entries: int
    truncated: bool
    malformed_entries: int
    unknown_cycle_entries: int
    entries_by_cycle: dict[str, int]
    durable_cycles: dict[str, dict[str, Any]]
    _removable_values: tuple[str, ...] = field(repr=False)
    entry_digests: tuple[str, ...] = field(repr=False)

    @property
    def unique_cycle_ids(self) -> int:
        return len(self.entries_by_cycle)

    @property
    def duplicate_entries(self) -> int:
        return self.inspected_entries - self.malformed_entries - self.unique_cycle_ids

    def report(self) -> dict[str, Any]:
        cycle_entries: list[dict[str, Any]] = []
        for cycle_id in sorted(self.entries_by_cycle):
            durable = self.durable_cycles.get(cycle_id)
            cycle_entries.append(
                {
                    "cycle_id": cycle_id,
                    "physical_entries": self.entries_by_cycle[cycle_id],
                    "durable_state": (
                        {
                            "status": durable.get("status"),
                            "next_attempt_at": durable.get("next_attempt_at"),
                            "lease_expires_at": durable.get("lease_expires_at"),
                            "completed_at": durable.get("completed_at"),
                            "updated_at": durable.get("updated_at"),
                        }
                        if durable is not None
                        else None
                    ),
                }
            )
        return {
            "queue": LEGACY_IA_QUEUE,
            "physical_entries": self.physical_entries,
            "inspected_entries": self.inspected_entries,
            "truncated": self.truncated,
            "unique_cycle_ids": self.unique_cycle_ids,
            "duplicate_entries": self.duplicate_entries,
            "malformed_entries": self.malformed_entries,
            "unknown_cycle_entries": self.unknown_cycle_entries,
            "entry_digests": list(self.entry_digests),
            "validated_entries_eligible_for_retirement": len(
                self._removable_values
            ),
            "cycles": cycle_entries,
            "decision": (
                "rerun with a larger --max-items before apply"
                if self.truncated
                else "apply may remove only validated durable-cycle entries"
            ),
        }


@dataclass(frozen=True)
class LegacyIAListsInventory:
    """Complete inventory for the IA queue and its legacy dead-letter list."""

    physical_counts: dict[str, int]
    inspected_counts: dict[str, int]
    truncated: dict[str, bool]
    malformed_counts: dict[str, int]
    unknown_counts: dict[str, int]
    entries_by_cycle: dict[str, dict[str, int]]
    durable_cycles: dict[str, dict[str, Any]]
    removable_values: dict[str, tuple[str, ...]] = field(repr=False)
    entry_digests: dict[str, tuple[str, ...]] = field(repr=False)

    @property
    def physical_entries(self) -> int:
        return sum(self.physical_counts.values())

    @property
    def inspected_entries(self) -> int:
        return sum(self.inspected_counts.values())

    @property
    def unique_cycle_ids(self) -> int:
        return len(self.entries_by_cycle)

    @property
    def duplicate_entries(self) -> int:
        return self.inspected_entries - sum(self.malformed_counts.values()) - self.unique_cycle_ids

    def report(self) -> dict[str, Any]:
        cycles = []
        for cycle_id in sorted(self.entries_by_cycle):
            durable = self.durable_cycles.get(cycle_id)
            cycles.append(
                {
                    "cycle_id": cycle_id,
                    "physical_entries": sum(self.entries_by_cycle[cycle_id].values()),
                    "queues": self.entries_by_cycle[cycle_id],
                    "durable_state": (
                        {
                            "status": durable.get("status"),
                            "next_attempt_at": durable.get("next_attempt_at"),
                            "lease_expires_at": durable.get("lease_expires_at"),
                            "completed_at": durable.get("completed_at"),
                            "updated_at": durable.get("updated_at"),
                        }
                        if durable is not None
                        else None
                    ),
                }
            )
        return {
            "queues": {
                key: {
                    "physical_entries": self.physical_counts[key],
                    "inspected_entries": self.inspected_counts[key],
                    "truncated": self.truncated[key],
                    "malformed_entries": self.malformed_counts[key],
                    "unknown_cycle_entries": self.unknown_counts[key],
                    "entry_digests": list(self.entry_digests[key]),
                    "validated_entries_eligible_for_retirement": len(
                        self.removable_values[key]
                    ),
                }
                for key in self.physical_counts
            },
            "physical_entries": self.physical_entries,
            "inspected_entries": self.inspected_entries,
            "unique_cycle_ids": self.unique_cycle_ids,
            "duplicate_entries": self.duplicate_entries,
            "malformed_entries": sum(self.malformed_counts.values()),
            "unknown_cycle_entries": sum(self.unknown_counts.values()),
            "cycles": cycles,
            "decision": (
                "rerun with a larger --max-items before apply"
                if any(self.truncated.values())
                else "apply may remove only validated durable-cycle entries"
            ),
        }


def _cycle_id_from_value(value: str) -> str | None:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    cycle_id = parsed.get("cycle_id")
    if not isinstance(cycle_id, str):
        return None
    normalized = cycle_id.strip()
    return normalized or None


async def inventory_legacy_ia_queue(
    redis: LegacyQueueRedis,
    *,
    cycle_lookup: CycleLookup,
    max_items: int = DEFAULT_MAX_ITEMS,
) -> LegacyIAQueueInventory:
    """Build a bounded inventory without mutating Redis or PostgreSQL."""
    if max_items <= 0:
        raise ValueError("max_items must be positive")
    physical_entries = await redis.llen(LEGACY_IA_QUEUE)
    inspected_values = await redis.lrange(LEGACY_IA_QUEUE, 0, max_items - 1)
    if len(inspected_values) > max_items:
        raise RuntimeError("Redis returned more entries than the requested bound")
    parsed: list[tuple[str, str]] = []
    malformed_entries = 0
    for value in inspected_values:
        cycle_id = _cycle_id_from_value(value)
        if cycle_id is None:
            malformed_entries += 1
            continue
        parsed.append((value, cycle_id))
    cycle_ids = list(dict.fromkeys(cycle_id for _, cycle_id in parsed))
    durable_cycles = await cycle_lookup(cycle_ids)
    removable_values = tuple(
        value for value, cycle_id in parsed if cycle_id in durable_cycles
    )
    return LegacyIAQueueInventory(
        physical_entries=physical_entries,
        inspected_entries=len(inspected_values),
        truncated=physical_entries > len(inspected_values),
        malformed_entries=malformed_entries,
        unknown_cycle_entries=sum(
            1 for _, cycle_id in parsed if cycle_id not in durable_cycles
        ),
        entries_by_cycle=dict(Counter(cycle_id for _, cycle_id in parsed)),
        durable_cycles=durable_cycles,
        _removable_values=removable_values,
        entry_digests=tuple(
            hashlib.sha256(value.encode("utf-8")).hexdigest()
            for value in inspected_values
        ),
    )


async def inventory_legacy_ia_lists(
    redis: LegacyQueueRedis,
    *,
    cycle_lookup: CycleLookup,
    max_items: int = DEFAULT_MAX_ITEMS,
) -> LegacyIAListsInventory:
    """Inventory both IA legacy lists without mutating either store."""
    if max_items <= 0:
        raise ValueError("max_items must be positive")
    queues = (LEGACY_IA_QUEUE, LEGACY_IA_DEAD_LETTER)
    physical_counts = {key: await redis.llen(key) for key in queues}
    inspected = {
        key: await redis.lrange(key, 0, max_items - 1) for key in queues
    }
    for key in queues:
        if len(inspected[key]) > max_items:
            raise RuntimeError("Redis returned more entries than the requested bound")
    parsed: dict[str, list[tuple[str, str]]] = {key: [] for key in queues}
    malformed_counts: dict[str, int] = {}
    for key in queues:
        malformed_counts[key] = 0
        for value in inspected[key]:
            cycle_id = _cycle_id_from_value(value)
            if cycle_id is None:
                malformed_counts[key] += 1
            else:
                parsed[key].append((value, cycle_id))
    cycle_ids = list(
        dict.fromkeys(
            cycle_id for key in queues for _value, cycle_id in parsed[key]
        )
    )
    durable_cycles = await cycle_lookup(cycle_ids)
    entries_by_cycle: dict[str, dict[str, int]] = {}
    unknown_counts: dict[str, int] = {}
    removable_values: dict[str, tuple[str, ...]] = {}
    entry_digests: dict[str, tuple[str, ...]] = {}
    terminal_statuses = {"completed", "completed_with_warnings", "failed"}
    for key in queues:
        unknown_counts[key] = sum(
            1 for _value, cycle_id in parsed[key] if cycle_id not in durable_cycles
        )
        for _value, cycle_id in parsed[key]:
            counts = entries_by_cycle.setdefault(
                cycle_id, {LEGACY_IA_QUEUE: 0, LEGACY_IA_DEAD_LETTER: 0}
            )
            counts[key] += 1
        removable_values[key] = tuple(
            value
            for value, cycle_id in parsed[key]
            if cycle_id in durable_cycles
            and (
                key == LEGACY_IA_QUEUE
                or durable_cycles[cycle_id].get("status") in terminal_statuses
            )
        )
        entry_digests[key] = tuple(
            hashlib.sha256(value.encode("utf-8")).hexdigest()
            for value in inspected[key]
        )
    return LegacyIAListsInventory(
        physical_counts=physical_counts,
        inspected_counts={key: len(inspected[key]) for key in queues},
        truncated={key: physical_counts[key] > len(inspected[key]) for key in queues},
        malformed_counts=malformed_counts,
        unknown_counts=unknown_counts,
        entries_by_cycle=entries_by_cycle,
        durable_cycles=durable_cycles,
        removable_values=removable_values,
        entry_digests=entry_digests,
    )


async def retire_validated_legacy_ia_entries(
    redis: LegacyQueueRedis,
    inventory: LegacyIAListsInventory,
) -> int:
    """Remove only complete-snapshot values validated for their IA list."""
    if any(inventory.truncated.values()):
        raise RuntimeError(
            "cannot apply a truncated inventory; increase --max-items first"
        )
    removed = 0
    for key, values in inventory.removable_values.items():
        for value in values:
            removed += await redis.lrem(key, 1, value)
    return removed


async def retire_validated_legacy_ia_queue_entries(
    redis: LegacyQueueRedis,
    inventory: LegacyIAQueueInventory,
) -> int:
    """Remove exactly the validated, inspected list entries.

    Apply refuses an incomplete inventory. Each call removes at most one exact
    list value, so malformed/unknown values and entries beyond the inspected
    bound are never touched.
    """
    if inventory.truncated:
        raise RuntimeError(
            "cannot apply a truncated inventory; increase --max-items first"
        )
    removed = 0
    for value in inventory._removable_values:
        removed += await redis.lrem(LEGACY_IA_QUEUE, 1, value)
    return removed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inventory legacy ia_queue and ia_dead_letter entries and "
            "optionally retire only entries backed by durable PostgreSQL cycles."
        )
    )
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--confirm",
        default="",
        help=f"required with --apply: {_APPLY_CONFIRMATION}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON report path; parent directory must already exist",
    )
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.apply and args.confirm != _APPLY_CONFIRMATION:
        raise RuntimeError(
            "--apply requires --confirm " + _APPLY_CONFIRMATION
        )
    redis = None
    try:
        await initialize_database()
        redis = create_redis_client()
        await redis.ping()
        inventory = await inventory_legacy_ia_lists(
            redis,
            cycle_lookup=get_cycles_by_public_ids,
            max_items=args.max_items,
        )
        report: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "apply" if args.apply else "dry_run",
            "inventory": inventory.report(),
        }
        if args.apply:
            report["removed_validated_entries"] = (
                await retire_validated_legacy_ia_entries(redis, inventory)
            )
        return report
    finally:
        if redis is not None:
            await redis.aclose()
        await close_database()


def main() -> None:
    args = _parser().parse_args()
    report = asyncio.run(_run(args))
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
