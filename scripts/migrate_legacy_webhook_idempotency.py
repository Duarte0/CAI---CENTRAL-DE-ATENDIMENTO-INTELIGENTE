"""Migrate live Redis webhook markers into the PostgreSQL event ledger.

The command is a one-time, report-bound handoff.  It imports only valid
``processed:<sha256>`` markers with a positive remaining TTL and never deletes
or reads their Redis values.  A new marker appearing after the dry run blocks
the apply so an old API instance cannot silently create a mixed idempotency
boundary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, cast

from src.core.db import (
    close_database,
    import_legacy_webhook_event_keys,
    initialize_database,
)
from src.core.redis_client import create_redis_client


REPORT_VERSION = 1
LEGACY_PREFIX = "processed:"
DEFAULT_MAX_ITEMS = 1_000
CONFIRMATION = "migrate-legacy-webhook-idempotency"
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)


class HandoffSafetyError(RuntimeError):
    """Raised when the reviewed Redis handoff target is no longer exact."""


class LegacyProcessedRedis(Protocol):
    def scan_iter(self, *, match: str, count: int) -> AsyncIterator[object]:
        ...

    async def ttl(self, key: str) -> int:
        ...

    async def ping(self) -> bool:
        ...

    async def aclose(self) -> None:
        ...


@dataclass(frozen=True)
class MarkerInventory:
    scanned_keys: int
    valid_markers: dict[str, int]
    invalid_digest_keys: int
    expired_or_missing_keys: int
    no_expiry_keys: int
    truncated: bool

    def report(self) -> dict[str, Any]:
        return {
            "pattern": f"{LEGACY_PREFIX}*",
            "scanned_keys": self.scanned_keys,
            "valid_marker_count": len(self.valid_markers),
            "invalid_digest_keys": self.invalid_digest_keys,
            "expired_or_missing_keys": self.expired_or_missing_keys,
            "no_expiry_keys": self.no_expiry_keys,
            "truncated": self.truncated,
            "markers": [
                {"event_digest": digest, "ttl_seconds": ttl}
                for digest, ttl in sorted(self.valid_markers.items())
            ],
        }


def _validate_metadata(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized or any(character in normalized for character in "\r\n\x00"):
        raise HandoffSafetyError(f"{name} must be a nonblank single-line value")
    return normalized


def _validate_revision(value: str) -> str:
    normalized = _validate_metadata(value, "revision")
    if not _REVISION_RE.fullmatch(normalized):
        raise HandoffSafetyError("revision must be a git commit hash")
    return normalized


def _digest_from_key(value: str) -> str | None:
    if not value.startswith(LEGACY_PREFIX):
        return None
    digest = value.removeprefix(LEGACY_PREFIX)
    return digest if _DIGEST_RE.fullmatch(digest) else None


async def inventory_legacy_webhook_markers(
    redis: LegacyProcessedRedis,
    *,
    max_items: int = DEFAULT_MAX_ITEMS,
) -> MarkerInventory:
    """Inspect a bounded marker set without reading values or changing Redis."""
    if max_items <= 0:
        raise ValueError("max_items must be positive")
    seen_keys: set[str] = set()
    valid_markers: dict[str, int] = {}
    invalid_digest_keys = 0
    expired_or_missing_keys = 0
    no_expiry_keys = 0
    truncated = False
    async for raw_key in redis.scan_iter(match=f"{LEGACY_PREFIX}*", count=100):
        if not isinstance(raw_key, str):
            invalid_digest_keys += 1
            continue
        if raw_key in seen_keys:
            continue
        seen_keys.add(raw_key)
        if len(seen_keys) > max_items:
            truncated = True
            break
        digest = _digest_from_key(raw_key)
        if digest is None:
            invalid_digest_keys += 1
            continue
        ttl = await redis.ttl(raw_key)
        if ttl == -1:
            no_expiry_keys += 1
        elif ttl <= 0:
            expired_or_missing_keys += 1
        else:
            valid_markers[digest] = ttl
    return MarkerInventory(
        scanned_keys=len(seen_keys),
        valid_markers=valid_markers,
        invalid_digest_keys=invalid_digest_keys,
        expired_or_missing_keys=expired_or_missing_keys,
        no_expiry_keys=no_expiry_keys,
        truncated=truncated,
    )


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_report(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HandoffSafetyError(f"cannot read handoff report: {path}") from exc
    if not isinstance(value, dict):
        raise HandoffSafetyError("handoff report must contain a JSON object")
    return cast(dict[str, Any], value)


def _report_markers(report: Mapping[str, Any]) -> dict[str, int]:
    raw_inventory = report.get("inventory")
    if not isinstance(raw_inventory, Mapping):
        raise HandoffSafetyError("handoff report has no marker inventory")
    if raw_inventory.get("truncated") is not False:
        raise HandoffSafetyError("handoff report is truncated")
    raw_markers = raw_inventory.get("markers")
    if not isinstance(raw_markers, list):
        raise HandoffSafetyError("handoff report has no marker list")
    markers: dict[str, int] = {}
    for raw_marker in raw_markers:
        if not isinstance(raw_marker, Mapping):
            raise HandoffSafetyError("handoff report has an invalid marker")
        digest = raw_marker.get("event_digest")
        ttl = raw_marker.get("ttl_seconds")
        if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
            raise HandoffSafetyError("handoff report has an invalid digest")
        if not isinstance(ttl, int) or ttl <= 0:
            raise HandoffSafetyError("handoff report has an invalid marker TTL")
        if digest in markers:
            raise HandoffSafetyError("handoff report contains a duplicate digest")
        markers[digest] = ttl
    return markers


def _validate_dry_run_report(
    report: Mapping[str, Any],
    *,
    operator: str,
    revision: str,
    max_items: int,
) -> dict[str, int]:
    if report.get("report_version") != REPORT_VERSION or report.get("mode") != "dry_run":
        raise HandoffSafetyError("--apply requires a compatible dry-run report")
    metadata = report.get("metadata")
    if not isinstance(metadata, Mapping):
        raise HandoffSafetyError("handoff report has no execution metadata")
    if metadata.get("operator") != operator or metadata.get("revision") != revision:
        raise HandoffSafetyError("operator/revision differ from the dry-run report")
    recorded_max_items = metadata.get("max_items")
    if not isinstance(recorded_max_items, int) or max_items < recorded_max_items:
        raise HandoffSafetyError(
            "--max-items cannot be lower than the reviewed dry-run bound"
        )
    return _report_markers(report)


async def _run_dry_run(
    *,
    operator: str,
    revision: str,
    report_path: Path,
    max_items: int,
) -> dict[str, Any]:
    redis = None
    try:
        await initialize_database()
        redis = create_redis_client()
        await redis.ping()
        inventory = await inventory_legacy_webhook_markers(
            cast(LegacyProcessedRedis, redis), max_items=max_items
        )
        report = {
            "report_version": REPORT_VERSION,
            "mode": "dry_run",
            "metadata": {
                "operator": operator,
                "revision": revision,
                "max_items": max_items,
                "source": f"{LEGACY_PREFIX}*",
                "database_mutation": False,
                "redis_mutation": False,
            },
            "inventory": inventory.report(),
            "decision": (
                "stop old API instances, review this complete marker inventory, "
                "then apply the PostgreSQL handoff before starting the new API"
            ),
        }
        _write_report(report_path, report)
        return report
    finally:
        if redis is not None:
            await redis.aclose()
        await close_database()


async def _run_apply(
    *,
    operator: str,
    revision: str,
    report_path: Path,
    max_items: int,
    backup_reference: str,
    confirm: str,
) -> dict[str, Any]:
    report = _read_report(report_path)
    reviewed = _validate_dry_run_report(
        report,
        operator=operator,
        revision=revision,
        max_items=max_items,
    )
    if confirm != CONFIRMATION:
        raise HandoffSafetyError(f"--confirm must be exactly {CONFIRMATION}")
    if not backup_reference.strip() or any(
        character in backup_reference for character in "\r\n\x00"
    ):
        raise HandoffSafetyError(
            "backup-reference must identify an approved recovery point"
        )
    redis = None
    try:
        await initialize_database()
        redis = create_redis_client()
        await redis.ping()
        current = await inventory_legacy_webhook_markers(
            cast(LegacyProcessedRedis, redis), max_items=max_items
        )
        if current.truncated:
            raise HandoffSafetyError(
                "current marker inventory is truncated; rerun the dry-run"
            )
        current_digests = set(current.valid_markers)
        new_digests = current_digests - set(reviewed)
        if new_digests:
            raise HandoffSafetyError(
                "new Redis webhook markers appeared after dry-run; stop old API "
                "instances and create a new report"
            )
        entries = sorted(current.valid_markers.items())
        imported = await import_legacy_webhook_event_keys(entries)
        report["updated_at"] = datetime.now(timezone.utc).isoformat()
        report["apply"] = {
            "operator": operator,
            "revision": revision,
            "confirmation": confirm,
            "backup_reference": backup_reference,
            "live_markers_at_apply": len(entries),
            "reviewed_markers_expired_before_apply": len(set(reviewed) - current_digests),
            "imported_count": imported,
            "redis_source_deleted": False,
            "database_only_mutation": True,
        }
        _write_report(report_path, report)
        return report
    finally:
        if redis is not None:
            await redis.aclose()
        await close_database()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--apply", action="store_true")
    parser.add_argument("--operator", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--confirm", default="")
    parser.add_argument("--backup-reference", default="")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main() -> None:
    args = _parser().parse_args()
    operator = _validate_metadata(args.operator, "operator")
    revision = _validate_revision(args.revision)
    if args.max_items <= 0:
        raise SystemExit("--max-items must be positive")
    if args.apply and not args.backup_reference:
        raise SystemExit("--backup-reference is required with --apply")
    if args.dry_run and args.confirm:
        raise SystemExit("--confirm is only valid with --apply")
    if args.dry_run:
        result = asyncio.run(
            _run_dry_run(
                operator=operator,
                revision=revision,
                report_path=args.report,
                max_items=args.max_items,
            )
        )
    else:
        result = asyncio.run(
            _run_apply(
                operator=operator,
                revision=revision,
                report_path=args.report,
                max_items=args.max_items,
                backup_reference=args.backup_reference,
                confirm=args.confirm,
            )
        )
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
