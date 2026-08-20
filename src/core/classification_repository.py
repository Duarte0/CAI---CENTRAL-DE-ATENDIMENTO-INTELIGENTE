"""PostgreSQL persistence for durable IA classifications.

The repository keeps classification identity, ordered message associations,
protocol metadata, and existence queries together while using the process-local
pool and schema capabilities owned by ``src.core.db``.  The database facade
re-exports the async operations for existing workers, utilities, and tests.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence, cast
from uuid import UUID

from psycopg.types.json import Jsonb

from src.core.identifiers import uuid7
from src.core.intents import normalize_intent_type

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClassificationIdentity:
    id: int
    public_id: UUID | None


def _database():
    from src.core import db

    return db


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
    items = cast(Sequence[Any], value)
    return [
        item.strip()
        for item in items
        if isinstance(item, str) and item.strip()
    ]


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
    idempotency_key: str | None = None,
) -> ClassificationIdentity:
    database = _database()
    capabilities = database._schema_capabilities
    created_timestamp = database._parse_timestamp(created_at)
    public_id = uuid7() if capabilities.classification_identity_columns else None
    normalized_idempotency_key = (
        idempotency_key.strip()
        if (
            capabilities.classification_idempotency_index
            and isinstance(idempotency_key, str)
            and idempotency_key.strip()
        )
        else None
    )
    with database.get_database_pool().connection() as connection:
        with connection.transaction():
            base_values = (
                conversation_id,
                Jsonb(list(message_ids)),
                created_timestamp,
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
                created_timestamp,
            )
            if capabilities.classification_identity_columns:
                conflict = (
                    """
                    ON CONFLICT (idempotency_key)
                    WHERE idempotency_key IS NOT NULL
                    DO NOTHING
                    """
                    if (
                        normalized_idempotency_key
                        and capabilities.classification_idempotency_index
                    )
                    else ""
                )
                row = connection.execute(
                    f"""
                    INSERT INTO ia_classifications (
                        conversation_id, message_ids, created_at, full_context,
                        message_count, intent_type, confidence, title, protocol,
                        description, department, agent, model,
                        processing_time_ms, prompt_version, updated_at,
                        public_id, idempotency_key
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    )
                    {conflict}
                    RETURNING id, public_id
                    """,
                    (*base_values, public_id, normalized_idempotency_key),
                ).fetchone()
                if row is None and normalized_idempotency_key:
                    row = connection.execute(
                        """
                        SELECT id, public_id
                        FROM ia_classifications
                        WHERE idempotency_key = %s
                        """,
                        (normalized_idempotency_key,),
                    ).fetchone()
            else:
                row = connection.execute(
                    """
                    INSERT INTO ia_classifications (
                        conversation_id, message_ids, created_at, full_context,
                        message_count, intent_type, confidence, title, protocol,
                        description, department, agent, model,
                        processing_time_ms, prompt_version, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    RETURNING id, NULL::uuid
                    """,
                    base_values,
                ).fetchone()
            if row is None:
                raise RuntimeError("PostgreSQL did not return a classification id")
            identity = ClassificationIdentity(id=int(row[0]), public_id=row[1])
            if capabilities.classification_messages:
                seen_message_ids: set[str] = set()
                positioned_message_ids: list[tuple[int, str]] = []
                for position, message_id in enumerate(message_ids):
                    if message_id not in seen_message_ids:
                        seen_message_ids.add(message_id)
                        positioned_message_ids.append((position, message_id))
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO classification_messages (
                            classification_id, message_id, position, created_at
                        ) VALUES (%s, %s, %s, %s)
                        ON CONFLICT (classification_id, message_id) DO NOTHING
                        """,
                        [
                            (identity.id, message_id, position, created_timestamp)
                            for position, message_id in positioned_message_ids
                        ],
                    )
    return identity


async def insert_classification(**kwargs: Any) -> ClassificationIdentity:
    return await asyncio.to_thread(_insert_classification_sync, **kwargs)


def _update_analysis_protocol_sync(conversation_id: str, protocol: str) -> bool:
    database = _database()
    with database.get_database_pool().connection() as connection:
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
    database = _database()
    with database.get_database_pool().connection() as connection:
        return (
            connection.execute(
                """
                SELECT 1 FROM ia_classifications
                WHERE conversation_id = %s AND created_at = %s
                LIMIT 1
                """,
                (conversation_id, database._parse_timestamp(created_at)),
            ).fetchone()
            is not None
        )


async def classification_exists(conversation_id: str, created_at: str) -> bool:
    return await asyncio.to_thread(
        _classification_exists_sync, conversation_id, created_at
    )


def _ticket_has_classification_sync(conversation_id: str) -> bool:
    database = _database()
    with database.get_database_pool().connection() as connection:
        return (
            connection.execute(
                "SELECT 1 FROM ia_classifications WHERE conversation_id = %s LIMIT 1",
                (conversation_id,),
            ).fetchone()
            is not None
        )


async def ticket_has_classification(conversation_id: str) -> bool:
    return await asyncio.to_thread(_ticket_has_classification_sync, conversation_id)
