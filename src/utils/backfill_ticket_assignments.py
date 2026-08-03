"""Backfill department and agent names without invoking the IA.

The DigiSac ticket endpoint exposes the ticket's current ``departmentId`` and
``userId``.  Historical transfer events are not reconstructed by this command;
it fills only empty assignment fields and preserves every IA-produced column.
"""

import argparse
import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any, Mapping

import psycopg
from psycopg.types.json import Jsonb
import requests

from src.core.config import settings


logger = logging.getLogger(__name__)
TRANSIENT_STATUSES = {408, 425, 429, 500, 502, 503, 504}


@dataclass
class BackfillReport:
    scanned: int = 0
    would_update: int = 0
    updated: int = 0
    unchanged: int = 0
    not_found: int = 0
    unresolved: int = 0
    errors: int = 0


def _get_json(
    url: str, *, params: Mapping[str, Any] | None = None
) -> Mapping[str, Any]:
    if not settings.digisac_api_key:
        raise RuntimeError("DIGISAC_API_KEY is not configured")
    headers = {"Authorization": f"Bearer {settings.digisac_api_key}"}
    attempts = max(1, settings.digisac_directory_max_retries)
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=settings.digisac_directory_timeout_seconds,
            )
            if response.status_code == 404:
                raise LookupError("DigiSac resource was not found")
            if response.status_code in TRANSIENT_STATUSES:
                raise requests.RequestException(
                    f"transient DigiSac HTTP {response.status_code}"
                )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise ValueError("DigiSac response must be an object")
            return payload
        except (requests.Timeout, requests.ConnectionError, requests.RequestException):
            if attempt == attempts:
                raise
            time.sleep(min(2 ** (attempt - 1), 4))
    raise RuntimeError("unreachable")


def _fetch_directory(resource: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    page = 1
    last_page = 1
    base_url = settings.digisac_api_base_url.rstrip("/")
    while page <= last_page:
        payload = _get_json(f"{base_url}/{resource}", params={"page": page})
        data = payload.get("data")
        current_page = payload.get("currentPage")
        raw_last_page = payload.get("lastPage")
        if (
            not isinstance(data, list)
            or not isinstance(current_page, int)
            or not isinstance(raw_last_page, int)
        ):
            raise ValueError(f"Invalid DigiSac {resource} pagination")
        for item in data:
            if not isinstance(item, Mapping):
                continue
            item_id = item.get("id")
            name = item.get("name")
            if isinstance(item_id, str) and isinstance(name, str) and name.strip():
                entries[item_id] = name.strip()
        last_page = max(current_page, raw_last_page)
        page = current_page + 1
    return entries


def backfill(database_url: str, *, apply: bool = False) -> BackfillReport:
    """Fill empty assignment fields from the current DigiSac ticket state."""
    departments = _fetch_directory("departments")
    users = _fetch_directory("users")
    report = BackfillReport()
    # Read and close before any HTTP call so a slow DigiSac scan cannot leave an
    # idle PostgreSQL transaction holding an old snapshot.
    with psycopg.connect(database_url, autocommit=True) as connection:
        rows = connection.execute(
            """
            SELECT conversation_id,
                   BOOL_OR(department <> '[]'::jsonb) AS has_department,
                   BOOL_OR(agent <> '[]'::jsonb) AS has_agent
            FROM ia_classifications
            GROUP BY conversation_id
            ORDER BY conversation_id
            """
        ).fetchall()
    updates: list[tuple[str | None, str | None, str]] = []
    for row in rows:
        report.scanned += 1
        if row[1] and row[2]:
            report.unchanged += 1
            continue
        conversation_id = row[0]
        try:
            ticket = _get_json(
                f"{settings.digisac_api_base_url.rstrip('/')}/tickets/"
                f"{conversation_id}"
            )
        except LookupError:
            report.not_found += 1
            continue
        except Exception:
            logger.exception(
                "Failed to fetch DigiSac ticket %s", conversation_id
            )
            report.errors += 1
            continue
        department = departments.get(ticket.get("departmentId"))
        agent = users.get(ticket.get("userId"))
        needs_department = not row[1]
        needs_agent = not row[2]
        can_update = (needs_department and department) or (needs_agent and agent)
        if (needs_department and not department) or (needs_agent and not agent):
            report.unresolved += 1
        if not can_update:
            continue
        updates.append((department, agent, conversation_id))
        report.would_update += 1

    if apply and updates:
        with psycopg.connect(database_url) as connection:
            connection.execute("SET LOCAL statement_timeout = '30s'")
            connection.execute("SET LOCAL lock_timeout = '3s'")
            with connection.transaction():
                for department, agent, conversation_id in updates:
                    cursor = connection.execute(
                        """
                        UPDATE ia_classifications
                        SET department = CASE WHEN department = '[]'::jsonb
                                THEN COALESCE(%s, '[]'::jsonb) ELSE department END,
                            agent = CASE WHEN agent = '[]'::jsonb
                                THEN COALESCE(%s, '[]'::jsonb) ELSE agent END,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE conversation_id = %s
                          AND (department = '[]'::jsonb OR agent = '[]'::jsonb)
                        """,
                        (
                            Jsonb([department]) if department else None,
                            Jsonb([agent]) if agent else None,
                            conversation_id,
                        ),
                    )
                    report.updated += cursor.rowcount
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=settings.database_url, required=not bool(settings.database_url))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write changes in a transaction; back up PostgreSQL before applying",
    )
    args = parser.parse_args()
    report = backfill(args.database_url, apply=args.apply)
    print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
