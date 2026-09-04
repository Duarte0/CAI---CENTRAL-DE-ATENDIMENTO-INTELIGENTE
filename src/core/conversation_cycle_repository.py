"""PostgreSQL persistence for conversation-processing cycles.

The repository uses the process-local pool and schema verification owned by
``src.core.db`` while keeping cycle SQL and durable coordination together.
The compatibility facade re-exports these async operations for existing
routes, workers, utilities, and tests.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from src.core.config import settings
from src.core import db as database
from src.core.db import (
    _iso,
    _parse_timestamp,
    _row_dict,
    get_database_pool,
)
from src.core.identifiers import uuid7

RECOVERABLE_CYCLE_STATUSES = (
    "pending",
    "recovering_messages",
    "waiting_media",
    "building_context",
    "summarizing",
    "classifying",
    "retryable_failure",
)
TERMINAL_CYCLE_STATUSES = ("completed", "completed_with_warnings", "failed")
BLOCKED_CYCLE_STATUSES = ("media_blocked",)


def _require_cycle_schema() -> None:
    if not database._schema_capabilities.conversation_cycles:
        raise RuntimeError(
            "conversation cycle persistence requires migration "
            "0013_conversation_cycles"
    )


def _cycle_row(
    connection: psycopg.Connection[Any], public_id: str
) -> dict[str, Any] | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            """
            SELECT *
            FROM conversation_processing_cycles
            WHERE public_id = %s
            """,
            (public_id,),
        ).fetchone()
    return _row_dict(row)


def _normalize_cycle_contact_external_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > 240 or any(
        character in normalized for character in "\r\n\x00"
    ):
        raise ValueError("contact_external_id must be a safe stable ID")
    return normalized


def _create_open_cycle_sync(
    *,
    conversation_id: str,
    started_at: str | datetime,
    open_event_key: str,
    start_strategy: str,
    contact_external_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    _require_cycle_schema()
    normalized_contact_external_id = _normalize_cycle_contact_external_id(
        contact_external_id
    )
    now = datetime.now(timezone.utc)
    public_id = uuid7()
    with get_database_pool().connection() as connection:
        with connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (conversation_id,),
            )
            duplicate = connection.execute(
                """
                SELECT public_id
                FROM conversation_processing_cycles
                WHERE open_event_key = %s
                FOR UPDATE
                """,
                (open_event_key,),
            ).fetchone()
            existing = connection.execute(
                """
                SELECT public_id
                FROM conversation_processing_cycles
                WHERE conversation_id = %s AND ticket_closed_at IS NULL
                FOR UPDATE
                """,
                (conversation_id,),
            ).fetchone()
            created = duplicate is None and existing is None
            if duplicate is not None:
                public_id = duplicate[0]
            elif existing is None:
                sequence_row = connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence_number), 0) + 1
                    FROM conversation_processing_cycles
                    WHERE conversation_id = %s
                    """,
                    (conversation_id,),
                ).fetchone()
                if sequence_row is None:
                    raise RuntimeError("PostgreSQL did not return cycle sequence")
                sequence = int(sequence_row[0])
                connection.execute(
                    """
                    INSERT INTO conversation_processing_cycles (
                        public_id, conversation_id, sequence_number,
                        cycle_started_at, cycle_start_strategy, open_event_key,
                        digisac_contact_external_id,
                        status, next_attempt_at, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        'open', NULL, %s, %s
                    )
                    """,
                    (
                        public_id,
                        conversation_id,
                        sequence,
                        _parse_timestamp(started_at),
                        start_strategy,
                        open_event_key,
                        normalized_contact_external_id,
                        now,
                        now,
                    ),
                )
            else:
                public_id = existing[0]
                if normalized_contact_external_id is not None:
                    connection.execute(
                        """
                        UPDATE conversation_processing_cycles
                        SET digisac_contact_external_id = COALESCE(
                            digisac_contact_external_id, %s
                        ), updated_at = %s
                        WHERE public_id = %s
                        """,
                        (normalized_contact_external_id, now, public_id),
                    )
            cycle = _cycle_row(connection, str(public_id))
    if cycle is None:
        raise RuntimeError("PostgreSQL did not return the conversation cycle")
    return cycle, created


async def create_open_cycle(**kwargs: Any) -> tuple[dict[str, Any], bool]:
    return await asyncio.to_thread(_create_open_cycle_sync, **kwargs)


def _close_cycle_sync(
    *,
    conversation_id: str,
    protocol: str,
    closed_at: str | datetime,
    close_event_key: str,
    fallback_start_strategy: str = "pending_api_inference",
    contact_external_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    _require_cycle_schema()
    normalized_contact_external_id = _normalize_cycle_contact_external_id(
        contact_external_id
    )
    now = datetime.now(timezone.utc)
    closed_timestamp = _parse_timestamp(closed_at)
    with get_database_pool().connection() as connection:
        with connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (conversation_id,),
            )
            duplicate = connection.execute(
                """
                SELECT public_id
                FROM conversation_processing_cycles
                WHERE close_event_key = %s
                FOR UPDATE
                """,
                (close_event_key,),
            ).fetchone()
            created = duplicate is None
            if duplicate is not None:
                public_id = duplicate[0]
            else:
                opened = connection.execute(
                    """
                    SELECT id, public_id
                    FROM conversation_processing_cycles
                    WHERE conversation_id = %s AND ticket_closed_at IS NULL
                    FOR UPDATE
                    """,
                    (conversation_id,),
                ).fetchone()
                if opened is not None:
                    public_id = opened[1]
                    connection.execute(
                        """
                        UPDATE conversation_processing_cycles
                        SET protocol = %s, ticket_closed_at = %s,
                            close_event_key = %s, status = 'pending',
                            digisac_contact_external_id = COALESCE(
                                digisac_contact_external_id, %s
                            ),
                            next_attempt_at = %s, updated_at = %s,
                            error_phase = NULL, error_message = NULL
                        WHERE id = %s
                        """,
                        (
                            protocol,
                            closed_timestamp,
                            close_event_key,
                            normalized_contact_external_id,
                            now
                            + timedelta(
                                seconds=settings.digisac_history_initial_delay_seconds
                            ),
                            now,
                            opened[0],
                        ),
                    )
                else:
                    sequence_row = connection.execute(
                        """
                        SELECT COALESCE(MAX(sequence_number), 0) + 1
                        FROM conversation_processing_cycles
                        WHERE conversation_id = %s
                        """,
                        (conversation_id,),
                    ).fetchone()
                    if sequence_row is None:
                        raise RuntimeError(
                            "PostgreSQL did not return cycle sequence"
                        )
                    sequence = int(sequence_row[0])
                    public_id = uuid7()
                    connection.execute(
                        """
                        INSERT INTO conversation_processing_cycles (
                            public_id, conversation_id, sequence_number,
                            protocol, cycle_started_at, ticket_closed_at,
                            cycle_start_strategy, close_event_key, status,
                            digisac_contact_external_id,
                            next_attempt_at, created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, NULL, %s, %s, %s, 'pending', %s,
                            %s, %s, %s
                        )
                        """,
                        (
                            public_id,
                            conversation_id,
                            sequence,
                            protocol,
                            closed_timestamp,
                            fallback_start_strategy,
                            close_event_key,
                            normalized_contact_external_id,
                            now
                            + timedelta(
                                seconds=settings.digisac_history_initial_delay_seconds
                            ),
                            now,
                            now,
                        ),
                    )
            cycle = _cycle_row(connection, str(public_id))
    if cycle is None:
        raise RuntimeError("PostgreSQL did not return the closed cycle")
    return cycle, created


async def close_cycle(**kwargs: Any) -> tuple[dict[str, Any], bool]:
    return await asyncio.to_thread(_close_cycle_sync, **kwargs)


def _get_cycle_sync(public_id: str) -> dict[str, Any] | None:
    _require_cycle_schema()
    with get_database_pool().connection() as connection:
        return _cycle_row(connection, public_id)


async def get_cycle(public_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_get_cycle_sync, public_id)


def _get_cycles_by_public_ids_sync(
    public_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    _require_cycle_schema()
    normalized = tuple(
        dict.fromkeys(public_id.strip() for public_id in public_ids if public_id.strip())
    )
    if not normalized:
        return {}
    with get_database_pool().connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            rows = cursor.execute(
                """
                SELECT public_id, status, next_attempt_at, lease_expires_at,
                       completed_at, updated_at
                FROM conversation_processing_cycles
                WHERE public_id::text = ANY(%s)
                """,
                (list(normalized),),
            ).fetchall()
    return {
        str(row["public_id"]): _row_dict(row) or {}
        for row in rows
    }


async def get_cycles_by_public_ids(
    public_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """Return safe cycle summaries for a bounded operational inventory."""
    return await asyncio.to_thread(_get_cycles_by_public_ids_sync, public_ids)


def _get_latest_cycle_sync(conversation_id: str) -> dict[str, Any] | None:
    _require_cycle_schema()
    with get_database_pool().connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute(
                """
                SELECT *
                FROM conversation_processing_cycles
                WHERE conversation_id = %s
                ORDER BY sequence_number DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
    return _row_dict(row)


async def get_latest_cycle(conversation_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_get_latest_cycle_sync, conversation_id)


def _get_previous_cycle_sync(
    conversation_id: str, sequence_number: int
) -> dict[str, Any] | None:
    _require_cycle_schema()
    with get_database_pool().connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute(
                """
                SELECT *
                FROM conversation_processing_cycles
                WHERE conversation_id = %s
                  AND sequence_number < %s
                ORDER BY sequence_number DESC
                LIMIT 1
                """,
                (conversation_id, sequence_number),
            ).fetchone()
    return _row_dict(row)


async def get_previous_cycle(
    conversation_id: str, sequence_number: int
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        _get_previous_cycle_sync, conversation_id, sequence_number
    )


def _list_cycles_sync(
    conversation_id: str, *, limit: int = 50
) -> list[dict[str, Any]]:
    _require_cycle_schema()
    bounded_limit = max(1, min(limit, 100))
    with get_database_pool().connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            rows = cursor.execute(
                """
                SELECT *
                FROM conversation_processing_cycles
                WHERE conversation_id = %s
                ORDER BY sequence_number DESC
                LIMIT %s
                """,
                (conversation_id, bounded_limit),
            ).fetchall()
    return [_row_dict(row) or {} for row in rows]


async def list_cycles(
    conversation_id: str, *, limit: int = 50
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _list_cycles_sync, conversation_id, limit=limit
    )


def _transition_cycle_sync(
    public_id: str,
    status: str,
    *,
    expected_statuses: Sequence[str] | None = None,
    fields: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    _require_cycle_schema()
    allowed = {
        "open",
        *RECOVERABLE_CYCLE_STATUSES,
        *BLOCKED_CYCLE_STATUSES,
        *TERMINAL_CYCLE_STATUSES,
    }
    if status not in allowed:
        raise ValueError(f"Unsupported cycle status {status!r}")
    values = dict(fields or {})
    permitted = {
        "protocol",
        "cycle_started_at",
        "cycle_start_strategy",
        "attempt_count",
        "transient_retry_count",
        "error_phase",
        "error_message",
        "warning_count",
        "snapshot_json",
        "rendered_context",
        "model_context",
        "context_reduction_applied",
        "context_reduction_json",
        "history_recovery_attempt",
        "history_page_count",
        "processing_time_ms",
        "classification_id",
        "next_attempt_at",
        "enqueued_at",
        "lease_owner",
        "lease_expires_at",
        "completed_at",
    }
    unknown = set(values) - permitted
    if unknown:
        raise ValueError(f"Unsupported cycle fields: {sorted(unknown)!r}")
    assignments: list[sql.Composable] = [
        sql.SQL("status = %s"),
        sql.SQL("updated_at = CURRENT_TIMESTAMP"),
    ]
    parameters: list[Any] = [status]
    for name, value in values.items():
        assignments.append(
            sql.Composed([sql.Identifier(name), sql.SQL(" = %s")])
        )
        if name in {"snapshot_json", "context_reduction_json"} and value is not None:
            parameters.append(Jsonb(value))
        elif name in {
            "cycle_started_at",
            "next_attempt_at",
            "enqueued_at",
            "lease_expires_at",
            "completed_at",
        } and value is not None:
            parameters.append(_parse_timestamp(value))
        else:
            parameters.append(value)
    parameters.append(public_id)
    status_clause = sql.SQL("")
    if expected_statuses:
        status_clause = sql.SQL("AND status = ANY(%s)")
        parameters.append(list(expected_statuses))
    with get_database_pool().connection() as connection:
        with connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                query = sql.SQL(
                    """
                    UPDATE conversation_processing_cycles
                    SET {assignments}
                    WHERE public_id = %s
                    {status_clause}
                    RETURNING *
                    """,
                ).format(
                    assignments=sql.SQL(", ").join(
                        assignments
                    ),
                    status_clause=status_clause,
                )
                row = cursor.execute(
                    query,
                    parameters,
                ).fetchone()
    return _row_dict(row)


async def transition_cycle(
    public_id: str,
    status: str,
    *,
    expected_statuses: Sequence[str] | None = None,
    fields: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        _transition_cycle_sync,
        public_id,
        status,
        expected_statuses=expected_statuses,
        fields=fields,
    )


def _claim_cycle_sync(
    public_id: str, *, owner: str, lease_seconds: int
) -> dict[str, Any] | None:
    _require_cycle_schema()
    with get_database_pool().connection() as connection:
        with connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    """
                    UPDATE conversation_processing_cycles
                    SET lease_owner = %s,
                        lease_expires_at = CURRENT_TIMESTAMP
                            + (%s * INTERVAL '1 second'),
                        enqueued_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE public_id = %s
                      AND status = ANY(%s)
                      AND (next_attempt_at IS NULL
                           OR next_attempt_at <= CURRENT_TIMESTAMP)
                      AND (lease_expires_at IS NULL
                           OR lease_expires_at <= CURRENT_TIMESTAMP)
                    RETURNING *
                    """,
                    (
                        owner,
                        lease_seconds,
                        public_id,
                        list(RECOVERABLE_CYCLE_STATUSES),
                    ),
                ).fetchone()
    return _row_dict(row)


async def claim_cycle(
    public_id: str, *, owner: str, lease_seconds: int
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        _claim_cycle_sync,
        public_id,
        owner=owner,
        lease_seconds=lease_seconds,
    )


def _claim_next_cycle_sync(
    *, owner: str, lease_seconds: int
) -> dict[str, Any] | None:
    """Atomically lease one due cycle without a transport queue."""
    _require_cycle_schema()
    with get_database_pool().connection() as connection:
        with connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    """
                    WITH candidate AS (
                        SELECT id
                        FROM conversation_processing_cycles
                        WHERE status = ANY(%s)
                          AND (next_attempt_at IS NULL
                               OR next_attempt_at <= CURRENT_TIMESTAMP)
                          AND (lease_expires_at IS NULL
                               OR lease_expires_at <= CURRENT_TIMESTAMP)
                        ORDER BY COALESCE(next_attempt_at, updated_at), id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE conversation_processing_cycles AS cycle
                    SET lease_owner = %s,
                        lease_expires_at = CURRENT_TIMESTAMP
                            + (%s * INTERVAL '1 second'),
                        enqueued_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    FROM candidate
                    WHERE cycle.id = candidate.id
                    RETURNING cycle.*
                    """,
                    (
                        list(RECOVERABLE_CYCLE_STATUSES),
                        owner,
                        lease_seconds,
                    ),
                ).fetchone()
    return _row_dict(row)


async def claim_next_cycle(
    *, owner: str, lease_seconds: int
) -> dict[str, Any] | None:
    """Lease the next due persistent finalization cycle.

    PostgreSQL is the work authority: a row is selected and leased in one
    transaction, so polling workers never need to publish or consume a Redis
    job copy.
    """
    return await asyncio.to_thread(
        _claim_next_cycle_sync,
        owner=owner,
        lease_seconds=lease_seconds,
    )


def _recoverable_cycles_sync(*, limit: int = 100) -> list[dict[str, Any]]:
    _require_cycle_schema()
    with get_database_pool().connection() as connection:
        with connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                rows = cursor.execute(
                    """
                    WITH candidates AS (
                        SELECT id
                        FROM conversation_processing_cycles
                        WHERE status = ANY(%s)
                          AND (next_attempt_at IS NULL
                               OR next_attempt_at <= CURRENT_TIMESTAMP)
                          AND (lease_expires_at IS NULL
                               OR lease_expires_at <= CURRENT_TIMESTAMP)
                          AND (enqueued_at IS NULL
                               OR enqueued_at < CURRENT_TIMESTAMP
                                  - (%s * INTERVAL '1 second'))
                        ORDER BY COALESCE(next_attempt_at, updated_at), id
                        FOR UPDATE SKIP LOCKED
                        LIMIT %s
                    )
                    UPDATE conversation_processing_cycles AS cycle
                    SET enqueued_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    FROM candidates
                    WHERE cycle.id = candidates.id
                    RETURNING cycle.*
                    """,
                    (
                        list(RECOVERABLE_CYCLE_STATUSES),
                        settings.finalization_lease_seconds,
                        max(1, limit),
                    ),
                ).fetchall()
    return [_row_dict(row) or {} for row in rows]


async def get_recoverable_cycles(*, limit: int = 100) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_recoverable_cycles_sync, limit=limit)


def _release_cycle_publication_sync(public_id: str) -> bool:
    _require_cycle_schema()
    with get_database_pool().connection() as connection:
        with connection.transaction():
            row = connection.execute(
                """
                UPDATE conversation_processing_cycles
                SET enqueued_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE public_id = %s
                  AND status = ANY(%s)
                RETURNING public_id
                """,
                (public_id, list(RECOVERABLE_CYCLE_STATUSES)),
            ).fetchone()
    return row is not None


async def release_cycle_publication(public_id: str) -> bool:
    return await asyncio.to_thread(_release_cycle_publication_sync, public_id)


def _wake_unblocked_media_cycles_sync(
    *, max_attempts: int, limit: int
) -> list[dict[str, Any]]:
    _require_cycle_schema()
    with get_database_pool().connection() as connection:
        with connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                rows = cursor.execute(
                    """
                    WITH candidates AS (
                        SELECT cycle.id
                        FROM conversation_processing_cycles AS cycle
                        WHERE cycle.status = 'media_blocked'
                          AND NOT EXISTS (
                              SELECT 1
                              FROM conversation_cycle_messages AS message
                              LEFT JOIN message_transcriptions AS audio
                                ON audio.message_id = message.message_id
                               AND message.message_type IN ('ptt', 'audio', 'voice')
                              LEFT JOIN message_image_extractions AS image
                                ON image.message_id = message.message_id
                               AND message.message_type = 'image'
                              WHERE message.cycle_id = cycle.id
                                AND (
                                    (
                                        message.message_type IN ('ptt', 'audio', 'voice')
                                        AND audio.status = 'failed'
                                        AND audio.attempt_count >= %s
                                    )
                                    OR (
                                        message.message_type = 'image'
                                        AND image.status = 'failed'
                                        AND image.attempt_count >= %s
                                    )
                                )
                          )
                        ORDER BY cycle.updated_at, cycle.id
                        FOR UPDATE SKIP LOCKED
                        LIMIT %s
                    )
                    UPDATE conversation_processing_cycles AS cycle
                    SET status = 'waiting_media',
                        next_attempt_at = CURRENT_TIMESTAMP,
                        enqueued_at = NULL,
                        error_phase = NULL,
                        error_message = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    FROM candidates
                    WHERE cycle.id = candidates.id
                    RETURNING cycle.*
                    """,
                    (max_attempts, max_attempts, max(1, limit)),
                ).fetchall()
    return [_row_dict(row) or {} for row in rows]


async def wake_unblocked_media_cycles(
    *, max_attempts: int, limit: int = 100
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _wake_unblocked_media_cycles_sync,
        max_attempts=max_attempts,
        limit=limit,
    )


def _save_cycle_messages_sync(
    public_id: str, messages: Sequence[Mapping[str, Any]]
) -> tuple[list[str], list[str]]:
    _require_cycle_schema()
    accepted: list[str] = []
    conflicts: list[str] = []
    now = datetime.now(timezone.utc)
    with get_database_pool().connection() as connection:
        with connection.transaction():
            cycle = connection.execute(
                """
                SELECT id
                FROM conversation_processing_cycles
                WHERE public_id = %s
                FOR UPDATE
                """,
                (public_id,),
            ).fetchone()
            if cycle is None:
                raise LookupError("conversation cycle not found")
            cycle_id = int(cycle[0])
            connection.execute(
                "DELETE FROM conversation_cycle_messages WHERE cycle_id = %s",
                (cycle_id,),
            )
            for position, message in enumerate(messages):
                message_id = str(message["message_id"])
                owner = connection.execute(
                    """
                    SELECT cycle_id
                    FROM conversation_cycle_messages
                    WHERE message_id = %s
                    """,
                    (message_id,),
                ).fetchone()
                if owner is not None and int(owner[0]) != cycle_id:
                    conflicts.append(message_id)
                    continue
                connection.execute(
                    """
                    INSERT INTO conversation_cycle_messages (
                        cycle_id, message_id, position, message_type,
                        message_timestamp, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (cycle_id, message_id) DO UPDATE SET
                        position = EXCLUDED.position,
                        message_type = EXCLUDED.message_type,
                        message_timestamp = EXCLUDED.message_timestamp
                    """,
                    (
                        cycle_id,
                        message_id,
                        position,
                        str(message["type"]),
                        _parse_timestamp(str(message["timestamp"])),
                        now,
                    ),
                )
                accepted.append(message_id)
    return accepted, conflicts


async def save_cycle_messages(
    public_id: str, messages: Sequence[Mapping[str, Any]]
) -> tuple[list[str], list[str]]:
    return await asyncio.to_thread(
        _save_cycle_messages_sync, public_id, messages
    )


def _get_content_states_sync(
    audio_message_ids: Sequence[str], image_message_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    with get_database_pool().connection() as connection:
        for table, kind, message_ids in (
            ("message_transcriptions", "audio", audio_message_ids),
            ("message_image_extractions", "image", image_message_ids),
        ):
            unique = list(dict.fromkeys(message_ids))
            if not unique:
                continue
            rows = connection.execute(
                sql.SQL(
                    """
                SELECT message_id, status, text, attempt_count, error_message,
                       updated_at, next_attempt_at, enqueued_at
                FROM {table}
                WHERE message_id = ANY(%s)
                """,
                ).format(table=sql.Identifier(table)),
                (unique,),
            ).fetchall()
            for row in rows:
                states[str(row[0])] = {
                    "kind": kind,
                    "status": row[1],
                    "text": row[2],
                    "attempt_count": row[3],
                    "error_message": row[4],
                    "updated_at": _iso(row[5]),
                    "next_attempt_at": _iso(row[6]),
                    "enqueued_at": _iso(row[7]),
                }
    return states


async def get_content_states(
    audio_message_ids: Sequence[str], image_message_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    return await asyncio.to_thread(
        _get_content_states_sync, audio_message_ids, image_message_ids
    )


def _cycle_metrics_sync() -> dict[str, int]:
    if not database._schema_capabilities.conversation_cycles:
        return {}
    with get_database_pool().connection() as connection:
        rows = connection.execute(
            """
            SELECT status, COUNT(*)
            FROM conversation_processing_cycles
            GROUP BY status
            """
        ).fetchall()
    return {str(status): int(count) for status, count in rows}


async def get_cycle_metrics() -> dict[str, int]:
    return await asyncio.to_thread(_cycle_metrics_sync)


def _cycle_work_metrics_sync() -> dict[str, int]:
    """Return PostgreSQL-derived finalization work counters."""
    if not database._schema_capabilities.conversation_cycles:
        return {"due": 0, "scheduled": 0, "leased": 0}
    with get_database_pool().connection() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE status = ANY(%s)
                      AND (next_attempt_at IS NULL
                           OR next_attempt_at <= CURRENT_TIMESTAMP)
                      AND (lease_expires_at IS NULL
                           OR lease_expires_at <= CURRENT_TIMESTAMP)
                ) AS due,
                COUNT(*) FILTER (
                    WHERE status = ANY(%s)
                      AND next_attempt_at > CURRENT_TIMESTAMP
                ) AS scheduled,
                COUNT(*) FILTER (
                    WHERE status = ANY(%s)
                      AND lease_expires_at > CURRENT_TIMESTAMP
                ) AS leased
            FROM conversation_processing_cycles
            """,
            (
                list(RECOVERABLE_CYCLE_STATUSES),
                list(RECOVERABLE_CYCLE_STATUSES),
                list(RECOVERABLE_CYCLE_STATUSES),
            ),
        ).fetchone()
    if row is None:
        return {"due": 0, "scheduled": 0, "leased": 0}
    return {
        "due": int(row[0]),
        "scheduled": int(row[1]),
        "leased": int(row[2]),
    }


async def get_cycle_work_metrics() -> dict[str, int]:
    return await asyncio.to_thread(_cycle_work_metrics_sync)


def _get_cycle_result_sync(public_id: str) -> dict[str, Any] | None:
    _require_cycle_schema()
    with get_database_pool().connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute(
                """
                SELECT
                    c.public_id AS cycle_id,
                    c.conversation_id,
                    c.sequence_number,
                    c.status,
                    c.warning_count,
                    i.public_id AS classification_public_id,
                    i.intent_type,
                    i.confidence,
                    i.title,
                    i.protocol,
                    i.description,
                    i.department,
                    i.agent,
                    i.message_count,
                    i.created_at AS processed_at
                FROM conversation_processing_cycles AS c
                LEFT JOIN ia_classifications AS i
                  ON i.id = c.classification_id
                WHERE c.public_id = %s
                """,
                (public_id,),
            ).fetchone()
    return _row_dict(row)


async def get_cycle_result(public_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_get_cycle_result_sync, public_id)
