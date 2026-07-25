"""PostgreSQL persistence for the CAI pipeline.

The module keeps the existing async-facing API used by FastAPI and the workers,
while using one thread-safe synchronous psycopg pool per process.  Schema
creation is deliberately not performed here; deploys must apply Alembic
migrations before starting the application.
"""

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from src.core.config import settings
from src.core.intents import normalize_intent_type

logger = logging.getLogger(__name__)
EXPECTED_SCHEMA_REVISION = "0001_initial"
_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _parse_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"Invalid timestamp value: {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row_dict(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return {key: _iso(value) for key, value in row.items()} if row else None


def _require_database_url() -> str:
    value = settings.database_url
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(
            "DATABASE_URL is required; apply migrations and configure the "
            "PostgreSQL connection before starting the CAI"
        )
    return value.strip()


def _get_pool() -> ConnectionPool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialized")
    return _pool


def _open_pool_sync() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            return
        conninfo = _require_database_url()
        pool = ConnectionPool(
            conninfo=conninfo,
            min_size=settings.database_pool_min_size,
            max_size=settings.database_pool_max_size,
            timeout=settings.database_pool_timeout_seconds,
            open=False,
        )
        try:
            pool.open(wait=True, timeout=settings.database_pool_timeout_seconds)
        except Exception:
            pool.close()
            logger.exception("Failed to open PostgreSQL connection pool")
            raise RuntimeError("Unable to connect to PostgreSQL") from None
        _pool = pool
        logger.info(
            "PostgreSQL pool opened: min_size=%s max_size=%s",
            settings.database_pool_min_size,
            settings.database_pool_max_size,
        )


def _verify_schema_sync() -> None:
    pool = _get_pool()
    try:
        with pool.connection() as connection:
            row = connection.execute(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).fetchone()
    except psycopg.Error:
        logger.exception("PostgreSQL schema verification failed")
        raise RuntimeError(
            "PostgreSQL is reachable but its migrations are not available"
        ) from None
    if not row or row[0] != EXPECTED_SCHEMA_REVISION:
        actual = row[0] if row else "none"
        raise RuntimeError(
            f"Unsupported PostgreSQL schema revision {actual!r}; "
            f"expected {EXPECTED_SCHEMA_REVISION!r}"
        )


def _initialize_database_sync() -> None:
    _open_pool_sync()
    try:
        _verify_schema_sync()
    except Exception:
        _close_database_sync()
        raise


async def initialize_database() -> None:
    """Open the process-local pool and verify that migrations are installed."""
    await asyncio.to_thread(_initialize_database_sync)


def _close_database_sync() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close(timeout=settings.database_pool_timeout_seconds)
            _pool = None
            logger.info("PostgreSQL pool closed")


async def close_database() -> None:
    await asyncio.to_thread(_close_database_sync)


def _ping_database_sync() -> bool:
    try:
        with _get_pool().connection() as connection:
            connection.execute("SELECT 1").fetchone()
        return True
    except Exception:
        logger.exception("PostgreSQL healthcheck failed")
        return False


async def database_is_ready() -> bool:
    return await asyncio.to_thread(_ping_database_sync)


def _record_ticket_assignment_sync(
    *,
    conversation_id: str,
    department_id: str | None,
    user_id: str | None,
    event_timestamp: str,
    event_key: str,
    source_event_id: str | None = None,
    ticket_transfer_count: int | None = None,
) -> bool:
    if department_id is None and user_id is None:
        return False
    now = datetime.now(timezone.utc)
    with _get_pool().connection() as connection:
        with connection.transaction():
            inserted = connection.execute(
                """
                INSERT INTO ticket_assignment_event_keys (
                    event_key, conversation_id, created_at
                ) VALUES (%s, %s, %s)
                ON CONFLICT (event_key) DO NOTHING
                RETURNING event_key
                """,
                (event_key, conversation_id, now),
            ).fetchone()
            if inserted is None:
                return False
            previous = connection.execute(
                """
                SELECT department_id, user_id
                FROM ticket_assignment_history
                WHERE conversation_id = %s
                ORDER BY event_timestamp DESC, id DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
            if previous == (department_id, user_id):
                return False
            connection.execute(
                """
                INSERT INTO ticket_assignment_history (
                    conversation_id, department_id, user_id, event_timestamp,
                    source_event_id, event_key, ticket_transfer_count, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_key) DO NOTHING
                """,
                (
                    conversation_id,
                    department_id,
                    user_id,
                    _parse_timestamp(event_timestamp),
                    source_event_id,
                    event_key,
                    ticket_transfer_count,
                    now,
                ),
            )
            return True


async def record_ticket_assignment(**kwargs: Any) -> bool:
    return await asyncio.to_thread(_record_ticket_assignment_sync, **kwargs)


def _upsert_digisac_directory_sync(
    resource: str, entries: Sequence[Mapping[str, Any]], synced_at: str
) -> int:
    table = {"departments": "digisac_departments", "users": "digisac_users"}.get(
        resource
    )
    if table is None:
        raise ValueError(f"Unsupported DigiSac directory resource: {resource}")
    rows = []
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
                _parse_timestamp(source_updated_at)
                if isinstance(source_updated_at, str)
                else None,
                _parse_timestamp(synced_at),
            )
        )
    with _get_pool().connection() as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.executemany(
                    f"""
                    INSERT INTO {table} (id, name, source_updated_at, synced_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        source_updated_at = EXCLUDED.source_updated_at,
                        synced_at = EXCLUDED.synced_at
                    """,
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
                (resource, _parse_timestamp(synced_at), _parse_timestamp(synced_at)),
            )
    return len(rows)


async def upsert_digisac_directory(
    resource: str, entries: Sequence[Mapping[str, Any]], synced_at: str
) -> int:
    return await asyncio.to_thread(
        _upsert_digisac_directory_sync, resource, entries, synced_at
    )


def _mark_directory_sync_attempt_sync(resource: str, attempted_at: str) -> None:
    with _get_pool().connection() as connection:
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO digisac_directory_sync_state (resource, last_attempt_at)
                VALUES (%s, %s)
                ON CONFLICT (resource) DO UPDATE SET
                    last_attempt_at = EXCLUDED.last_attempt_at
                """,
                (resource, _parse_timestamp(attempted_at)),
            )


async def mark_directory_sync_attempt(resource: str, attempted_at: str) -> None:
    await asyncio.to_thread(
        _mark_directory_sync_attempt_sync, resource, attempted_at
    )


def _directory_refresh_is_due_sync(cooldown_seconds: int) -> bool:
    threshold = datetime.now(timezone.utc).timestamp() - cooldown_seconds
    with _get_pool().connection() as connection:
        rows = connection.execute(
            """
            SELECT resource, last_attempt_at
            FROM digisac_directory_sync_state
            WHERE resource IN ('departments', 'users')
            """
        ).fetchall()
    attempts = {resource: value for resource, value in rows}
    for resource in ("departments", "users"):
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


def _resolve_ticket_assignments_sync(
    conversation_id: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    with _get_pool().connection() as connection:
        rows = connection.execute(
            """
            SELECT h.department_id, d.name, h.user_id, u.name
            FROM ticket_assignment_history AS h
            LEFT JOIN digisac_departments AS d ON d.id = h.department_id
            LEFT JOIN digisac_users AS u ON u.id = h.user_id
            WHERE h.conversation_id = %s
            ORDER BY h.event_timestamp ASC, h.id ASC
            """,
            (conversation_id,),
        ).fetchall()
    departments: list[str] = []
    agents: list[str] = []
    unresolved_departments: list[str] = []
    unresolved_users: list[str] = []
    seen_departments: set[str] = set()
    seen_agents: set[str] = set()
    seen_unresolved_departments: set[str] = set()
    seen_unresolved_users: set[str] = set()
    for department_id, department_name, user_id, user_name in rows:
        if isinstance(department_name, str) and department_name.strip():
            normalized = department_name.strip()
            if normalized not in seen_departments:
                seen_departments.add(normalized)
                departments.append(normalized)
        elif department_id and department_id not in seen_unresolved_departments:
            seen_unresolved_departments.add(department_id)
            unresolved_departments.append(department_id)
        if isinstance(user_name, str) and user_name.strip():
            normalized = user_name.strip()
            if normalized not in seen_agents:
                seen_agents.add(normalized)
                agents.append(normalized)
        elif user_id and user_id not in seen_unresolved_users:
            seen_unresolved_users.add(user_id)
            unresolved_users.append(user_id)
    return departments, agents, unresolved_departments, unresolved_users


async def resolve_ticket_assignments(
    conversation_id: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    return await asyncio.to_thread(
        _resolve_ticket_assignments_sync, conversation_id
    )


def _intent_type(result: Mapping[str, Any]) -> str:
    raw_intent_type = result.get("intent_type")
    intent_type = normalize_intent_type(raw_intent_type)
    if intent_type is None:
        logger.warning(
            "Invalid intent_type %r while persisting classification; using 'other'",
            raw_intent_type,
        )
        return "other"
    return intent_type


def _structured_name_list(result: Mapping[str, Any], field: str) -> list[str]:
    value = result.get(field)
    if not isinstance(value, (list, tuple)):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _insert_classification_sync(
    *,
    conversation_id: str,
    message_ids: Sequence[str],
    created_at: str,
    full_context: str,
    message_count: int,
    result: Mapping[str, Any],
    model: str,
    processing_time_ms: int,
    prompt_version: str,
    protocol: str | None = None,
) -> int:
    with _get_pool().connection() as connection:
        with connection.transaction():
            row = connection.execute(
                """
                INSERT INTO ia_classifications (
                    conversation_id, message_ids, created_at, full_context,
                    message_count, intent_type, confidence, title, protocol,
                    description, department, agent, model, processing_time_ms,
                    prompt_version, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    conversation_id,
                    Jsonb(list(message_ids)),
                    _parse_timestamp(created_at),
                    full_context,
                    message_count,
                    _intent_type(result),
                    result.get("confidence"),
                    result.get("title"),
                    protocol,
                    result.get("description"),
                    Jsonb(_structured_name_list(result, "department")),
                    Jsonb(_structured_name_list(result, "agent")),
                    model,
                    processing_time_ms,
                    prompt_version,
                    _parse_timestamp(created_at),
                ),
            ).fetchone()
    if row is None:
        raise RuntimeError("PostgreSQL did not return a classification id")
    return int(row[0])


async def insert_classification(**kwargs: Any) -> int:
    return await asyncio.to_thread(_insert_classification_sync, **kwargs)


def _update_analysis_protocol_sync(conversation_id: str, protocol: str) -> bool:
    with _get_pool().connection() as connection:
        with connection.transaction():
            cursor = connection.execute(
                """
                UPDATE ia_classifications
                SET protocol = %s, updated_at = CURRENT_TIMESTAMP
                WHERE conversation_id = %s AND protocol IS DISTINCT FROM %s
                """,
                (protocol, conversation_id, protocol),
            )
            if cursor.rowcount:
                return True
            return (
                connection.execute(
                    "SELECT 1 FROM ia_classifications WHERE conversation_id = %s LIMIT 1",
                    (conversation_id,),
                ).fetchone()
                is not None
            )


async def update_analysis_protocol(conversation_id: str, protocol: str) -> bool:
    return await asyncio.to_thread(
        _update_analysis_protocol_sync, conversation_id, protocol
    )


def _classification_exists_sync(conversation_id: str, created_at: str) -> bool:
    with _get_pool().connection() as connection:
        return (
            connection.execute(
                """
                SELECT 1 FROM ia_classifications
                WHERE conversation_id = %s AND created_at = %s
                LIMIT 1
                """,
                (conversation_id, _parse_timestamp(created_at)),
            ).fetchone()
            is not None
        )


async def classification_exists(conversation_id: str, created_at: str) -> bool:
    return await asyncio.to_thread(
        _classification_exists_sync, conversation_id, created_at
    )


def _ticket_has_classification_sync(conversation_id: str) -> bool:
    with _get_pool().connection() as connection:
        return (
            connection.execute(
                "SELECT 1 FROM ia_classifications WHERE conversation_id = %s LIMIT 1",
                (conversation_id,),
            ).fetchone()
            is not None
        )


async def ticket_has_classification(conversation_id: str) -> bool:
    return await asyncio.to_thread(_ticket_has_classification_sync, conversation_id)


def _reserve_content_sync(
    table: str, message_id: str, conversation_id: str | None, model: str
) -> bool:
    if table not in {"message_transcriptions", "message_image_extractions"}:
        raise ValueError("Unsupported content table")
    now = datetime.now(timezone.utc)
    with _get_pool().connection() as connection:
        with connection.transaction():
            row = connection.execute(
                f"""
                INSERT INTO {table} (
                    message_id, conversation_id, model, status, created_at, updated_at
                ) VALUES (%s, %s, %s, 'pending', %s, %s)
                ON CONFLICT (message_id) DO UPDATE SET
                    conversation_id = COALESCE(EXCLUDED.conversation_id,
                                               {table}.conversation_id),
                    model = EXCLUDED.model,
                    status = 'pending', error_message = NULL,
                    updated_at = EXCLUDED.updated_at
                WHERE {table}.status = 'failed'
                RETURNING message_id
                """,
                (message_id, conversation_id, model, now, now),
            ).fetchone()
            return row is not None


async def reserve_transcription(
    message_id: str, conversation_id: str | None, model: str
) -> bool:
    return await asyncio.to_thread(
        _reserve_content_sync, "message_transcriptions", message_id, conversation_id, model
    )


def _set_content_status_sync(
    table: str,
    message_id: str,
    status: str,
    *,
    text: str | None = None,
    error_message: str | None = None,
    increment_attempt: bool = False,
) -> None:
    if table not in {"message_transcriptions", "message_image_extractions"}:
        raise ValueError("Unsupported content table")
    completed_at = datetime.now(timezone.utc) if status == "completed" else None
    with _get_pool().connection() as connection:
        with connection.transaction():
            connection.execute(
                f"""
                UPDATE {table}
                SET status = %s, text = COALESCE(%s, text), error_message = %s,
                    attempt_count = attempt_count + %s,
                    updated_at = CURRENT_TIMESTAMP, completed_at = %s
                WHERE message_id = %s
                """,
                (status, text, error_message, int(increment_attempt), completed_at, message_id),
            )


async def set_transcription_status(
    message_id: str,
    status: str,
    *,
    text: str | None = None,
    error_message: str | None = None,
    increment_attempt: bool = False,
) -> None:
    await asyncio.to_thread(
        _set_content_status_sync,
        "message_transcriptions",
        message_id,
        status,
        text=text,
        error_message=error_message,
        increment_attempt=increment_attempt,
    )


def _get_content_sync(table: str, message_id: str) -> dict[str, Any] | None:
    if table not in {"message_transcriptions", "message_image_extractions"}:
        raise ValueError("Unsupported content table")
    with _get_pool().connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute(
                f"SELECT * FROM {table} WHERE message_id = %s", (message_id,)
            ).fetchone()
    return _row_dict(row)


async def get_transcription(message_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_get_content_sync, "message_transcriptions", message_id)


def _get_completed_content_sync(table: str, message_ids: Sequence[str]) -> dict[str, str]:
    if table not in {"message_transcriptions", "message_image_extractions"}:
        raise ValueError("Unsupported content table")
    unique_ids = list(dict.fromkeys(message_ids))
    if not unique_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(unique_ids))
    with _get_pool().connection() as connection:
        rows = connection.execute(
            f"""
            SELECT message_id, text FROM {table}
            WHERE status = 'completed' AND text IS NOT NULL
              AND message_id IN ({placeholders})
            """,
            unique_ids,
        ).fetchall()
    return {
        str(message_id): text.strip()
        for message_id, text in rows
        if isinstance(text, str) and text.strip()
    }


async def get_completed_transcriptions(message_ids: Sequence[str]) -> dict[str, str]:
    return await asyncio.to_thread(
        _get_completed_content_sync, "message_transcriptions", message_ids
    )


async def reserve_image_extraction(
    message_id: str, conversation_id: str | None, model: str
) -> bool:
    return await asyncio.to_thread(
        _reserve_content_sync, "message_image_extractions", message_id, conversation_id, model
    )


async def set_image_extraction_status(
    message_id: str,
    status: str,
    *,
    text: str | None = None,
    error_message: str | None = None,
    increment_attempt: bool = False,
) -> None:
    await asyncio.to_thread(
        _set_content_status_sync,
        "message_image_extractions",
        message_id,
        status,
        text=text,
        error_message=error_message,
        increment_attempt=increment_attempt,
    )


async def get_image_extraction(message_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        _get_content_sync, "message_image_extractions", message_id
    )


async def get_completed_image_extractions(
    message_ids: Sequence[str],
) -> dict[str, str]:
    return await asyncio.to_thread(
        _get_completed_content_sync, "message_image_extractions", message_ids
    )


def _get_pending_content_extractions_sync(
    audio_message_ids: Sequence[str], image_message_ids: Sequence[str]
) -> set[str]:
    pending: set[str] = set()
    with _get_pool().connection() as connection:
        for table, message_ids in (
            ("message_transcriptions", audio_message_ids),
            ("message_image_extractions", image_message_ids),
        ):
            unique_ids = list(dict.fromkeys(message_ids))
            if not unique_ids:
                continue
            placeholders = ", ".join(["%s"] * len(unique_ids))
            rows = connection.execute(
                f"""
                SELECT message_id FROM {table}
                WHERE status IN ('pending', 'processing')
                  AND message_id IN ({placeholders})
                """,
                unique_ids,
            ).fetchall()
            pending.update(str(row[0]) for row in rows)
    return pending


async def get_pending_content_extractions(
    audio_message_ids: Sequence[str], image_message_ids: Sequence[str]
) -> set[str]:
    return await asyncio.to_thread(
        _get_pending_content_extractions_sync,
        audio_message_ids,
        image_message_ids,
    )
