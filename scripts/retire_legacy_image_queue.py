"""Inventory and boundedly retire legacy image Redis lists.

The image worker polls PostgreSQL directly. This command never republishes work:
it compares bounded Redis-list snapshots with durable extraction rows and, in
explicit apply mode, removes only entries whose durable state is safe to retire.
Unknown and malformed values remain available for investigation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol

from src.core.config import settings
from src.core.db import (
    close_database,
    get_image_extraction,
    initialize_database,
    set_image_extraction_status,
)
from src.core.redis_client import create_redis_client
from src.workers.image_worker import _is_transient_failure_text


LEGACY_IMAGE_QUEUE = "image_extraction_queue"
LEGACY_IMAGE_DEAD_LETTER = "image_extraction_dead_letter"
DEFAULT_MAX_ITEMS = 1_000
_APPLY_CONFIRMATION = "retire-legacy-image-queue"


class LegacyImageRedis(Protocol):
    async def llen(self, key: str) -> int:
        ...

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        ...

    async def lrem(self, key: str, count: int, value: str) -> int:
        ...


ImageLookup = Callable[[list[str]], Awaitable[dict[str, dict[str, Any]]]]


@dataclass(frozen=True)
class LegacyImageInventory:
    physical_counts: dict[str, int]
    inspected_counts: dict[str, int]
    truncated: dict[str, bool]
    malformed_counts: dict[str, int]
    unknown_counts: dict[str, int]
    entries_by_message: dict[str, dict[str, int]]
    durable_rows: dict[str, dict[str, Any]]
    removable_values: dict[str, tuple[str, ...]] = field(repr=False)

    @property
    def physical_entries(self) -> int:
        return sum(self.physical_counts.values())

    @property
    def inspected_entries(self) -> int:
        return sum(self.inspected_counts.values())

    @property
    def unique_message_ids(self) -> int:
        return len(self.entries_by_message)

    @property
    def duplicate_entries(self) -> int:
        return self.inspected_entries - sum(
            self.malformed_counts.values()
        ) - self.unique_message_ids

    def report(self) -> dict[str, Any]:
        messages = []
        for message_id in sorted(self.entries_by_message):
            row = self.durable_rows.get(message_id)
            messages.append(
                {
                    "message_id": message_id,
                    "physical_entries": self.entries_by_message[message_id],
                    "durable_state": (
                        {
                            "status": row.get("status"),
                            "attempt_count": row.get("attempt_count"),
                            "next_attempt_at": row.get("next_attempt_at"),
                            "lease_expires_at": row.get("lease_expires_at"),
                            "completed_at": row.get("completed_at"),
                            "updated_at": row.get("updated_at"),
                        }
                        if row is not None
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
                    "unknown_message_entries": self.unknown_counts[key],
                    "validated_entries_eligible_for_retirement": len(
                        self.removable_values[key]
                    ),
                }
                for key in self.physical_counts
            },
            "physical_entries": self.physical_entries,
            "inspected_entries": self.inspected_entries,
            "unique_message_ids": self.unique_message_ids,
            "duplicate_entries": self.duplicate_entries,
            "messages": messages,
            "decision": (
                "rerun with a larger --max-items before apply"
                if any(self.truncated.values())
                else "apply may remove only validated safe entries"
            ),
        }


def _message_id_from_value(value: str) -> str | None:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, Mapping):
        return None
    message_id = parsed.get("message_id")
    if not isinstance(message_id, str):
        return None
    normalized = message_id.strip()
    return normalized or None


async def inventory_legacy_image_lists(
    redis: LegacyImageRedis,
    *,
    image_lookup: ImageLookup,
    max_items: int = DEFAULT_MAX_ITEMS,
) -> LegacyImageInventory:
    """Build a bounded inventory without mutating Redis or PostgreSQL."""
    if max_items <= 0:
        raise ValueError("max_items must be positive")
    queues = (LEGACY_IMAGE_QUEUE, LEGACY_IMAGE_DEAD_LETTER)
    physical_counts = {key: await redis.llen(key) for key in queues}
    inspected: dict[str, list[str]] = {
        key: await redis.lrange(key, 0, max_items - 1) for key in queues
    }
    parsed: dict[str, list[tuple[str, str]]] = {key: [] for key in queues}
    malformed_counts: dict[str, int] = {}
    for key in queues:
        malformed_counts[key] = 0
        for value in inspected[key]:
            message_id = _message_id_from_value(value)
            if message_id is None:
                malformed_counts[key] += 1
            else:
                parsed[key].append((value, message_id))

    message_ids = list(
        dict.fromkeys(
            message_id
            for key in queues
            for _value, message_id in parsed[key]
        )
    )
    durable_rows = await image_lookup(message_ids)
    entries_by_message: dict[str, dict[str, int]] = {}
    unknown_counts: dict[str, int] = {}
    removable_values: dict[str, tuple[str, ...]] = {}
    for key in queues:
        unknown_counts[key] = sum(
            1 for _value, message_id in parsed[key] if message_id not in durable_rows
        )
        for _value, message_id in parsed[key]:
            counts = entries_by_message.setdefault(
                message_id,
                {LEGACY_IMAGE_QUEUE: 0, LEGACY_IMAGE_DEAD_LETTER: 0},
            )
            counts[key] += 1
        safe_values: list[str] = []
        for value, message_id in parsed[key]:
            row = durable_rows.get(message_id)
            if row is None:
                continue
            if key == LEGACY_IMAGE_QUEUE:
                safe_values.append(value)
            elif row.get("status") == "completed":
                safe_values.append(value)
            elif row.get("status") == "failed" and not _is_transient_failure_text(
                row.get("error_message")
                if isinstance(row.get("error_message"), str)
                else None
            ):
                safe_values.append(value)
        removable_values[key] = tuple(safe_values)

    return LegacyImageInventory(
        physical_counts=physical_counts,
        inspected_counts={key: len(inspected[key]) for key in queues},
        truncated={key: physical_counts[key] > len(inspected[key]) for key in queues},
        malformed_counts=malformed_counts,
        unknown_counts=unknown_counts,
        entries_by_message=entries_by_message,
        durable_rows=durable_rows,
        removable_values=removable_values,
    )


async def retire_validated_legacy_image_entries(
    redis: LegacyImageRedis,
    inventory: LegacyImageInventory,
) -> int:
    """Remove only safe, inspected exact values after a complete inventory."""
    if any(inventory.truncated.values()):
        raise RuntimeError(
            "cannot apply a truncated inventory; increase --max-items first"
        )
    removed = 0
    for key, values in inventory.removable_values.items():
        for value in values:
            removed += await redis.lrem(key, 1, value)
    return removed


async def recover_legacy_transient_dead_letters(
    inventory: LegacyImageInventory,
) -> int:
    """Import transient dead-letter evidence into DB-only retry state."""
    recovered = 0
    for message_id, row in inventory.durable_rows.items():
        if row.get("status") != "failed":
            continue
        error_message = row.get("error_message")
        if not isinstance(error_message, str) or not _is_transient_failure_text(
            error_message
        ):
            continue
        if not inventory.entries_by_message.get(message_id, {}).get(
            LEGACY_IMAGE_DEAD_LETTER
        ):
            continue
        retry_at = datetime.now(timezone.utc) + timedelta(
            seconds=settings.image_retry_base_seconds
        )
        transitioned = await set_image_extraction_status(
            message_id,
            "pending",
            error_message="transient_image_failure:legacy_dead_letter",
            next_attempt_at=retry_at,
            expected_statuses=("failed",),
        )
        if transitioned is not None:
            recovered += 1
    return recovered


async def _lookup_images(
    message_ids: list[str],
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for message_id in message_ids:
        row = await get_image_extraction(message_id)
        if row is not None:
            rows[message_id] = row
    return rows


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inventory legacy image Redis lists and optionally import transient "
            "dead letters or retire safe entries."
        )
    )
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--recover-transient", action="store_true")
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
        raise RuntimeError("--apply requires --confirm " + _APPLY_CONFIRMATION)
    redis = None
    try:
        await initialize_database()
        redis = create_redis_client()
        await redis.ping()
        inventory = await inventory_legacy_image_lists(
            redis,
            image_lookup=_lookup_images,
            max_items=args.max_items,
        )
        report: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "apply" if args.apply else "dry_run",
            "inventory": inventory.report(),
        }
        if args.recover_transient:
            report["recovered_transient_dead_letters"] = (
                await recover_legacy_transient_dead_letters(inventory)
            )
        if args.apply:
            report["removed_validated_entries"] = (
                await retire_validated_legacy_image_entries(redis, inventory)
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
