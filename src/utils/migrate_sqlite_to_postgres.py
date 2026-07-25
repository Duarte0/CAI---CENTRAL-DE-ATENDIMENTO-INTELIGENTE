"""Copy the legacy SQLite persistence into an empty PostgreSQL database.

The source is opened read-only.  The destination must already have the
Alembic schema and must be empty; this prevents accidental overwrites or
duplicates during a cutover.  The SQLite file is never changed.
"""

import argparse
import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from src.core.db import EXPECTED_SCHEMA_REVISION

logger = logging.getLogger(__name__)

TABLES: dict[str, tuple[str, ...]] = {
    "ia_classifications": ("id",),
    "message_transcriptions": ("message_id",),
    "message_image_extractions": ("message_id",),
    "ticket_assignment_history": ("id",),
    "ticket_assignment_event_keys": ("event_key",),
    "digisac_departments": ("id",),
    "digisac_users": ("id",),
    "digisac_directory_sync_state": ("resource",),
}
JSON_COLUMNS = {
    "ia_classifications": {"message_ids", "department", "agent"},
}
TIMESTAMP_COLUMNS = {
    "ia_classifications": {
        "created_at", "reviewed_at", "updated_at"
    },
    "message_transcriptions": {"created_at", "updated_at", "completed_at"},
    "message_image_extractions": {"created_at", "updated_at", "completed_at"},
    "ticket_assignment_history": {"event_timestamp", "created_at"},
    "ticket_assignment_event_keys": {"created_at"},
    "digisac_departments": {"source_updated_at", "synced_at"},
    "digisac_users": {"source_updated_at", "synced_at"},
    "digisac_directory_sync_state": {"last_attempt_at", "last_success_at"},
}


@dataclass
class TableReport:
    source: int = 0
    destination: int = 0
    ids_missing: int = 0
    ids_extra: int = 0
    json_invalid: int = 0
    rows_mismatched: int = 0
    status: str = "PENDING"


@dataclass
class MigrationReport:
    tables: dict[str, TableReport] = field(default_factory=dict)
    normalized_naive_timestamps: int = 0


def _timestamp(value: Any, report: MigrationReport) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"timestamp is not text: {value!r}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        report.normalized_naive_timestamps += 1
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_value(table: str, column: str, value: Any) -> Jsonb | None:
    if value is None:
        return None
    if not isinstance(value, str):
        parsed = value
    else:
        parsed = json.loads(value)
    if table == "ia_classifications" and column in {"message_ids", "department", "agent"}:
        if not isinstance(parsed, list):
            raise ValueError(f"{table}.{column} must contain a JSON list")
    return Jsonb(parsed)


def _sqlite_rows(path: Path) -> dict[str, list[tuple[list[str], list[Any]]]]:
    uri = f"file:{path.resolve()}?mode=ro"
    result: dict[str, list[tuple[list[str], list[Any]]]] = {}
    with sqlite3.connect(uri, uri=True) as connection:
        for table in TABLES:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not exists:
                raise RuntimeError(f"SQLite source is missing table {table}")
            cursor = connection.execute(f'SELECT * FROM "{table}"')
            columns = [item[0] for item in cursor.description]
            result[table] = [(columns, list(row)) for row in cursor.fetchall()]
    return result


def _destination_is_empty(connection: psycopg.Connection[Any]) -> None:
    nonempty: list[str] = []
    for table in TABLES:
        count = connection.execute(
            sql.SQL("SELECT COUNT(*) FROM {};").format(sql.Identifier(table))
        ).fetchone()[0]
        if count:
            nonempty.append(f"{table}={count}")
    if nonempty:
        raise RuntimeError(
            "PostgreSQL destination is not empty; refusing migration: "
            + ", ".join(nonempty)
        )


def _prepare_rows(
    source: dict[str, list[tuple[list[str], list[Any]]]],
    report: MigrationReport,
) -> dict[str, list[tuple[list[str], list[Any]]]]:
    prepared: dict[str, list[tuple[list[str], list[Any]]]] = {}
    for table, table_rows in source.items():
        prepared_rows: list[tuple[list[str], list[Any]]] = []
        for columns, values in table_rows:
            converted: list[Any] = []
            for column, value in zip(columns, values):
                try:
                    if column in JSON_COLUMNS.get(table, set()):
                        converted.append(_json_value(table, column, value))
                    elif column in TIMESTAMP_COLUMNS.get(table, set()):
                        converted.append(_timestamp(value, report))
                    else:
                        converted.append(value)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    report.tables.setdefault(table, TableReport()).json_invalid += int(
                        column in JSON_COLUMNS.get(table, set())
                    )
                    raise RuntimeError(
                        f"Invalid value in {table}.{column}: {exc}"
                    ) from exc
            prepared_rows.append((columns, converted))
        prepared[table] = prepared_rows
        report.tables.setdefault(table, TableReport()).source = len(prepared_rows)
    return prepared


def _copy_rows(
    connection: psycopg.Connection[Any],
    prepared: dict[str, list[tuple[list[str], list[Any]]]],
) -> None:
    for table, table_rows in prepared.items():
        for columns, values in table_rows:
            statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table),
                sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                sql.SQL(", ").join(sql.Placeholder() for _ in columns),
            )
            connection.execute(statement, values)


def _reset_sequences(connection: psycopg.Connection[Any]) -> None:
    """Advance identity sequences past explicitly restored historical IDs."""
    for table in ("ia_classifications", "ticket_assignment_history"):
        sequence = connection.execute(
            "SELECT pg_get_serial_sequence(%s, 'id')", (table,)
        ).fetchone()[0]
        if not sequence:
            continue
        max_id = connection.execute(
            sql.SQL("SELECT MAX(id) FROM {};").format(sql.Identifier(table))
        ).fetchone()[0]
        if max_id is not None:
            connection.execute("SELECT setval(%s, %s, true)", (sequence, max_id))


def _validate(
    connection: psycopg.Connection[Any],
    prepared: dict[str, list[tuple[list[str], list[Any]]]],
    report: MigrationReport,
) -> None:
    for table, table_rows in prepared.items():
        keys = TABLES[table]
        source_columns = table_rows[0][0] if table_rows else list(keys)
        source_by_key = {
            tuple(row[source_columns.index(key)] for key in keys): row
            for columns, row in table_rows
        }
        target_columns = sql.SQL(", ").join(
            sql.Identifier(column) for column in source_columns
        )
        target_rows = connection.execute(
            sql.SQL("SELECT {} FROM {};").format(
                target_columns, sql.Identifier(table)
            )
        ).fetchall()
        target_by_key = {
            tuple(row[source_columns.index(key)] for key in keys): row
            for row in target_rows
        }
        table_report = report.tables[table]
        table_report.destination = len(target_rows)
        table_report.ids_missing = len(set(source_by_key) - set(target_by_key))
        table_report.ids_extra = len(set(target_by_key) - set(source_by_key))
        for key in set(source_by_key) & set(target_by_key):
            if [_normal(value) for value in source_by_key[key]] != [
                _normal(value) for value in target_by_key[key]
            ]:
                table_report.rows_mismatched += 1
        table_report.status = (
            "OK"
            if (
                len(source_by_key) == len(target_by_key)
                and not table_report.ids_missing
                and not table_report.ids_extra
                and not table_report.rows_mismatched
            )
            else "DIVERGENCE"
        )


def _normal(value: Any) -> Any:
    """Canonicalize values returned by SQLite and PostgreSQL for comparison."""
    if isinstance(value, Jsonb):
        return value.obj
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def migrate(sqlite_path: Path, database_url: str) -> MigrationReport:
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"SQLite source not found: {sqlite_path}")
    source = _sqlite_rows(sqlite_path)
    report = MigrationReport()
    prepared = _prepare_rows(source, report)
    with psycopg.connect(database_url) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version LIMIT 1"
        ).fetchone()
        if not revision or revision[0] != EXPECTED_SCHEMA_REVISION:
            raise RuntimeError(
                f"PostgreSQL must be migrated to {EXPECTED_SCHEMA_REVISION} first"
            )
        _destination_is_empty(connection)
        with connection.transaction():
            _copy_rows(connection, prepared)
            _reset_sequences(connection)
            _validate(connection, prepared, report)
            if any(item.status != "OK" for item in report.tables.values()):
                raise RuntimeError("Migration validation found divergent IDs")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-path", type=Path, required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = migrate(args.sqlite_path, args.database_url)
    payload = asdict(report)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
