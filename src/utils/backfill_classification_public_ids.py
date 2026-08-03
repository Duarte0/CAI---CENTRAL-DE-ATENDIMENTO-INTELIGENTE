"""Backfill UUIDv7 public identifiers in bounded, restartable batches."""

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime

import psycopg

from src.core.identifiers import uuid7


@dataclass
class BackfillReport:
    missing_before: int
    updated: int
    missing_after: int
    dry_run: bool


def backfill(
    database_url: str,
    *,
    apply: bool = False,
    batch_size: int = 500,
) -> BackfillReport:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    updated = 0
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("SET statement_timeout = '30s'")
        connection.execute("SET lock_timeout = '3s'")
        missing_before = int(
            connection.execute(
                "SELECT COUNT(*) FROM ia_classifications WHERE public_id IS NULL"
            ).fetchone()[0]
        )
        if not apply:
            return BackfillReport(
                missing_before=missing_before,
                updated=0,
                missing_after=missing_before,
                dry_run=True,
            )

        cursor_created_at: datetime | None = None
        cursor_id = 0
        while True:
            rows = connection.execute(
                """
                SELECT id, created_at
                FROM ia_classifications
                WHERE public_id IS NULL
                  AND (
                    %s::timestamptz IS NULL
                    OR (created_at, id) > (%s::timestamptz, %s)
                  )
                ORDER BY created_at, id
                LIMIT %s
                """,
                (
                    cursor_created_at,
                    cursor_created_at,
                    cursor_id,
                    batch_size,
                ),
            ).fetchall()
            if not rows:
                break
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        UPDATE ia_classifications
                        SET public_id = %s
                        WHERE id = %s AND public_id IS NULL
                        """,
                        [(uuid7(), row[0]) for row in rows],
                    )
                    updated += cursor.rowcount
            cursor_id = int(rows[-1][0])
            cursor_created_at = rows[-1][1]

        missing_after = int(
            connection.execute(
                "SELECT COUNT(*) FROM ia_classifications WHERE public_id IS NULL"
            ).fetchone()[0]
        )
    return BackfillReport(
        missing_before=missing_before,
        updated=updated,
        missing_after=missing_after,
        dry_run=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform updates; without this flag the command is read-only.",
    )
    args = parser.parse_args()
    report = backfill(
        args.database_url,
        apply=args.apply,
        batch_size=args.batch_size,
    )
    print(json.dumps(asdict(report), indent=2, sort_keys=True))
    if args.apply and report.missing_after:
        raise SystemExit("public_id backfill is incomplete")


if __name__ == "__main__":
    main()
