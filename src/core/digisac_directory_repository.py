"""PostgreSQL persistence for the DigiSac directory cache.

The repository uses the process-local pool owned by ``src.core.db`` while
keeping directory cache, synchronization state, and user-name lookup together.
The database facade re-exports the async operations for existing providers,
workers, and tests.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from psycopg import sql


_DIRECTORY_TABLES = {
    "departments": "digisac_departments",
    "users": "digisac_users",
}
_DIRECTORY_RESOURCES = ("departments", "users")


def _database():
    """Resolve the facade lazily so this repository is import-order safe."""
    from src.core import db

    return db


def _upsert_digisac_directory_sync(
    resource: str, entries: Sequence[Mapping[str, Any]], synced_at: str
) -> int:
    table = _DIRECTORY_TABLES.get(resource)
    if table is None:
        raise ValueError(f"Unsupported DigiSac directory resource: {resource}")
    database = _database()
    rows: list[tuple[str, str, datetime | None, datetime]] = []
    for entry in entries:
        entry_id = entry.get("id")
        name = entry.get("name")
        if not isinstance(entry_id, str) or not entry_id.strip():
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        source_updated_at = entry.get("updatedAt")
        rows.append(
            (
                entry_id.strip(),
                name.strip(),
                database._parse_timestamp(source_updated_at)
                if isinstance(source_updated_at, str)
                else None,
                database._parse_timestamp(synced_at),
            )
        )
    with database.get_database_pool().connection() as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.executemany(
                    sql.SQL(
                        """
                    INSERT INTO {table_name} (
                        id, name, source_updated_at, synced_at
                    )
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        source_updated_at = EXCLUDED.source_updated_at,
                        synced_at = EXCLUDED.synced_at
                    """
                    ).format(table_name=sql.Identifier(table)),
                    rows,
                )
            connection.execute(
                """
                INSERT INTO digisac_directory_sync_state (
                    resource, last_attempt_at, last_success_at
                ) VALUES (%s, %s, %s)
                ON CONFLICT (resource) DO UPDATE SET
                    last_attempt_at = EXCLUDED.last_attempt_at,
                    last_success_at = EXCLUDED.last_success_at
                """,
                (
                    resource,
                    database._parse_timestamp(synced_at),
                    database._parse_timestamp(synced_at),
                ),
            )
    return len(rows)


async def upsert_digisac_directory(
    resource: str, entries: Sequence[Mapping[str, Any]], synced_at: str
) -> int:
    return await asyncio.to_thread(
        _upsert_digisac_directory_sync, resource, entries, synced_at
    )


def _mark_directory_sync_attempt_sync(resource: str, attempted_at: str) -> None:
    database = _database()
    with database.get_database_pool().connection() as connection:
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO digisac_directory_sync_state (resource, last_attempt_at)
                VALUES (%s, %s)
                ON CONFLICT (resource) DO UPDATE SET
                    last_attempt_at = EXCLUDED.last_attempt_at
                """,
                (resource, database._parse_timestamp(attempted_at)),
            )


async def mark_directory_sync_attempt(resource: str, attempted_at: str) -> None:
    await asyncio.to_thread(
        _mark_directory_sync_attempt_sync, resource, attempted_at
    )


def _directory_refresh_is_due_sync(cooldown_seconds: int) -> bool:
    threshold = datetime.now(timezone.utc).timestamp() - cooldown_seconds
    database = _database()
    with database.get_database_pool().connection() as connection:
        rows = connection.execute(
            """
            SELECT resource, last_attempt_at
            FROM digisac_directory_sync_state
            WHERE resource IN ('departments', 'users')
            """
        ).fetchall()
    attempts = {resource: value for resource, value in rows}
    for resource in _DIRECTORY_RESOURCES:
        value = attempts.get(resource)
        if not value:
            return True
        if value.timestamp() <= threshold:
            return True
    return False


async def directory_refresh_is_due(cooldown_seconds: int) -> bool:
    return await asyncio.to_thread(
        _directory_refresh_is_due_sync, cooldown_seconds
    )


def _resolve_user_names_sync(user_ids: Sequence[str]) -> dict[str, str]:
    unique = list(dict.fromkeys(item for item in user_ids if item))
    if not unique:
        return {}
    database = _database()
    with database.get_database_pool().connection() as connection:
        rows = connection.execute(
            """
            SELECT id, name
            FROM digisac_users
            WHERE id = ANY(%s)
            """,
            (unique,),
        ).fetchall()
    return {
        str(user_id): str(name).strip()
        for user_id, name in rows
        if isinstance(name, str) and name.strip()
    }


async def resolve_user_names(user_ids: Sequence[str]) -> dict[str, str]:
    return await asyncio.to_thread(_resolve_user_names_sync, user_ids)
