"""Bounded, observable cleanup for expired PostgreSQL webhook event keys."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, cast

from src.core.db import (
    close_database,
    initialize_database,
)
from src.core.webhook_event_repository import (
    WebhookEventCleanupReport,
    cleanup_expired_webhook_event_keys,
    count_expired_webhook_event_keys,
)


REPORT_VERSION = 1
DEFAULT_BATCH_SIZE = 100
MAX_BATCH_SIZE = 1_000
CONFIRMATION = "cleanup-expired-webhook-event-keys"
_REVISION_RE = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)


class CleanupSafetyError(RuntimeError):
    """Raised when a cleanup report or target is not safe to use."""


def _metadata(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized or any(character in normalized for character in "\r\n\x00"):
        raise CleanupSafetyError(f"{name} must be a nonblank single-line value")
    return normalized


def _revision(value: str) -> str:
    normalized = _metadata(value, "revision")
    if not _REVISION_RE.fullmatch(normalized):
        raise CleanupSafetyError("revision must be a git commit hash")
    return normalized


def _validate_batch_size(value: int) -> int:
    if not 1 <= value <= MAX_BATCH_SIZE:
        raise CleanupSafetyError(
            f"batch-size must be between 1 and {MAX_BATCH_SIZE}"
        )
    return value


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
        raise CleanupSafetyError(f"cannot read cleanup report: {path}") from exc
    if not isinstance(value, dict):
        raise CleanupSafetyError("cleanup report must contain a JSON object")
    return cast(dict[str, Any], value)


def _validate_dry_run_report(
    report: Mapping[str, Any],
    *,
    operator: str,
    revision: str,
    batch_size: int,
) -> None:
    if report.get("report_version") != REPORT_VERSION or report.get("mode") != "dry_run":
        raise CleanupSafetyError("--apply requires a compatible dry-run report")
    metadata = report.get("metadata")
    if not isinstance(metadata, Mapping):
        raise CleanupSafetyError("cleanup report has no execution metadata")
    if metadata.get("operator") != operator or metadata.get("revision") != revision:
        raise CleanupSafetyError("operator/revision differ from the dry-run report")
    if metadata.get("batch_size") != batch_size:
        raise CleanupSafetyError("batch-size differs from the dry-run report")
    if not isinstance(report.get("expired_before"), int):
        raise CleanupSafetyError("cleanup report has no expired-row count")


async def _run_dry_run(
    *, operator: str, revision: str, batch_size: int, report_path: Path
) -> dict[str, Any]:
    await initialize_database()
    try:
        expired_before = await count_expired_webhook_event_keys()
        report = {
            "report_version": REPORT_VERSION,
            "mode": "dry_run",
            "metadata": {
                "operator": operator,
                "revision": revision,
                "batch_size": batch_size,
                "database_mutation": False,
            },
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "expired_before": expired_before,
            "decision": (
                "review the count and apply at most the declared batch; rerun "
                "until expired_before is zero"
            ),
        }
        _write_report(report_path, report)
        return report
    finally:
        await close_database()


async def _run_apply(
    *,
    operator: str,
    revision: str,
    batch_size: int,
    report_path: Path,
    backup_reference: str,
    confirm: str,
) -> dict[str, Any]:
    report = _read_report(report_path)
    _validate_dry_run_report(
        report,
        operator=operator,
        revision=revision,
        batch_size=batch_size,
    )
    if confirm != CONFIRMATION:
        raise CleanupSafetyError(f"--confirm must be exactly {CONFIRMATION}")
    if not backup_reference.strip() or any(
        character in backup_reference for character in "\r\n\x00"
    ):
        raise CleanupSafetyError(
            "backup-reference must identify an approved recovery point"
        )
    await initialize_database()
    try:
        result: WebhookEventCleanupReport = (
            await cleanup_expired_webhook_event_keys(batch_size)
        )
        report["updated_at"] = datetime.now(timezone.utc).isoformat()
        report["apply"] = {
            "operator": operator,
            "revision": revision,
            "confirmation": confirm,
            "backup_reference": backup_reference,
            "before_count": result.before_count,
            "removed_count": result.removed_count,
            "after_count": result.after_count,
            "database_only_mutation": True,
        }
        _write_report(report_path, report)
        return report
    finally:
        await close_database()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--apply", action="store_true")
    parser.add_argument("--operator", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--confirm", default="")
    parser.add_argument("--backup-reference", default="")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main() -> None:
    args = _parser().parse_args()
    operator = _metadata(args.operator, "operator")
    revision = _revision(args.revision)
    batch_size = _validate_batch_size(args.batch_size)
    if args.dry_run and args.confirm:
        raise SystemExit("--confirm is only valid with --apply")
    if args.apply and not args.backup_reference:
        raise SystemExit("--backup-reference is required with --apply")
    if args.dry_run:
        result = asyncio.run(
            _run_dry_run(
                operator=operator,
                revision=revision,
                batch_size=batch_size,
                report_path=args.report,
            )
        )
    else:
        result = asyncio.run(
            _run_apply(
                operator=operator,
                revision=revision,
                batch_size=batch_size,
                report_path=args.report,
                backup_reference=args.backup_reference,
                confirm=args.confirm,
            )
        )
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
