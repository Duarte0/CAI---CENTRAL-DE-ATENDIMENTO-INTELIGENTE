"""Populate normalized classification-message rows from legacy JSONB."""

import argparse
import json
from dataclasses import asdict, dataclass

import psycopg

INSPECTION_SQL = """
    WITH expanded AS (
        SELECT c.id, item.value, item.ordinality
        FROM ia_classifications AS c
        CROSS JOIN LATERAL jsonb_array_elements(c.message_ids)
            WITH ORDINALITY AS item(value, ordinality)
    )
    SELECT
        (SELECT COUNT(*) FROM ia_classifications),
        COUNT(*) FILTER (
            WHERE jsonb_typeof(value) = 'string'
              AND btrim(value #>> '{}') <> ''
        ),
        COUNT(*) FILTER (
            WHERE jsonb_typeof(value) <> 'string'
               OR btrim(value #>> '{}') = ''
        ),
        COUNT(*) FILTER (
            WHERE jsonb_typeof(value) = 'string'
              AND btrim(value #>> '{}') <> ''
              AND duplicate_rank > 1
        )
    FROM (
        SELECT *,
            row_number() OVER (
                PARTITION BY id, value ORDER BY ordinality
            ) AS duplicate_rank
        FROM expanded
    ) AS inspected
"""

CONSISTENCY_SQL = """
    WITH expected AS (
        SELECT
            c.id AS classification_id,
            item.message_id,
            MIN(item.ordinality)::integer - 1 AS position,
            c.created_at
        FROM ia_classifications AS c
        CROSS JOIN LATERAL jsonb_array_elements_text(c.message_ids)
            WITH ORDINALITY AS item(message_id, ordinality)
        GROUP BY c.id, item.message_id, c.created_at
    )
    SELECT COUNT(*)
    FROM expected
    FULL OUTER JOIN classification_messages AS actual
      USING (classification_id, message_id)
    WHERE expected.classification_id IS NULL
       OR actual.classification_id IS NULL
       OR expected.position <> actual.position
       OR expected.created_at <> actual.created_at
"""


@dataclass
class BackfillReport:
    classifications: int
    expected_links: int
    existing_links_before: int
    inserted_links: int
    links_after: int
    duplicate_message_ids: int
    invalid_message_values: int
    inconsistent_links: int
    dry_run: bool


def backfill(
    database_url: str,
    *,
    apply: bool = False,
    batch_size: int = 500,
) -> BackfillReport:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    inserted = 0
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("SET statement_timeout = '30s'")
        connection.execute("SET lock_timeout = '3s'")
        summary = connection.execute(INSPECTION_SQL).fetchone()
        classifications = int(summary[0])
        expected_links = int(summary[1]) - int(summary[3])
        invalid_message_values = int(summary[2])
        duplicate_message_ids = int(summary[3])
        existing_before = int(
            connection.execute(
                "SELECT COUNT(*) FROM classification_messages"
            ).fetchone()[0]
        )
        if invalid_message_values:
            raise RuntimeError(
                "message_ids contains non-string values; correct them before backfill"
            )
        if apply:
            last_id = 0
            while True:
                ids = [
                    int(row[0])
                    for row in connection.execute(
                        """
                        SELECT id
                        FROM ia_classifications
                        WHERE id > %s
                        ORDER BY id
                        LIMIT %s
                        """,
                        (last_id, batch_size),
                    ).fetchall()
                ]
                if not ids:
                    break
                with connection.transaction():
                    cursor = connection.execute(
                        """
                        INSERT INTO classification_messages (
                            classification_id, message_id, position, created_at
                        )
                        SELECT
                            c.id,
                            item.message_id,
                            item.ordinality::integer - 1,
                            c.created_at
                        FROM ia_classifications AS c
                        CROSS JOIN LATERAL jsonb_array_elements_text(c.message_ids)
                            WITH ORDINALITY AS item(message_id, ordinality)
                        WHERE c.id = ANY(%s)
                        ON CONFLICT DO NOTHING
                        """,
                        (ids,),
                    )
                    inserted += cursor.rowcount
                last_id = ids[-1]
            # Include rows written concurrently by the compatible application
            # in the final report instead of comparing against a stale count.
            summary = connection.execute(INSPECTION_SQL).fetchone()
            classifications = int(summary[0])
            expected_links = int(summary[1]) - int(summary[3])
            invalid_message_values = int(summary[2])
            duplicate_message_ids = int(summary[3])
        links_after = int(
            connection.execute(
                "SELECT COUNT(*) FROM classification_messages"
            ).fetchone()[0]
        )
        inconsistent_links = int(connection.execute(CONSISTENCY_SQL).fetchone()[0])
    return BackfillReport(
        classifications=classifications,
        expected_links=expected_links,
        existing_links_before=existing_before,
        inserted_links=inserted,
        links_after=links_after,
        duplicate_message_ids=duplicate_message_ids,
        invalid_message_values=invalid_message_values,
        inconsistent_links=inconsistent_links,
        dry_run=not apply,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform inserts; without this flag the command is read-only.",
    )
    args = parser.parse_args()
    report = backfill(
        args.database_url,
        apply=args.apply,
        batch_size=args.batch_size,
    )
    print(json.dumps(asdict(report), indent=2, sort_keys=True))
    if args.apply and (
        report.links_after != report.expected_links or report.inconsistent_links
    ):
        raise SystemExit("classification_messages backfill is inconsistent")


if __name__ == "__main__":
    main()
