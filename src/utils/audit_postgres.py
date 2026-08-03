"""Produce a read-only PostgreSQL health report without row-level data."""

import argparse
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def audit(database_url: str) -> dict[str, Any]:
    with psycopg.connect(
        database_url,
        autocommit=True,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=15000",
    ) as connection:
        version = connection.execute(
            """
            SELECT current_database() AS database,
                   current_setting('server_version') AS server_version,
                   pg_database_size(current_database()) AS database_bytes
            """
        ).fetchone()
        tables = connection.execute(
            """
            SELECT
                relname AS table,
                n_live_tup,
                n_dead_tup,
                seq_scan,
                idx_scan,
                n_tup_ins,
                n_tup_upd,
                n_tup_del,
                last_analyze,
                last_autoanalyze,
                pg_total_relation_size(relid) AS total_bytes
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC, relname
            """
        ).fetchall()
        indexes = connection.execute(
            """
            SELECT
                ui.relname AS table,
                ui.indexrelname AS index,
                ui.idx_scan,
                pg_relation_size(ui.indexrelid) AS index_bytes,
                pi.indisunique,
                pi.indisvalid,
                pi.indisready
            FROM pg_stat_user_indexes AS ui
            JOIN pg_index AS pi ON pi.indexrelid = ui.indexrelid
            ORDER BY ui.relname, ui.indexrelname
            """
        ).fetchall()
        constraints = connection.execute(
            """
            SELECT
                conrelid::regclass::text AS table,
                conname AS constraint,
                contype AS type,
                convalidated AS validated
            FROM pg_constraint
            WHERE connamespace = current_schema()::regnamespace
            ORDER BY conrelid::regclass::text, conname
            """
        ).fetchall()
        activity = connection.execute(
            """
            SELECT state, wait_event_type, COUNT(*) AS sessions
            FROM pg_stat_activity
            WHERE datname = current_database()
            GROUP BY state, wait_event_type
            ORDER BY state NULLS LAST, wait_event_type NULLS LAST
            """
        ).fetchall()
    return {
        "database": dict(version),
        "tables": [dict(row) for row in tables],
        "indexes": [dict(row) for row in indexes],
        "constraints": [dict(row) for row in constraints],
        "activity": [dict(row) for row in activity],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            audit(args.database_url),
            default=_json_default,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
