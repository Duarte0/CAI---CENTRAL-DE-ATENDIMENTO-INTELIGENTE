"""PostgreSQL persistence for durable audio and image media extraction.

The repository uses the process-local pool owned by ``src.core.db`` while
keeping the shared transcription and image-extraction state protocol together.
The database facade re-exports the async operations for existing API, worker,
utility, and test consumers.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Sequence

from psycopg import sql
from psycopg.rows import dict_row

from src.core.db import _row_dict, get_database_pool

_CONTENT_TABLES = frozenset(
    {"message_transcriptions", "message_image_extractions"}
)


def _validate_content_table(table: str) -> None:
    if table not in _CONTENT_TABLES:
        raise ValueError("Unsupported content table")


def _reserve_content_sync(
    table: str,
    message_id: str,
    conversation_id: str | None,
    model: str,
    *,
    legacy_publication_marker: bool,
) -> bool:
    _validate_content_table(table)
    now = datetime.now(timezone.utc)
    publication_marker = now if legacy_publication_marker else None
    with get_database_pool().connection() as connection:
        with connection.transaction():
            row = connection.execute(
                sql.SQL(
                    """
                INSERT INTO {table_name} (
                    message_id, conversation_id, model, status,
                    next_attempt_at, enqueued_at, created_at, updated_at
                ) VALUES (%s, %s, %s, 'pending', %s, %s, %s, %s)
                ON CONFLICT (message_id) DO UPDATE SET
                    conversation_id = COALESCE(EXCLUDED.conversation_id,
                                               {table_name}.conversation_id),
                    model = EXCLUDED.model,
                    status = 'pending', error_message = NULL,
                    next_attempt_at = EXCLUDED.next_attempt_at,
                    enqueued_at = EXCLUDED.enqueued_at,
                    updated_at = EXCLUDED.updated_at
                WHERE {table_name}.status = 'failed'
                RETURNING message_id
                """
                ).format(table_name=sql.Identifier(table)),
                (
                    message_id,
                    conversation_id,
                    model,
                    now,
                    publication_marker,
                    now,
                    now,
                ),
            ).fetchone()
            return row is not None


async def reserve_transcription(
    message_id: str, conversation_id: str | None, model: str
) -> bool:
    return await asyncio.to_thread(
        _reserve_content_sync,
        "message_transcriptions",
        message_id,
        conversation_id,
        model,
        legacy_publication_marker=False,
    )


def _set_content_status_sync(
    table: str,
    message_id: str,
    status: str,
    *,
    text: str | None = None,
    error_message: str | None = None,
    increment_attempt: bool = False,
    next_attempt_at: datetime | None = None,
    expected_statuses: Sequence[str] | None = None,
    expected_updated_at: datetime | None = None,
    expected_lease_owner: str | None = None,
) -> datetime | None:
    _validate_content_table(table)
    if status not in {"pending", "processing", "completed", "failed"}:
        raise ValueError("Unsupported content status")
    if expected_statuses is None:
        expected_statuses = {
            "processing": ("pending",),
            "pending": ("processing",),
            "completed": ("processing",),
            "failed": ("pending", "processing"),
        }[status]
    completed_at = datetime.now(timezone.utc) if status == "completed" else None
    with get_database_pool().connection() as connection:
        with connection.transaction():
            row = connection.execute(
                sql.SQL(
                    """
                UPDATE {table_name}
                SET status = %s, text = COALESCE(%s, text), error_message = %s,
                    attempt_count = attempt_count + %s,
                    next_attempt_at = CASE
                        WHEN %s = 'pending' THEN %s::timestamptz
                        ELSE NULL
                    END,
                    enqueued_at = NULL,
                    lease_owner = CASE
                        WHEN %s::text IS NULL THEN lease_owner
                        ELSE NULL
                    END,
                    lease_expires_at = CASE
                        WHEN %s::text IS NULL THEN lease_expires_at
                        ELSE NULL
                    END,
                    updated_at = CURRENT_TIMESTAMP, completed_at = %s
                WHERE message_id = %s
                  AND status = ANY(%s)
                  AND (%s::timestamptz IS NULL OR updated_at = %s)
                  AND (%s::text IS NULL OR lease_owner = %s::text)
                RETURNING updated_at
                """
                ).format(table_name=sql.Identifier(table)),
                (
                    status,
                    text,
                    error_message,
                    int(increment_attempt),
                    status,
                    next_attempt_at,
                    expected_lease_owner,
                    expected_lease_owner,
                    completed_at,
                    message_id,
                    list(expected_statuses),
                    expected_updated_at,
                    expected_updated_at,
                    expected_lease_owner,
                    expected_lease_owner,
                ),
            ).fetchone()
    return row[0] if row else None


async def set_transcription_status(
    message_id: str,
    status: str,
    *,
    text: str | None = None,
    error_message: str | None = None,
    increment_attempt: bool = False,
    next_attempt_at: datetime | None = None,
    expected_statuses: Sequence[str] | None = None,
    expected_updated_at: datetime | None = None,
    expected_lease_owner: str | None = None,
) -> datetime | None:
    return await asyncio.to_thread(
        _set_content_status_sync,
        "message_transcriptions",
        message_id,
        status,
        text=text,
        error_message=error_message,
        increment_attempt=increment_attempt,
        next_attempt_at=next_attempt_at,
        expected_statuses=expected_statuses,
        expected_updated_at=expected_updated_at,
        expected_lease_owner=expected_lease_owner,
    )


def _claim_content_sync(
    table: str,
    *,
    owner: str,
    lease_seconds: int,
    message_id: str | None = None,
) -> dict[str, Any] | None:
    """Atomically claim one due media row for a polling worker."""
    _validate_content_table(table)
    normalized_owner = owner.strip()
    if not normalized_owner:
        raise ValueError("A media lease owner is required")
    if lease_seconds < 1:
        raise ValueError("Media lease duration must be positive")

    message_filter = sql.SQL("")
    parameters: list[Any] = []
    if message_id is not None:
        message_filter = sql.SQL("AND message_id = %s")
        parameters.append(message_id)
    parameters.extend([normalized_owner, lease_seconds])
    with get_database_pool().connection() as connection:
        with connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    sql.SQL(
                        """
                    WITH candidate AS (
                        SELECT message_id, status AS previous_status
                        FROM {table_name}
                        WHERE (
                            (
                                status = 'pending'
                                AND (next_attempt_at IS NULL
                                     OR next_attempt_at <= CURRENT_TIMESTAMP)
                            )
                            OR (
                                status = 'processing'
                                AND (lease_expires_at IS NULL
                                     OR lease_expires_at <= CURRENT_TIMESTAMP)
                            )
                        )
                        {message_filter}
                        ORDER BY COALESCE(next_attempt_at, updated_at), message_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE {table_name} AS content
                    SET status = 'processing',
                        attempt_count = content.attempt_count + 1,
                        error_message = CASE
                            WHEN candidate.previous_status = 'processing'
                            THEN 'recovered after processing lease expired'
                            ELSE content.error_message
                        END,
                        next_attempt_at = NULL,
                        enqueued_at = NULL,
                        lease_owner = %s,
                        lease_expires_at = CURRENT_TIMESTAMP
                            + (%s * INTERVAL '1 second'),
                        completed_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    FROM candidate
                    WHERE content.message_id = candidate.message_id
                    RETURNING content.*, candidate.previous_status
                    """
                    ).format(
                        table_name=sql.Identifier(table),
                        message_filter=message_filter,
                    ),
                    parameters,
                ).fetchone()
    return _row_dict(row)


async def claim_next_transcription(
    *, owner: str, lease_seconds: int, message_id: str | None = None
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        _claim_content_sync,
        "message_transcriptions",
        owner=owner,
        lease_seconds=lease_seconds,
        message_id=message_id,
    )


def _get_content_sync(table: str, message_id: str) -> dict[str, Any] | None:
    _validate_content_table(table)
    with get_database_pool().connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute(
                sql.SQL(
                    "SELECT * FROM {table_name} WHERE message_id = %s"
                ).format(table_name=sql.Identifier(table)),
                (message_id,),
            ).fetchone()
    return _row_dict(row)


async def get_transcription(message_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        _get_content_sync, "message_transcriptions", message_id
    )


def _content_work_metrics_sync(table: str) -> dict[str, int]:
    _validate_content_table(table)
    with get_database_pool().connection() as connection:
        row = connection.execute(
            sql.SQL(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE status = 'pending'
                          AND (next_attempt_at IS NULL
                               OR next_attempt_at <= CURRENT_TIMESTAMP)
                    ) AS due,
                    COUNT(*) FILTER (
                        WHERE status = 'pending'
                          AND next_attempt_at > CURRENT_TIMESTAMP
                    ) AS scheduled,
                    COUNT(*) FILTER (
                        WHERE status = 'processing'
                          AND lease_expires_at > CURRENT_TIMESTAMP
                    ) AS leased,
                    COUNT(*) FILTER (
                        WHERE status = 'processing'
                          AND (lease_expires_at IS NULL
                               OR lease_expires_at <= CURRENT_TIMESTAMP)
                    ) AS stale,
                    COUNT(*) FILTER (WHERE status = 'completed') AS completed,
                    COUNT(*) FILTER (WHERE status = 'failed') AS failed
                FROM {table_name}
                """
            ).format(table_name=sql.Identifier(table))
        ).fetchone()
    if row is None:
        return {
            "due": 0,
            "scheduled": 0,
            "leased": 0,
            "stale": 0,
            "completed": 0,
            "failed": 0,
        }
    return {
        "due": int(row[0]),
        "scheduled": int(row[1]),
        "leased": int(row[2]),
        "stale": int(row[3]),
        "completed": int(row[4]),
        "failed": int(row[5]),
    }


async def get_transcription_work_metrics() -> dict[str, int]:
    return await asyncio.to_thread(
        _content_work_metrics_sync, "message_transcriptions"
    )


def _get_completed_content_sync(
    table: str, message_ids: Sequence[str]
) -> dict[str, str]:
    _validate_content_table(table)
    unique_ids = list(dict.fromkeys(message_ids))
    if not unique_ids:
        return {}
    with get_database_pool().connection() as connection:
        rows = connection.execute(
            sql.SQL(
                """
            SELECT message_id, text FROM {table_name}
            WHERE status = 'completed' AND text IS NOT NULL
              AND message_id = ANY(%s)
            """
            ).format(table_name=sql.Identifier(table)),
            (unique_ids,),
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
        _reserve_content_sync,
        "message_image_extractions",
        message_id,
        conversation_id,
        model,
        legacy_publication_marker=True,
    )


async def set_image_extraction_status(
    message_id: str,
    status: str,
    *,
    text: str | None = None,
    error_message: str | None = None,
    increment_attempt: bool = False,
    next_attempt_at: datetime | None = None,
    expected_statuses: Sequence[str] | None = None,
    expected_updated_at: datetime | None = None,
) -> datetime | None:
    return await asyncio.to_thread(
        _set_content_status_sync,
        "message_image_extractions",
        message_id,
        status,
        text=text,
        error_message=error_message,
        increment_attempt=increment_attempt,
        next_attempt_at=next_attempt_at,
        expected_statuses=expected_statuses,
        expected_updated_at=expected_updated_at,
    )


def _claim_due_content_sync(
    table: str,
    *,
    lease_seconds: int,
    batch_size: int,
) -> list[dict[str, Any]]:
    _validate_content_table(table)
    if lease_seconds < 1 or batch_size < 1:
        raise ValueError("Recovery lease and batch size must be positive")
    with get_database_pool().connection() as connection:
        with connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                rows = cursor.execute(
                    sql.SQL(
                        """
                    WITH candidates AS (
                        SELECT message_id, status AS previous_status,
                               attempt_count
                        FROM {table_name}
                        WHERE (
                                status = 'pending'
                                AND (
                                    next_attempt_at IS NULL
                                    OR next_attempt_at <= CURRENT_TIMESTAMP
                                )
                                AND (
                                    enqueued_at IS NULL
                                    OR enqueued_at < (
                                        CURRENT_TIMESTAMP
                                        - (%s * INTERVAL '1 second')
                                    )
                                )
                              )
                           OR (
                                status = 'processing'
                                AND updated_at < (
                                    CURRENT_TIMESTAMP
                                    - (%s * INTERVAL '1 second')
                                )
                              )
                        ORDER BY COALESCE(next_attempt_at, updated_at), message_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT %s
                    )
                    UPDATE {table_name} AS content
                    SET status = 'pending',
                        error_message = CASE
                            WHEN content.status = 'processing'
                            THEN 'recovered after processing lease expired'
                            ELSE content.error_message
                        END,
                        next_attempt_at = NULL,
                        enqueued_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP,
                        completed_at = NULL
                    FROM candidates
                    WHERE content.message_id = candidates.message_id
                    RETURNING
                        content.message_id,
                        content.conversation_id,
                        content.model,
                        content.updated_at,
                        candidates.previous_status,
                        candidates.attempt_count
                    """
                    ).format(table_name=sql.Identifier(table)),
                    (lease_seconds, lease_seconds, batch_size),
                ).fetchall()
    return [dict(row) for row in rows]


async def recover_stale_transcriptions(
    *,
    lease_seconds: int,
    batch_size: int,
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _claim_due_content_sync,
        "message_transcriptions",
        lease_seconds=lease_seconds,
        batch_size=batch_size,
    )


async def recover_stale_image_extractions(
    *,
    lease_seconds: int,
    batch_size: int,
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _claim_due_content_sync,
        "message_image_extractions",
        lease_seconds=lease_seconds,
        batch_size=batch_size,
    )


def _release_content_publication_sync(
    table: str, message_id: str, error_message: str
) -> bool:
    _validate_content_table(table)
    with get_database_pool().connection() as connection:
        with connection.transaction():
            row = connection.execute(
                sql.SQL(
                    """
                UPDATE {table_name}
                SET enqueued_at = NULL,
                    error_message = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE message_id = %s AND status = 'pending'
                RETURNING message_id
                """
                ).format(table_name=sql.Identifier(table)),
                (error_message, message_id),
            ).fetchone()
    return row is not None


async def release_transcription_publication(
    message_id: str, error_message: str
) -> bool:
    return await asyncio.to_thread(
        _release_content_publication_sync,
        "message_transcriptions",
        message_id,
        error_message,
    )


async def release_image_publication(
    message_id: str, error_message: str
) -> bool:
    return await asyncio.to_thread(
        _release_content_publication_sync,
        "message_image_extractions",
        message_id,
        error_message,
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
    with get_database_pool().connection() as connection:
        for table, message_ids in (
            ("message_transcriptions", audio_message_ids),
            ("message_image_extractions", image_message_ids),
        ):
            unique_ids = list(dict.fromkeys(message_ids))
            if not unique_ids:
                continue
            rows = connection.execute(
                sql.SQL(
                    """
                SELECT message_id FROM {table_name}
                WHERE status IN ('pending', 'processing')
                  AND message_id = ANY(%s)
                """
                ).format(table_name=sql.Identifier(table)),
                (unique_ids,),
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
