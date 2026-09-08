"""Controlled preflight and explicitly approved disposal for issue 0056.

The default operation is read-only.  --apply requires an exact reviewed
dry-run report, a listed and disposable-restore-validated PostgreSQL backup,
an unchanged target fingerprint, and the confirmation
"DISPOSE cai-redis-1 cai_redis_data" (or equivalent explicit names).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

import psycopg

ROOT = Path(__file__).resolve().parents[1]
REPORT_VERSION = 1
POSTGRES_IMAGE = "postgres:16.14-alpine"
FORBIDDEN_COMMANDS = (
    "docker volume prune",
    "docker system prune",
    "docker compose down -v",
    "FLUSHDB",
    "FLUSHALL",
)
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


class DisposalSafetyError(RuntimeError):
    """Raised when the exact issue-0056 safety contract is not met."""


@dataclass(frozen=True)
class TargetSnapshot:
    container_name: str
    container_id: str
    container_status: str
    container_project: str
    container_service: str
    mounted_volume: str
    mount_destination: str
    volume_name: str
    volume_mountpoint: str
    volume_project: str
    volume_label: str
    attachments: tuple[tuple[str, str], ...]
    compose_services: tuple[str, ...]
    compose_volumes: tuple[str, ...]

    @property
    def active_attachments(self) -> tuple[tuple[str, str], ...]:
        active_states = {"created", "restarting", "running", "paused"}
        return tuple(
            row for row in self.attachments
            if row[1].lower().split(maxsplit=1)[0] in active_states
        )

    def _base_report(self) -> dict[str, Any]:
        return {
            "container_name": self.container_name,
            "container_id": self.container_id,
            "container_status": self.container_status,
            "container_project": self.container_project,
            "container_service": self.container_service,
            "mounted_volume": self.mounted_volume,
            "mount_destination": self.mount_destination,
            "volume_name": self.volume_name,
            "volume_mountpoint": self.volume_mountpoint,
            "volume_project": self.volume_project,
            "volume_label": self.volume_label,
            "attachments": [
                {"name": name, "state": state} for name, state in self.attachments
            ],
            "active_attachments": [
                {"name": name, "state": state}
                for name, state in self.active_attachments
            ],
            "compose_services": list(self.compose_services),
            "compose_volumes": list(self.compose_volumes),
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self._base_report(), sort_keys=True).encode()
        ).hexdigest()

    def report(self) -> dict[str, Any]:
        result = self._base_report()
        result["fingerprint"] = self.fingerprint()
        return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _name(value: str, label: str) -> str:
    if not SAFE_NAME.fullmatch(value):
        raise DisposalSafetyError(f"invalid {label}: {value!r}")
    return value


def _run(
    command: Sequence[str],
    *,
    input_bytes: bytes | None = None,
    stdout: Any = subprocess.PIPE,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        list(command),
        cwd=ROOT,
        input=input_bytes,
        stdout=stdout,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise DisposalSafetyError(
            f"command failed ({result.returncode}): {command[0]}"
            + (f": {detail[:400]}" if detail else "")
        )
    return result


def _docker(*args: str, input_bytes: bytes | None = None) -> bytes:
    return _run(["docker", *args], input_bytes=input_bytes).stdout or b""


def _compose(project: str, *args: str) -> bytes:
    _name(project, "Compose project")
    return _run(["docker", "compose", "-p", project, *args]).stdout or b""


def _json_inspect(kind: str, name: str) -> dict[str, Any]:
    try:
        value = json.loads(_docker("inspect", name).decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DisposalSafetyError(f"invalid inspect JSON for {kind}") from exc
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise DisposalSafetyError(f"inspect did not resolve exactly one {kind}")
    return cast(dict[str, Any], value[0])


def _lines(raw: bytes) -> tuple[str, ...]:
    return tuple(
        line.strip()
        for line in raw.decode(errors="replace").splitlines()
        if line.strip()
    )


def _volume_attachments(volume: str) -> tuple[tuple[str, str], ...]:
    raw = _docker(
        "ps", "-a", "--filter", f"volume={volume}",
        "--format", "{{.Names}}\t{{.State}}",
    )
    rows: list[tuple[str, str]] = []
    for line in _lines(raw):
        name, sep, state = line.partition("\t")
        if not sep or not name or not state:
            raise DisposalSafetyError("invalid Docker volume attachment row")
        rows.append((name, state))
    return tuple(sorted(rows))


def _compose_inventory(project: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    services = tuple(sorted(_lines(_compose(project, "config", "--services"))))
    volumes = tuple(sorted(_lines(_compose(project, "config", "--volumes"))))
    if "redis" in services:
        raise DisposalSafetyError("current Compose topology still declares Redis")
    return services, volumes


def resolve_target(*, project: str, container: str, volume: str) -> TargetSnapshot:
    """Resolve the exact stopped Redis target using read-only Docker commands."""
    _name(project, "Compose project")
    _name(container, "container")
    _name(volume, "volume")
    container_info = _json_inspect("container", container)
    if str(container_info.get("Name", "")).removeprefix("/") != container:
        raise DisposalSafetyError("container name differs from reviewed target")
    state = container_info.get("State")
    config = container_info.get("Config")
    labels = config.get("Labels", {}) if isinstance(config, Mapping) else {}
    if not isinstance(state, Mapping) or not isinstance(labels, Mapping):
        raise DisposalSafetyError("container inspect lacks state or labels")
    project_label = str(labels.get("com.docker.compose.project", ""))
    service_label = str(labels.get("com.docker.compose.service", ""))
    if project_label != project or service_label != "redis":
        raise DisposalSafetyError("container Compose labels do not match target")
    status = str(state.get("Status", ""))
    if status != "exited":
        raise DisposalSafetyError(f"target container is not stopped: {status!r}")

    mounts = container_info.get("Mounts", [])
    if not isinstance(mounts, list):
        raise DisposalSafetyError("container inspect has invalid mounts")
    volume_mounts = [
        item for item in mounts
        if isinstance(item, Mapping) and item.get("Type") == "volume"
    ]
    if len(volume_mounts) != 1:
        raise DisposalSafetyError("target container must have one volume mount")
    mount = volume_mounts[0]
    if str(mount.get("Name", "")) != volume or str(mount.get("Destination", "")) != "/data":
        raise DisposalSafetyError("container mount does not match target volume at /data")

    volume_info = _json_inspect("volume", volume)
    volume_labels = volume_info.get("Labels", {})
    if str(volume_info.get("Name", "")) != volume or not isinstance(volume_labels, Mapping):
        raise DisposalSafetyError("volume inspect does not match target")
    if str(volume_labels.get("com.docker.compose.project", "")) != project:
        raise DisposalSafetyError("volume project label does not match target")
    if str(volume_labels.get("com.docker.compose.volume", "")) != "redis_data":
        raise DisposalSafetyError("volume label is not redis_data")
    attachments = _volume_attachments(volume)
    if any(name != container for name, _state in attachments):
        raise DisposalSafetyError("volume has an unrelated container attachment")
    services, compose_volumes = _compose_inventory(project)
    if volume in compose_volumes:
        raise DisposalSafetyError("target volume is still declared by Compose")

    return TargetSnapshot(
        container_name=container,
        container_id=str(container_info.get("Id", "")),
        container_status=status,
        container_project=project_label,
        container_service=service_label,
        mounted_volume=str(mount.get("Name", "")),
        mount_destination=str(mount.get("Destination", "")),
        volume_name=str(volume_info.get("Name", "")),
        volume_mountpoint=str(volume_info.get("Mountpoint", "")),
        volume_project=str(volume_labels.get("com.docker.compose.project", "")),
        volume_label=str(volume_labels.get("com.docker.compose.volume", "")),
        attachments=attachments,
        compose_services=services,
        compose_volumes=compose_volumes,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_postgres_backup(project: str, path: Path) -> dict[str, Any]:
    """Write a custom-format dump using the PostgreSQL container environment."""
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "docker", "compose", "-p", project, "exec", "-T", "postgres",
        "sh", "-c", 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom',
    ]
    with path.open("wb") as handle:
        _run(command, stdout=handle)
    size = path.stat().st_size
    if size == 0:
        raise DisposalSafetyError("PostgreSQL backup is empty")
    return {"file_name": path.name, "bytes": size, "sha256": _sha256(path)}


def validate_backup_listing(project: str, path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise DisposalSafetyError(f"backup is missing or empty: {path.name}")
    with path.open("rb") as handle:
        result = _run(
            [
                "docker", "compose", "-p", project, "exec", "-T", "postgres",
                "sh", "-c", 'pg_restore -U "$POSTGRES_USER" --list',
            ],
            input_bytes=handle.read(),
        )
    if not result.stdout:
        raise DisposalSafetyError("pg_restore --list returned no entries")


def validate_backup_in_disposable_postgres(
    path: Path, *, project: str, timeout_seconds: int = 60
) -> None:
    """Restore the dump into an isolated trust-authenticated PostgreSQL container."""
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive")
    name = f"{project}-0056-restore-{os.getpid()}"
    _name(name, "disposable restore container")
    try:
        _docker(
            "run", "--detach", "--rm", "--name", name,
            "--env", "POSTGRES_HOST_AUTH_METHOD=trust",
            "--env", "POSTGRES_DB=cai_restore",
            POSTGRES_IMAGE,
        )
        deadline = time.monotonic() + timeout_seconds
        while True:
            ready = subprocess.run(
                ["docker", "exec", name, "pg_isready", "-U", "postgres", "-d", "cai_restore"],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            if ready.returncode == 0:
                break
            if time.monotonic() >= deadline:
                raise DisposalSafetyError("disposable PostgreSQL did not become ready")
            time.sleep(1)
        with path.open("rb") as handle:
            _run(
                [
                    "docker", "exec", "-i", name, "pg_restore",
                    "-U", "postgres",
                    "--no-owner", "--no-privileges", "--exit-on-error",
                    "--dbname=cai_restore",
                ],
                input_bytes=handle.read(),
            )
        database = _docker(
            "exec", name, "psql", "-U", "postgres", "-d", "cai_restore",
            "-Atqc", "SELECT current_database()",
        ).decode(errors="replace").strip()
        if database != "cai_restore":
            raise DisposalSafetyError("disposable restore database check failed")
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )


def _postgres_report() -> dict[str, Any]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise DisposalSafetyError("DATABASE_URL is required for the PostgreSQL snapshot")
    try:
        with psycopg.connect(database_url, connect_timeout=10) as connection:
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
    except psycopg.Error as exc:
        raise DisposalSafetyError("could not collect PostgreSQL invariant snapshot") from exc
    if row is None:
        raise DisposalSafetyError("PostgreSQL invariant snapshot returned no row")
    return {
        "cycle_statuses": dict(sorted(cycle_statuses.items())),
        "audio_statuses": dict(sorted(audio_statuses.items())),
        "image_statuses": dict(sorted(image_statuses.items())),
        "counts": {
            "cycles": int(row[0]),
            "cycle_publication_markers": int(row[1]),
            "future_cycle_attempts": int(row[2]),
            "active_cycle_leases": int(row[3]),
            "future_audio_attempts": int(row[4]),
            "future_image_attempts": int(row[5]),
            "pending_images": int(row[6]),
            "failed_images": int(row[7]),
            "completed_images": int(row[8]),
        },
    }


def _status_counts(connection: psycopg.Connection[Any], table: str) -> dict[str, int]:
    rows = connection.execute(
        f"SELECT status, count(*) FROM {table} GROUP BY status"
    ).fetchall()
    return {str(status): int(count) for status, count in rows}


def _git_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    return result.stdout.decode(errors="replace").strip() if result.returncode == 0 else "unknown"


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _read_report(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DisposalSafetyError(f"cannot read report: {path.name}") from exc
    if not isinstance(value, dict) or value.get("report_version") != REPORT_VERSION:
        raise DisposalSafetyError("unsupported disposal report")
    return cast(dict[str, Any], value)


def _require_confirmation(value: str | None, container: str, volume: str) -> None:
    expected = f"DISPOSE {container} {volume}"
    if value != expected:
        raise DisposalSafetyError(f"apply requires exact confirmation: {expected!r}")


def _delete_exact_target(project: str, container: str, volume: str) -> dict[str, Any]:
    _docker("rm", "--", container)
    _docker("volume", "rm", "--", volume)
    container_gone = subprocess.run(
        ["docker", "inspect", container],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    ).returncode != 0
    volume_gone = subprocess.run(
        ["docker", "volume", "inspect", volume],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    ).returncode != 0
    if not container_gone or not volume_gone:
        raise DisposalSafetyError("post-disposal inspect still found the target")
    return {
        "project": project,
        "container_removed": container_gone,
        "volume_removed": volume_gone,
        "commands": [f"docker rm -- {container}", f"docker volume rm -- {volume}"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--project", default="cai")
    parser.add_argument("--container", default="cai-redis-1")
    parser.add_argument("--volume", default="cai_redis_data")
    parser.add_argument("--operator", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--validate-restore", action="store_true")
    parser.add_argument("--confirm")
    return parser


def run(args: argparse.Namespace) -> int:
    if not args.operator.strip() or len(args.operator) > 120:
        raise DisposalSafetyError("operator must be a non-empty short label")
    target = resolve_target(
        project=args.project, container=args.container, volume=args.volume
    )
    if target.active_attachments:
        raise DisposalSafetyError("target volume has active attachments")
    postgres_before = _postgres_report()

    if args.dry_run:
        backup: dict[str, Any] | None = None
        if args.backup:
            backup = create_postgres_backup(args.project, args.backup)
            validate_backup_listing(args.project, args.backup)
            backup["pg_restore_listed"] = True
            backup["disposable_restore_validated"] = False
            if args.validate_restore:
                validate_backup_in_disposable_postgres(
                    args.backup, project=args.project
                )
                backup["disposable_restore_validated"] = True
        _write_report(
            args.report,
            {
                "report_version": REPORT_VERSION,
                "operation": "issue-0056-retained-redis-storage",
                "mode": "dry-run",
                "generated_at": _utc_now(),
                "operator": args.operator.strip(),
                "repository_revision": _git_revision(),
                "target": target.report(),
                "postgres": {"before": postgres_before},
                "backup": backup,
                "checks": {
                    "compose_redis_service_absent": "redis" not in target.compose_services,
                    "compose_volume_not_declared": args.volume not in target.compose_volumes,
                    "container_stopped": target.container_status == "exited",
                    "only_reviewed_attachment": not target.active_attachments,
                    "no_disposal_performed": True,
                    "forbidden_commands_not_used": list(FORBIDDEN_COMMANDS),
                },
                "ready_for_apply": bool(
                    backup
                    and backup.get("pg_restore_listed")
                    and backup.get("disposable_restore_validated")
                ),
            },
        )
        return 0

    _require_confirmation(args.confirm, args.container, args.volume)
    if not args.backup:
        raise DisposalSafetyError("apply requires the reviewed backup path")
    report = _read_report(args.report)
    if report.get("mode") != "dry-run" or report.get("ready_for_apply") is not True:
        raise DisposalSafetyError("apply requires a ready dry-run report")
    current_target = target.report()
    reviewed_target = report.get("target")
    if (
        not isinstance(reviewed_target, Mapping)
        or reviewed_target.get("fingerprint") != current_target["fingerprint"]
    ):
        raise DisposalSafetyError("target changed after the reviewed dry-run")
    backup_report = report.get("backup")
    if (
        not isinstance(backup_report, Mapping)
        or backup_report.get("file_name") != args.backup.name
    ):
        raise DisposalSafetyError("backup does not match the reviewed report")
    validate_backup_listing(args.project, args.backup)
    if _sha256(args.backup) != backup_report.get("sha256"):
        raise DisposalSafetyError("backup digest differs from reviewed report")
    result = _delete_exact_target(args.project, args.container, args.volume)
    report.update(
        {
            "mode": "apply",
            "applied_at": _utc_now(),
            "postgres": {"before": postgres_before, "after": _postgres_report()},
            "disposal": result,
            "ready_for_apply": False,
        }
    )
    _write_report(args.report, report)
    return 0


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except (DisposalSafetyError, OSError, ValueError) as exc:
        print(f"disposal safety check failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
