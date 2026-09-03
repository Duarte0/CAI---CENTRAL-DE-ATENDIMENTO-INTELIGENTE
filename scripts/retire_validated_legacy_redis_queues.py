"""Coordinate the evidence-first retirement of the three legacy Redis lists.

The application workers no longer use these lists as a work transport.  This
command is the supported maintenance entry point: a dry run writes one complete
report, and apply consumes only that reviewed report, one family at a time.  It
never republishes a value and never writes PostgreSQL state.

The individual queue modules remain the narrow mutation boundary.  This module
adds the operational guardrails that cannot be enforced by a single-list
helper: target metadata, runtime snapshots, durable invariants, a second
snapshot, and an exact digest comparison that detects a changed list without
persisting raw Redis values.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from src.core.config import settings
from src.core.db import (
    CURRENT_SCHEMA_REVISION,
    close_database,
    get_cycles_by_public_ids,
    get_database_pool,
    get_image_extraction,
    get_transcription,
    initialize_database,
)
from src.core.redis_client import create_redis_client
from scripts.retire_legacy_audio_queue import (
    LEGACY_AUDIO_DEAD_LETTER,
    LEGACY_AUDIO_QUEUE,
    inventory_legacy_audio_lists,
    retire_validated_legacy_audio_entries,
)
from scripts.retire_legacy_ia_queue import (
    LEGACY_IA_DEAD_LETTER,
    LEGACY_IA_QUEUE,
    inventory_legacy_ia_lists,
    retire_validated_legacy_ia_entries,
)
from scripts.retire_legacy_image_queue import (
    LEGACY_IMAGE_DEAD_LETTER,
    LEGACY_IMAGE_QUEUE,
    inventory_legacy_image_lists,
    retire_validated_legacy_image_entries,
)


REPORT_VERSION = 1
DEFAULT_MAX_ITEMS = 1_000
DEFAULT_API_URL = "http://api:8000"
FAMILIES = ("ia", "audio", "image")
FAMILY_CONFIRMATIONS = {
    "ia": "retire-legacy-ia-queue",
    "audio": "retire-legacy-audio-queue",
    "image": "retire-legacy-image-queue",
}
_REVISION_RE = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)


class RetirementSafetyError(RuntimeError):
    """Raised when the reviewed target is no longer the current target."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_metadata(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized or any(character in normalized for character in "\r\n\x00"):
        raise RetirementSafetyError(f"{name} must be a nonblank single-line value")
    return normalized


def _validate_revision(value: str) -> str:
    normalized = _require_metadata(value, "revision")
    if not _REVISION_RE.fullmatch(normalized):
        raise RetirementSafetyError("revision must be a git commit hash")
    return normalized


def _validate_api_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RetirementSafetyError("api-url must be an absolute HTTP(S) URL")
    return normalized


def _runtime_json(api_url: str, path: str) -> dict[str, Any]:
    """Read a safe runtime endpoint without retaining URL/error contents."""
    request = Request(f"{api_url}{path}", method="GET")
    try:
        with urlopen(request, timeout=5) as response:
            status_code = int(response.status)
            raw = response.read()
    except HTTPError as exc:
        return {"status_code": int(exc.code), "healthy": False, "error": "http"}
    except (OSError, URLError, TimeoutError):
        return {"status_code": None, "healthy": False, "error": "unreachable"}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"status_code": status_code, "healthy": False, "error": "invalid_json"}
    if not isinstance(payload, dict):
        return {"status_code": status_code, "healthy": False, "error": "invalid_json"}
    return {"status_code": status_code, "healthy": status_code == 200, "body": payload}


def collect_runtime_snapshot(api_url: str) -> dict[str, Any]:
    """Collect only the health and queue JSON exposed by the application."""
    health = _runtime_json(api_url, "/health")
    queues = _runtime_json(api_url, "/queues")
    return {
        "captured_at": _utc_now(),
        "health": health,
        "queues": queues,
        "healthy": bool(health.get("healthy") and queues.get("healthy")),
    }


def _status_counts_sync(table: str) -> dict[str, int]:
    with get_database_pool().connection() as connection:
        rows = connection.execute(
            f"SELECT status, count(*) FROM {table} GROUP BY status ORDER BY status"
        ).fetchall()
    return {str(status): int(count) for status, count in rows}


def _postgres_invariants_sync() -> dict[str, Any]:
    """Return aggregate durable state only; no identifiers or content."""
    with get_database_pool().connection() as connection:
        row = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM conversation_processing_cycles),
                (SELECT count(*) FROM conversation_processing_cycles
                 WHERE status IN ('pending', 'recovering_messages', 'waiting_media',
                                  'building_context', 'summarizing', 'classifying',
                                  'retryable_failure')
                   AND (next_attempt_at IS NULL OR next_attempt_at <= now())),
                (SELECT count(*) FROM conversation_processing_cycles
                 WHERE next_attempt_at > now()),
                (SELECT count(*) FROM conversation_processing_cycles
                 WHERE lease_owner IS NOT NULL AND lease_expires_at > now()),
                (SELECT count(*) FROM message_transcriptions),
                (SELECT count(*) FROM message_transcriptions
                 WHERE status = 'pending'
                   AND (next_attempt_at IS NULL OR next_attempt_at <= now())),
                (SELECT count(*) FROM message_transcriptions
                 WHERE status = 'pending' AND next_attempt_at > now()),
                (SELECT count(*) FROM message_transcriptions
                 WHERE lease_owner IS NOT NULL AND lease_expires_at > now()),
                (SELECT count(*) FROM message_image_extractions),
                (SELECT count(*) FROM message_image_extractions
                 WHERE status = 'pending'
                   AND (next_attempt_at IS NULL OR next_attempt_at <= now())),
                (SELECT count(*) FROM message_image_extractions
                 WHERE status = 'pending' AND next_attempt_at > now()),
                (SELECT count(*) FROM message_image_extractions
                 WHERE lease_owner IS NOT NULL AND lease_expires_at > now())
            """
        ).fetchone()
    if row is None:
        raise RetirementSafetyError("PostgreSQL invariant snapshot returned no row")
    return {
        "captured_at": _utc_now(),
        "schema_revision": CURRENT_SCHEMA_REVISION,
        "cycles": {
            "total": int(row[0]),
            "due": int(row[1]),
            "scheduled": int(row[2]),
            "leased": int(row[3]),
            "by_status": _status_counts_sync("conversation_processing_cycles"),
        },
        "audio": {
            "total": int(row[4]),
            "due": int(row[5]),
            "scheduled": int(row[6]),
            "leased": int(row[7]),
            "by_status": _status_counts_sync("message_transcriptions"),
        },
        "image": {
            "total": int(row[8]),
            "due": int(row[9]),
            "scheduled": int(row[10]),
            "leased": int(row[11]),
            "by_status": _status_counts_sync("message_image_extractions"),
        },
    }


async def collect_postgres_invariants() -> dict[str, Any]:
    return await asyncio.to_thread(_postgres_invariants_sync)


async def _lookup_transcriptions(
    message_ids: list[str],
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for message_id in message_ids:
        row = await get_transcription(message_id)
        if row is not None:
            rows[message_id] = row
    return rows


async def _lookup_images(message_ids: list[str]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for message_id in message_ids:
        row = await get_image_extraction(message_id)
        if row is not None:
            rows[message_id] = row
    return rows


async def collect_legacy_inventories(
    redis: Any, max_items: int
) -> dict[str, dict[str, Any]]:
    ia = await inventory_legacy_ia_lists(
        redis, cycle_lookup=get_cycles_by_public_ids, max_items=max_items
    )
    audio = await inventory_legacy_audio_lists(
        redis, transcription_lookup=_lookup_transcriptions, max_items=max_items
    )
    image = await inventory_legacy_image_lists(
        redis, image_lookup=_lookup_images, max_items=max_items
    )
    return {
        "ia": ia.report(),
        "audio": audio.report(),
        "image": image.report(),
    }


def _family_digests(inventories: Mapping[str, Any], family: str) -> dict[str, list[str]]:
    raw = inventories.get(family)
    if not isinstance(raw, Mapping):
        raise RetirementSafetyError(f"report is missing {family} inventory")
    if family == "ia":
        raw_queues = raw.get("queues")
        if not isinstance(raw_queues, Mapping):
            raise RetirementSafetyError("report is missing IA queue inventory")
        result: dict[str, list[str]] = {}
        for queue in (LEGACY_IA_QUEUE, LEGACY_IA_DEAD_LETTER):
            queue_report = raw_queues.get(queue)
            if not isinstance(queue_report, Mapping):
                raise RetirementSafetyError(f"report is missing {queue} inventory")
            values = queue_report.get("entry_digests")
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise RetirementSafetyError(f"report has invalid {queue} entry digests")
            result[queue] = list(values)
        return result
    queues = (LEGACY_AUDIO_QUEUE, LEGACY_AUDIO_DEAD_LETTER) if family == "audio" else (
        LEGACY_IMAGE_QUEUE,
        LEGACY_IMAGE_DEAD_LETTER,
    )
    raw_queues = raw.get("queues")
    if not isinstance(raw_queues, Mapping):
        raise RetirementSafetyError(f"report is missing {family} queue inventory")
    result: dict[str, list[str]] = {}
    for queue in queues:
        queue_report = raw_queues.get(queue)
        if not isinstance(queue_report, Mapping):
            raise RetirementSafetyError(f"report is missing {queue} inventory")
        values = queue_report.get("entry_digests")
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise RetirementSafetyError(f"report has invalid {queue} entry digests")
        result[queue] = list(values)
    return result


def _assert_complete(inventories: Mapping[str, Any]) -> None:
    for family in FAMILIES:
        raw = inventories.get(family)
        if not isinstance(raw, Mapping):
            raise RetirementSafetyError(f"report is missing {family} inventory")
        queues = raw.get("queues")
        truncated = (
            any(
                bool(item.get("truncated"))
                for item in queues.values()
                if isinstance(item, Mapping)
            )
            if isinstance(queues, Mapping)
            else bool(raw.get("truncated", True))
        )
        if truncated:
            raise RetirementSafetyError(
                f"{family} inventory is truncated; increase --max-items and rerun"
            )


def _assert_second_snapshot(
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
    applied: set[str],
) -> None:
    """Require unchanged queue values, except families already retired."""
    _assert_complete(current)
    for family in FAMILIES:
        before = _family_digests(baseline, family)
        now = _family_digests(current, family)
        if family in applied:
            before_counts = {key: len(values) for key, values in before.items()}
            now_counts = {key: len(values) for key, values in now.items()}
            if any(now_counts[key] > before_counts[key] for key in before_counts):
                raise RetirementSafetyError(
                    f"{family} grew after its apply; create a new dry-run"
                )
            continue
        if now != before:
            raise RetirementSafetyError(
                f"{family} changed since dry-run; create and review a new report"
            )


def _validate_dry_run_report(
    report: Mapping[str, Any], operator: str, revision: str, max_items: int
) -> None:
    if report.get("report_version") != REPORT_VERSION or report.get("mode") != "dry_run":
        raise RetirementSafetyError("--apply requires a compatible dry-run report")
    metadata = report.get("metadata")
    if not isinstance(metadata, Mapping):
        raise RetirementSafetyError("dry-run report has no execution metadata")
    if metadata.get("operator") != operator or metadata.get("revision") != revision:
        raise RetirementSafetyError(
            "operator/revision differ from the reviewed dry-run report"
        )
    recorded_max_items = metadata.get("max_items")
    if not isinstance(recorded_max_items, int) or max_items < recorded_max_items:
        raise RetirementSafetyError(
            "--max-items cannot be lower than the reviewed dry-run bound"
        )
    inventories = report.get("inventories")
    if not isinstance(inventories, Mapping):
        raise RetirementSafetyError("dry-run report has no inventories")
    _assert_complete(inventories)
    runtime = report.get("runtime_before")
    if not isinstance(runtime, Mapping) or runtime.get("healthy") is not True:
        raise RetirementSafetyError("dry-run report does not prove healthy runtime")


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
        raise RetirementSafetyError(f"cannot read dry-run report: {path}") from exc
    if not isinstance(value, dict):
        raise RetirementSafetyError("report must contain a JSON object")
    return cast(dict[str, Any], value)


async def _run_dry_run(
    *, operator: str, revision: str, compose_project: str, report_path: Path,
    max_items: int, api_url: str,
) -> dict[str, Any]:
    redis = None
    try:
        await initialize_database()
        redis = create_redis_client()
        await redis.ping()
        runtime = await asyncio.to_thread(collect_runtime_snapshot, api_url)
        postgres = await collect_postgres_invariants()
        inventories = await collect_legacy_inventories(redis, max_items)
        report = {
            "report_version": REPORT_VERSION,
            "mode": "dry_run",
            "metadata": {
                "captured_at": _utc_now(),
                "operator": operator,
                "revision": revision,
                "compose_project": compose_project,
                "max_items": max_items,
                "database_configured": bool(settings.database_url),
                "redis_configured": bool(settings.redis_url),
                "api_endpoint": "/health and /queues",
            },
            "runtime_before": runtime,
            "postgres_before": postgres,
            "inventories": inventories,
            "decision": (
                "review this complete report and archive the approved PostgreSQL "
                "backup/recovery-point reference before applying one family"
            ),
            "applies": {},
        }
        _write_report(report_path, report)
        return report
    finally:
        if redis is not None:
            await redis.aclose()
        await close_database()


async def _run_apply(
    *, operator: str, revision: str, compose_project: str, report_path: Path,
    max_items: int, api_url: str, family: str, confirm: str,
    backup_reference: str,
) -> dict[str, Any]:
    report = _read_report(report_path)
    _validate_dry_run_report(report, operator, revision, max_items)
    if confirm != FAMILY_CONFIRMATIONS[family]:
        raise RetirementSafetyError(
            f"--confirm must be exactly {FAMILY_CONFIRMATIONS[family]}"
        )
    if not backup_reference.strip() or any(
        character in backup_reference for character in "\r\n\x00"
    ):
        raise RetirementSafetyError("backup-reference must identify an approved recovery point")
    applies = report.get("applies")
    if not isinstance(applies, dict):
        raise RetirementSafetyError("dry-run report has invalid applies history")
    if family in applies:
        raise RetirementSafetyError(f"{family} was already applied in this report")
    applied = {name for name in applies if name in FAMILIES}
    redis = None
    try:
        await initialize_database()
        redis = create_redis_client()
        await redis.ping()
        runtime_before = await asyncio.to_thread(collect_runtime_snapshot, api_url)
        if not runtime_before.get("healthy"):
            raise RetirementSafetyError("runtime is not healthy before apply")
        postgres_before = await collect_postgres_invariants()
        inventories = await collect_legacy_inventories(redis, max_items)
        baseline = report.get("inventories")
        if not isinstance(baseline, Mapping):
            raise RetirementSafetyError("dry-run report has no inventories")
        _assert_second_snapshot(baseline, inventories, applied)
        if family == "ia":
            typed = await inventory_legacy_ia_lists(
                redis, cycle_lookup=get_cycles_by_public_ids, max_items=max_items
            )
            removed = await retire_validated_legacy_ia_entries(redis, typed)
        elif family == "audio":
            typed_audio = await inventory_legacy_audio_lists(
                redis, transcription_lookup=_lookup_transcriptions, max_items=max_items
            )
            removed = await retire_validated_legacy_audio_entries(redis, typed_audio)
        else:
            typed_image = await inventory_legacy_image_lists(
                redis, image_lookup=_lookup_images, max_items=max_items
            )
            removed = await retire_validated_legacy_image_entries(redis, typed_image)
        runtime_after = await asyncio.to_thread(collect_runtime_snapshot, api_url)
        postgres_after = await collect_postgres_invariants()
        applies[family] = {
            "captured_at": _utc_now(),
            "operator": operator,
            "revision": revision,
            "compose_project": compose_project,
            "backup_reference": backup_reference,
            "confirmation": confirm,
            "removed_validated_entries": removed,
            "runtime_before": runtime_before,
            "postgres_before": postgres_before,
            "runtime_after": runtime_after,
            "postgres_after": postgres_after,
            "postconditions": {
                "runtime_healthy": bool(runtime_after.get("healthy")),
                "schema_revision": CURRENT_SCHEMA_REVISION,
                "postgres_mutation_by_command": False,
                "provider_calls_by_command": False,
                "other_families_touched": False,
            },
        }
        report["updated_at"] = _utc_now()
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
    parser.add_argument("--compose-project", default="cai")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--family", choices=FAMILIES)
    parser.add_argument("--confirm", default="")
    parser.add_argument(
        "--backup-reference",
        default="",
        help="approved PostgreSQL backup or recovery-point reference; required with --apply",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="write the report without printing its JSON to the terminal",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    operator = _require_metadata(args.operator, "operator")
    revision = _validate_revision(args.revision)
    compose_project = _require_metadata(args.compose_project, "compose-project")
    api_url = _validate_api_url(args.api_url)
    if args.max_items <= 0:
        raise SystemExit("--max-items must be positive")
    if args.apply and args.family is None:
        raise SystemExit("--family is required with --apply")
    if args.dry_run and args.family is not None:
        raise SystemExit("--family is only valid with --apply")
    if args.apply and not args.backup_reference:
        raise SystemExit("--backup-reference is required with --apply")
    if args.dry_run:
        result = asyncio.run(
            _run_dry_run(
                operator=operator,
                revision=revision,
                compose_project=compose_project,
                report_path=args.report,
                max_items=args.max_items,
                api_url=api_url,
            )
        )
    else:
        result = asyncio.run(
            _run_apply(
                operator=operator,
                revision=revision,
                compose_project=compose_project,
                report_path=args.report,
                max_items=args.max_items,
                api_url=api_url,
                family=args.family,
                confirm=args.confirm,
                backup_reference=args.backup_reference,
            )
        )
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
