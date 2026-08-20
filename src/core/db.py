"""PostgreSQL persistence for the CAI pipeline.

The module keeps the existing async-facing API used by FastAPI and the workers,
while using one thread-safe synchronous psycopg pool per process.  Schema
creation is deliberately not performed here; deploys must apply Alembic
migrations before starting the application.
"""

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence, cast
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from src.core.config import settings
from src.core.digisac_client import DigisacContact
from src.core.identifiers import uuid7
from src.core.intents import normalize_intent_type

logger = logging.getLogger(__name__)
CURRENT_SCHEMA_REVISION = "0020_cycle_contact_provenance"
EXPECTED_SCHEMA_REVISION = CURRENT_SCHEMA_REVISION
SUPPORTED_SCHEMA_REVISIONS = frozenset(
    {
        "0001_initial",
        "0002_quality_checks",
        "0003_validate_checks",
        "0004_updated_at_nn",
        "0005_class_identity",
        "0006_identity_indexes",
        "0007_class_messages",
        "0008_event_fk",
        "0009_recovery_indexes",
        "0010_public_id_check",
        "0011_public_id_final",
        "0012_validate_event_fk",
        "0013_conversation_cycles",
        "0014_retry_scheduling",
        "0015_acessorias_directory",
        "0016_digisac_contact_identity",
        "0017_digisac_acessorias_identity",
        "0018_department_mapping",
        "0019_acessorias_request_creation",
        CURRENT_SCHEMA_REVISION,
    }
)
_pool: ConnectionPool[psycopg.Connection[Any]] | None = None
_pool_lock = threading.Lock()


@dataclass(frozen=True)
class SchemaCapabilities:
    classification_identity_columns: bool = False
    classification_idempotency_index: bool = False
    classification_messages: bool = False
    conversation_cycles: bool = False
    contact_identity: bool = False


@dataclass(frozen=True)
class ClassificationIdentity:
    id: int
    public_id: UUID | None


_schema_capabilities = SchemaCapabilities()


def _parse_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


# Shared by the focused repositories; this facade module does not call the
# helper directly after those implementations are extracted.
def _row_dict(  # pyright: ignore[reportUnusedFunction]
    row: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    return {key: _iso(value) for key, value in row.items()} if row else None


def _require_database_url() -> str:
    value = settings.database_url
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(
            "DATABASE_URL is required; apply migrations and configure the "
            "PostgreSQL connection before starting the CAI"
        )
    return value.strip()


def _get_pool() -> ConnectionPool[psycopg.Connection[Any]]:
    if _pool is None:
        raise RuntimeError("Database pool is not initialized")
    return _pool


def get_database_pool() -> ConnectionPool[psycopg.Connection[Any]]:
    """Return the initialized process-local pool for durable integrations."""
    return _get_pool()


def _configure_connection_sync(connection: psycopg.Connection[Any]) -> None:
    connection.execute(
        """
        SELECT
            set_config('statement_timeout', %s, false),
            set_config('lock_timeout', %s, false),
            set_config('idle_in_transaction_session_timeout', %s, false)
        """,
        (
            f"{settings.database_statement_timeout_ms}ms",
            f"{settings.database_lock_timeout_ms}ms",
            f"{settings.database_idle_transaction_timeout_ms}ms",
        ),
    )
    connection.commit()


def _open_pool_sync() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            return
        conninfo = _require_database_url()
        pool: ConnectionPool[psycopg.Connection[Any]] = ConnectionPool(
            conninfo=conninfo,
            min_size=settings.database_pool_min_size,
            max_size=settings.database_pool_max_size,
            timeout=settings.database_pool_timeout_seconds,
            configure=_configure_connection_sync,
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
    global _schema_capabilities
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
    if not row or row[0] not in SUPPORTED_SCHEMA_REVISIONS:
        actual = row[0] if row else "none"
        raise RuntimeError(
            f"Unsupported PostgreSQL schema revision {actual!r}; "
            f"supported revisions are {sorted(SUPPORTED_SCHEMA_REVISIONS)!r}"
        )
    with pool.connection() as connection:
        capabilities = connection.execute(
            """
            SELECT
                EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'ia_classifications'
                      AND column_name = 'public_id'
                ),
                to_regclass(
                    current_schema() ||
                    '.ux_ia_classifications_idempotency_key'
                ) IS NOT NULL,
                to_regclass(
                    current_schema() || '.classification_messages'
                ) IS NOT NULL,
                to_regclass(
                    current_schema() || '.conversation_processing_cycles'
                ) IS NOT NULL,
                to_regclass(
                    current_schema() || '.digisac_contacts'
                ) IS NOT NULL
            """
        ).fetchone()
    if capabilities is None:
        raise RuntimeError("PostgreSQL capability query returned no row")
    _schema_capabilities = SchemaCapabilities(
        classification_identity_columns=bool(capabilities[0]),
        classification_idempotency_index=bool(capabilities[1]),
        classification_messages=bool(capabilities[2]),
        conversation_cycles=bool(capabilities[3]),
        contact_identity=bool(capabilities[4]),
    )
    logger.info(
        "PostgreSQL schema verified: revision=%s identity=%s "
        "idempotency=%s normalized_messages=%s conversation_cycles=%s",
        row[0],
        _schema_capabilities.classification_identity_columns,
        _schema_capabilities.classification_idempotency_index,
        _schema_capabilities.classification_messages,
        _schema_capabilities.conversation_cycles,
    )
    if not _schema_capabilities.conversation_cycles:
        raise RuntimeError(
            "persistent finalization requires migration "
            "0013_conversation_cycles"
        )
    if row[0] != CURRENT_SCHEMA_REVISION:
        raise RuntimeError(
            "durable finalization requires migration "
            f"{CURRENT_SCHEMA_REVISION}"
        )
    if not _schema_capabilities.contact_identity:
        raise RuntimeError(
            "DigiSac contact identity requires migration "
            f"{CURRENT_SCHEMA_REVISION}"
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
    global _pool, _schema_capabilities
    with _pool_lock:
        if _pool is not None:
            _pool.close(timeout=settings.database_pool_timeout_seconds)
            _pool = None
            _schema_capabilities = SchemaCapabilities()
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


def _upsert_digisac_directory_sync(
    resource: str, entries: Sequence[Mapping[str, Any]], synced_at: str
) -> int:
    table = {"departments": "digisac_departments", "users": "digisac_users"}.get(
        resource
    )
    if table is None:
        raise ValueError(f"Unsupported DigiSac directory resource: {resource}")
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


# DigiSac contact persistence lives in src.core.digisac_contact_repository.  These
# delegates preserve the historical db.py import boundary without creating a
# second pool or importing the repository during db module initialization.
async def upsert_digisac_contact(
    contact: DigisacContact,
    *,
    source: str,
    observed_at: str | datetime | None = None,
) -> dict[str, Any]:
    from src.core.digisac_contact_repository import (
        upsert_digisac_contact as repository_upsert_digisac_contact,
    )

    return await repository_upsert_digisac_contact(
        contact, source=source, observed_at=observed_at
    )


async def publish_digisac_contact_backfill(
    contacts: Sequence[DigisacContact],
    *,
    observed_at: str | datetime | None = None,
) -> dict[str, int]:
    from src.core.digisac_contact_repository import (
        publish_digisac_contact_backfill as repository_publish_digisac_contact_backfill,
    )

    return await repository_publish_digisac_contact_backfill(
        contacts, observed_at=observed_at
    )


async def request_digisac_contact_hydration(
    external_id: str, *, requested_at: str | datetime | None = None
) -> bool:
    from src.core.digisac_contact_repository import (
        request_digisac_contact_hydration as repository_request_digisac_contact_hydration,
    )

    return await repository_request_digisac_contact_hydration(
        external_id, requested_at=requested_at
    )


async def claim_digisac_contact_hydration(
    *, lease_seconds: int | None = None
) -> dict[str, Any] | None:
    from src.core.digisac_contact_repository import (
        claim_digisac_contact_hydration as repository_claim_digisac_contact_hydration,
    )

    return await repository_claim_digisac_contact_hydration(
        lease_seconds=lease_seconds
    )


async def mark_digisac_contact_hydration_success(
    external_id: str, *, expected_lease_until: datetime
) -> bool:
    from src.core.digisac_contact_repository import (
        mark_digisac_contact_hydration_success as repository_mark_digisac_contact_hydration_success,
    )

    return await repository_mark_digisac_contact_hydration_success(
        external_id, expected_lease_until=expected_lease_until
    )


async def mark_digisac_contact_hydration_failure(
    external_id: str,
    category: str,
    *,
    retryable: bool,
    expected_lease_until: datetime,
    max_attempts: int | None = None,
) -> bool:
    from src.core.digisac_contact_repository import (
        mark_digisac_contact_hydration_failure as repository_mark_digisac_contact_hydration_failure,
    )

    return await repository_mark_digisac_contact_hydration_failure(
        external_id,
        category,
        retryable=retryable,
        expected_lease_until=expected_lease_until,
        max_attempts=max_attempts,
    )


async def get_digisac_contact(external_id: str) -> dict[str, Any] | None:
    from src.core.digisac_contact_repository import (
        get_digisac_contact as repository_get_digisac_contact,
    )

    return await repository_get_digisac_contact(external_id)


async def get_digisac_contact_hydration(
    external_id: str,
) -> dict[str, Any] | None:
    from src.core.digisac_contact_repository import (
        get_digisac_contact_hydration as repository_get_digisac_contact_hydration,
    )

    return await repository_get_digisac_contact_hydration(external_id)


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
    created_timestamp = _parse_timestamp(created_at)
    public_id = uuid7() if _schema_capabilities.classification_identity_columns else None
    normalized_idempotency_key = (
        idempotency_key.strip()
        if (
            _schema_capabilities.classification_idempotency_index
            and isinstance(idempotency_key, str)
            and idempotency_key.strip()
        )
        else None
    )
    with _get_pool().connection() as connection:
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
            if _schema_capabilities.classification_identity_columns:
                conflict = (
                    """
                    ON CONFLICT (idempotency_key)
                    WHERE idempotency_key IS NOT NULL
                    DO NOTHING
                    """
                    if (
                        normalized_idempotency_key
                        and _schema_capabilities.classification_idempotency_index
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
            if _schema_capabilities.classification_messages:
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


# Keep the historical facade imports stable while the implementation lives in
# the focused cycle repository.
from src.core import conversation_cycle_repository as _conversation_cycle_repository

BLOCKED_CYCLE_STATUSES = _conversation_cycle_repository.BLOCKED_CYCLE_STATUSES
RECOVERABLE_CYCLE_STATUSES = _conversation_cycle_repository.RECOVERABLE_CYCLE_STATUSES
TERMINAL_CYCLE_STATUSES = _conversation_cycle_repository.TERMINAL_CYCLE_STATUSES
claim_cycle = _conversation_cycle_repository.claim_cycle
close_cycle = _conversation_cycle_repository.close_cycle
create_open_cycle = _conversation_cycle_repository.create_open_cycle
get_content_states = _conversation_cycle_repository.get_content_states
get_cycle = _conversation_cycle_repository.get_cycle
get_cycle_metrics = _conversation_cycle_repository.get_cycle_metrics
get_cycle_result = _conversation_cycle_repository.get_cycle_result
get_latest_cycle = _conversation_cycle_repository.get_latest_cycle
get_previous_cycle = _conversation_cycle_repository.get_previous_cycle
get_recoverable_cycles = _conversation_cycle_repository.get_recoverable_cycles
list_cycles = _conversation_cycle_repository.list_cycles
release_cycle_publication = _conversation_cycle_repository.release_cycle_publication
save_cycle_messages = _conversation_cycle_repository.save_cycle_messages
transition_cycle = _conversation_cycle_repository.transition_cycle
wake_unblocked_media_cycles = _conversation_cycle_repository.wake_unblocked_media_cycles


def _resolve_user_names_sync(user_ids: Sequence[str]) -> dict[str, str]:
    unique = list(dict.fromkeys(item for item in user_ids if item))
    if not unique:
        return {}
    with _get_pool().connection() as connection:
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


# Keep the historical facade imports stable while the implementation lives in
# the focused durable-media repository.
from src.core import durable_media_repository as _durable_media_repository

get_completed_image_extractions = (
    _durable_media_repository.get_completed_image_extractions
)
get_completed_transcriptions = _durable_media_repository.get_completed_transcriptions
get_image_extraction = _durable_media_repository.get_image_extraction
get_pending_content_extractions = (
    _durable_media_repository.get_pending_content_extractions
)
get_transcription = _durable_media_repository.get_transcription
recover_stale_image_extractions = (
    _durable_media_repository.recover_stale_image_extractions
)
recover_stale_transcriptions = _durable_media_repository.recover_stale_transcriptions
release_image_publication = _durable_media_repository.release_image_publication
release_transcription_publication = (
    _durable_media_repository.release_transcription_publication
)
reserve_image_extraction = _durable_media_repository.reserve_image_extraction
reserve_transcription = _durable_media_repository.reserve_transcription
set_image_extraction_status = _durable_media_repository.set_image_extraction_status
set_transcription_status = _durable_media_repository.set_transcription_status


# Keep the historical facade imports stable while the implementation lives in
# the focused ticket-assignment repository.
from src.core import ticket_assignment_repository as _ticket_assignment_repository

record_ticket_assignment = _ticket_assignment_repository.record_ticket_assignment
resolve_ticket_assignments = _ticket_assignment_repository.resolve_ticket_assignments
