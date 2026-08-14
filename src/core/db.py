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
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence, cast
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from src.core.config import settings
from src.core.digisac_client import DigisacContact
from src.core.identifiers import uuid7
from src.core.intents import normalize_intent_type

logger = logging.getLogger(__name__)
CURRENT_SCHEMA_REVISION = "0016_digisac_contact_identity"
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


_CONTACT_PROVIDER_FIELDS = (
    "name",
    "alternative_name",
    "internal_name",
    "raw_number",
    "normalized_number",
    "is_group",
    "account_id",
    "service_id",
    "provider_created_at",
    "provider_updated_at",
    "provider_deleted_at",
)


def _contact_has_metadata(row: Mapping[str, Any]) -> bool:
    return (
        row.get("provider_updated_at") is not None
        or row.get("provider_created_at") is not None
    )


def _upsert_digisac_contact_cursor(
    cursor: Any,
    contact: DigisacContact,
    normalized_source: str,
    observed: datetime,
) -> Mapping[str, Any]:
    values = {field: getattr(contact, field) for field in _CONTACT_PROVIDER_FIELDS}
    row = cursor.execute(
        """
        SELECT *
        FROM digisac_contacts
        WHERE external_id = %s
        FOR UPDATE
        """,
        (contact.external_id,),
    ).fetchone()
    if row is None:
        inserted = cursor.execute(
            """
            INSERT INTO digisac_contacts (
                external_id, name, alternative_name, internal_name,
                raw_number, normalized_number, is_group, account_id,
                service_id, provider_created_at, provider_updated_at,
                provider_deleted_at, last_seen_at, last_source
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s
            )
            RETURNING *
            """,
            (
                contact.external_id,
                *[values[field] for field in _CONTACT_PROVIDER_FIELDS],
                observed,
                normalized_source,
            ),
        ).fetchone()
        if inserted is None:
            raise RuntimeError("PostgreSQL did not return contact")
        row = inserted
    else:
        old_updated = row["provider_updated_at"]
        new_updated = contact.provider_updated_at
        older = (
            old_updated is not None
            and new_updated is not None
            and new_updated < old_updated
        )
        unordered = old_updated is not None and new_updated is None
        merged = dict(row)
        for field in _CONTACT_PROVIDER_FIELDS:
            incoming = values[field]
            if incoming is None or older or (unordered and row[field] is not None):
                merged[field] = row[field]
            else:
                merged[field] = incoming
        if old_updated is not None and (
            new_updated is None or new_updated < old_updated
        ):
            merged["provider_updated_at"] = old_updated
        elif new_updated is not None:
            merged["provider_updated_at"] = new_updated
        merged["last_seen_at"] = max(row["last_seen_at"], observed)
        merged["last_source"] = (
            row["last_source"] if older or unordered else normalized_source
        )
        updated = cursor.execute(
            """
            UPDATE digisac_contacts
            SET name = %s,
                alternative_name = %s,
                internal_name = %s,
                raw_number = %s,
                normalized_number = %s,
                is_group = %s,
                account_id = %s,
                service_id = %s,
                provider_created_at = %s,
                provider_updated_at = %s,
                provider_deleted_at = %s,
                last_seen_at = %s,
                last_source = %s,
                updated_at = now()
            WHERE id = %s
            RETURNING *
            """,
            (
                *(merged[field] for field in _CONTACT_PROVIDER_FIELDS),
                merged["last_seen_at"],
                merged["last_source"],
                row["id"],
            ),
        ).fetchone()
        if updated is None:
            raise RuntimeError("PostgreSQL did not update contact")
        row = updated
    if normalized_source == "ticket_webhook":
        cursor.execute(
            """
            UPDATE digisac_contact_hydrations
            SET status = 'succeeded',
                next_attempt_at = NULL,
                lease_until = NULL,
                completed_at = COALESCE(completed_at, now()),
                failure_category = NULL,
                failure_message = NULL,
                updated_at = now()
            WHERE contact_id = %s
            """,
            (row["id"],),
        )
    return row


def _upsert_digisac_contact_sync(
    contact: DigisacContact,
    source: str,
    observed_at: str | datetime | None = None,
) -> dict[str, Any]:
    normalized_source = source.strip()
    if not normalized_source:
        raise ValueError("contact source must not be blank")
    observed = (
        _parse_timestamp(observed_at)
        if isinstance(observed_at, (str, datetime))
        else datetime.now(timezone.utc)
    )
    with _get_pool().connection() as connection:
        with connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                row = _upsert_digisac_contact_cursor(
                    cursor, contact, normalized_source, observed
                )
    result = _row_dict(row)
    if result is None:
        raise RuntimeError("PostgreSQL did not return contact state")
    return result


async def upsert_digisac_contact(
    contact: DigisacContact,
    *,
    source: str,
    observed_at: str | datetime | None = None,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        _upsert_digisac_contact_sync, contact, source, observed_at
    )


def _publish_digisac_contact_backfill_sync(
    contacts: Sequence[DigisacContact],
    observed_at: str | datetime | None = None,
) -> dict[str, int]:
    observed = (
        _parse_timestamp(observed_at)
        if isinstance(observed_at, (str, datetime))
        else datetime.now(timezone.utc)
    )
    unique_contacts = tuple(
        {contact.external_id: contact for contact in contacts}.values()
    )
    with _get_pool().connection() as connection:
        with connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("cai:digisac_contacts:full_backfill",),
            )
            with connection.cursor(row_factory=dict_row) as cursor:
                for contact in unique_contacts:
                    _upsert_digisac_contact_cursor(
                        cursor, contact, "contacts_backfill", observed
                    )
    return {
        "published_count": len(unique_contacts),
        "unique_count": len(unique_contacts),
    }


async def publish_digisac_contact_backfill(
    contacts: Sequence[DigisacContact],
    *,
    observed_at: str | datetime | None = None,
) -> dict[str, int]:
    """Publish a validated snapshot atomically under a process-shared lock."""
    return await asyncio.to_thread(
        _publish_digisac_contact_backfill_sync, contacts, observed_at
    )


def _request_digisac_contact_hydration_sync(
    external_id: str, requested_at: str | datetime | None = None
) -> bool:
    normalized_id = external_id.strip()
    if not normalized_id:
        return False
    requested = (
        _parse_timestamp(requested_at)
        if isinstance(requested_at, (str, datetime))
        else datetime.now(timezone.utc)
    )
    with _get_pool().connection() as connection:
        with connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                contact = cursor.execute(
                    """
                    INSERT INTO digisac_contacts (
                        external_id, last_seen_at, last_source
                    ) VALUES (%s, %s, 'message_reference')
                    ON CONFLICT (external_id) DO UPDATE SET
                        last_seen_at = GREATEST(
                            digisac_contacts.last_seen_at, EXCLUDED.last_seen_at
                        ),
                        updated_at = now()
                    RETURNING *
                    """,
                    (normalized_id, requested),
                ).fetchone()
                if contact is None:
                    raise RuntimeError("PostgreSQL did not return contact placeholder")
                state = cursor.execute(
                    """
                    SELECT *
                    FROM digisac_contact_hydrations
                    WHERE contact_id = %s
                    FOR UPDATE
                    """,
                    (contact["id"],),
                ).fetchone()
                if state is not None and state["status"] in {"pending", "running"}:
                    return False
                if _contact_has_metadata(contact) and (
                    state is None or state["status"] == "succeeded"
                ):
                    return False
                if state is None:
                    cursor.execute(
                        """
                        INSERT INTO digisac_contact_hydrations (
                            contact_id, status, requested_at
                        ) VALUES (%s, 'pending', %s)
                        """,
                        (contact["id"], requested),
                    )
                    return True
                if state["status"] == "running":
                    return False
                cursor.execute(
                    """
                    UPDATE digisac_contact_hydrations
                    SET status = 'pending',
                        requested_at = LEAST(requested_at, %s),
                        next_attempt_at = NULL,
                        lease_until = NULL,
                        failure_category = NULL,
                        failure_message = NULL,
                        updated_at = now()
                    WHERE contact_id = %s
                    """,
                    (requested, contact["id"]),
                )
                return True


async def request_digisac_contact_hydration(
    external_id: str, *, requested_at: str | datetime | None = None
) -> bool:
    return await asyncio.to_thread(
        _request_digisac_contact_hydration_sync, external_id, requested_at
    )


def _claim_digisac_contact_hydration_sync(lease_seconds: int) -> dict[str, Any] | None:
    if lease_seconds <= 0:
        raise ValueError("contact hydration lease must be positive")
    now = datetime.now(timezone.utc)
    lease_until = now + timedelta(seconds=lease_seconds)
    with _get_pool().connection() as connection:
        with connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    """
                    SELECT h.contact_id, c.external_id, h.attempt_count
                    FROM digisac_contact_hydrations AS h
                    JOIN digisac_contacts AS c ON c.id = h.contact_id
                    WHERE (
                        h.status IN ('pending', 'failed')
                        AND (
                            h.next_attempt_at IS NULL
                            OR h.next_attempt_at <= %s
                        )
                    ) OR (
                        h.status = 'running'
                        AND h.lease_until IS NOT NULL
                        AND h.lease_until <= %s
                    )
                    ORDER BY h.requested_at ASC, h.contact_id ASC
                    FOR UPDATE OF h SKIP LOCKED
                    LIMIT 1
                    """,
                    (now, now),
                ).fetchone()
                if row is None:
                    return None
                updated = cursor.execute(
                    """
                    UPDATE digisac_contact_hydrations
                    SET status = 'running',
                        attempt_count = attempt_count + 1,
                        last_attempt_at = %s,
                        next_attempt_at = NULL,
                        lease_until = %s,
                        updated_at = now()
                    WHERE contact_id = %s
                    RETURNING attempt_count
                    """,
                    (now, lease_until, row["contact_id"]),
                ).fetchone()
                if updated is None:
                    raise RuntimeError("PostgreSQL did not claim contact hydration")
                return {
                    "contact_id": row["contact_id"],
                    "external_id": row["external_id"],
                    "attempt_count": updated["attempt_count"],
                    "lease_until": lease_until,
                }


async def claim_digisac_contact_hydration(
    *, lease_seconds: int | None = None
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        _claim_digisac_contact_hydration_sync,
        lease_seconds or settings.finalization_lease_seconds,
    )


def _mark_digisac_contact_hydration_success_sync(
    external_id: str, expected_lease_until: datetime
) -> bool:
    with _get_pool().connection() as connection:
        with connection.transaction():
            result = connection.execute(
                """
                UPDATE digisac_contact_hydrations AS h
                SET status = 'succeeded',
                    next_attempt_at = NULL,
                    lease_until = NULL,
                    completed_at = now(),
                    failure_category = NULL,
                    failure_message = NULL,
                    updated_at = now()
                FROM digisac_contacts AS c
                WHERE c.id = h.contact_id
                  AND c.external_id = %s
                  AND h.status = 'running'
                  AND h.lease_until = %s
                """,
                (external_id, expected_lease_until),
            )
            return result.rowcount == 1


async def mark_digisac_contact_hydration_success(
    external_id: str, *, expected_lease_until: datetime
) -> bool:
    return await asyncio.to_thread(
        _mark_digisac_contact_hydration_success_sync,
        external_id,
        expected_lease_until,
    )


def _safe_failure_category(category: str) -> str:
    safe = "".join(
        character
        for character in category.lower()
        if character.isascii() and (character.isalnum() or character in "_:-")
    )
    return safe[:80] or "unknown"


def _mark_digisac_contact_hydration_failure_sync(
    external_id: str,
    category: str,
    *,
    retryable: bool,
    expected_lease_until: datetime,
    max_attempts: int | None = None,
) -> bool:
    safe_category = _safe_failure_category(category)
    limit = max_attempts or settings.digisac_history_max_attempts
    with _get_pool().connection() as connection:
        with connection.transaction():
            row = connection.execute(
                """
                SELECT h.attempt_count
                FROM digisac_contact_hydrations AS h
                JOIN digisac_contacts AS c ON c.id = h.contact_id
                WHERE c.external_id = %s
                  AND h.status = 'running'
                  AND h.lease_until = %s
                FOR UPDATE
                """,
                (external_id, expected_lease_until),
            ).fetchone()
            if row is None:
                return False
            attempt_count = int(row[0])
            should_retry = retryable and attempt_count < limit
            delay = min(
                60.0,
                settings.digisac_history_retry_base_seconds
                * (2 ** max(0, attempt_count - 1)),
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE digisac_contact_hydrations AS h
                    SET status = 'failed',
                        next_attempt_at = %s,
                        lease_until = NULL,
                        completed_at = NULL,
                        failure_category = %s,
                        failure_message = %s,
                        updated_at = now()
                    FROM digisac_contacts AS c
                    WHERE c.id = h.contact_id
                      AND c.external_id = %s
                      AND h.lease_until = %s
                    """,
                    (
                        datetime.now(timezone.utc) + timedelta(seconds=delay)
                        if should_retry
                        else None,
                        safe_category,
                        f"contact hydration failed: {safe_category}",
                        external_id,
                        expected_lease_until,
                    ),
                )
            return True


async def mark_digisac_contact_hydration_failure(
    external_id: str,
    category: str,
    *,
    retryable: bool,
    expected_lease_until: datetime,
    max_attempts: int | None = None,
) -> bool:
    return await asyncio.to_thread(
        _mark_digisac_contact_hydration_failure_sync,
        external_id,
        category,
        retryable=retryable,
        expected_lease_until=expected_lease_until,
        max_attempts=max_attempts,
    )


def _get_digisac_contact_sync(external_id: str) -> dict[str, Any] | None:
    with _get_pool().connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute(
                "SELECT * FROM digisac_contacts WHERE external_id = %s",
                (external_id,),
            ).fetchone()
    return _row_dict(row)


async def get_digisac_contact(external_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_get_digisac_contact_sync, external_id)


def _get_digisac_contact_hydration_sync(
    external_id: str,
) -> dict[str, Any] | None:
    with _get_pool().connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute(
                """
                SELECT h.*
                FROM digisac_contact_hydrations AS h
                JOIN digisac_contacts AS c ON c.id = h.contact_id
                WHERE c.external_id = %s
                """,
                (external_id,),
            ).fetchone()
    return _row_dict(row)


async def get_digisac_contact_hydration(
    external_id: str,
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        _get_digisac_contact_hydration_sync, external_id
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


def _reserve_content_sync(
    table: str, message_id: str, conversation_id: str | None, model: str
) -> bool:
    if table not in {"message_transcriptions", "message_image_extractions"}:
        raise ValueError("Unsupported content table")
    now = datetime.now(timezone.utc)
    with _get_pool().connection() as connection:
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
                (message_id, conversation_id, model, now, now, now, now),
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
    next_attempt_at: datetime | None = None,
    expected_statuses: Sequence[str] | None = None,
    expected_updated_at: datetime | None = None,
) -> datetime | None:
    if table not in {"message_transcriptions", "message_image_extractions"}:
        raise ValueError("Unsupported content table")
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
    with _get_pool().connection() as connection:
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
                    updated_at = CURRENT_TIMESTAMP, completed_at = %s
                WHERE message_id = %s
                  AND status = ANY(%s)
                  AND (%s::timestamptz IS NULL OR updated_at = %s)
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
                    completed_at,
                    message_id,
                    list(expected_statuses),
                    expected_updated_at,
                    expected_updated_at,
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
    )


def _get_content_sync(table: str, message_id: str) -> dict[str, Any] | None:
    if table not in {"message_transcriptions", "message_image_extractions"}:
        raise ValueError("Unsupported content table")
    with _get_pool().connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute(
                sql.SQL(
                    "SELECT * FROM {table_name} WHERE message_id = %s"
                ).format(table_name=sql.Identifier(table)),
                (message_id,),
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
    with _get_pool().connection() as connection:
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
        _reserve_content_sync, "message_image_extractions", message_id, conversation_id, model
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
    if table not in {"message_transcriptions", "message_image_extractions"}:
        raise ValueError("Unsupported content table")
    if lease_seconds < 1 or batch_size < 1:
        raise ValueError("Recovery lease and batch size must be positive")
    with _get_pool().connection() as connection:
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
    if table not in {"message_transcriptions", "message_image_extractions"}:
        raise ValueError("Unsupported content table")
    with _get_pool().connection() as connection:
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
    with _get_pool().connection() as connection:
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
    if not _schema_capabilities.conversation_cycles:
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


def _create_open_cycle_sync(
    *,
    conversation_id: str,
    started_at: str | datetime,
    open_event_key: str,
    start_strategy: str,
) -> tuple[dict[str, Any], bool]:
    _require_cycle_schema()
    now = datetime.now(timezone.utc)
    public_id = uuid7()
    with _get_pool().connection() as connection:
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
                        status, next_attempt_at, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
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
                        now,
                        now,
                    ),
                )
            else:
                public_id = existing[0]
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
) -> tuple[dict[str, Any], bool]:
    _require_cycle_schema()
    now = datetime.now(timezone.utc)
    closed_timestamp = _parse_timestamp(closed_at)
    with _get_pool().connection() as connection:
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
                            next_attempt_at = %s, updated_at = %s,
                            error_phase = NULL, error_message = NULL
                        WHERE id = %s
                        """,
                        (
                            protocol,
                            closed_timestamp,
                            close_event_key,
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
                            next_attempt_at, created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, NULL, %s, %s, %s, 'pending',
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
    with _get_pool().connection() as connection:
        return _cycle_row(connection, public_id)


async def get_cycle(public_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_get_cycle_sync, public_id)


def _get_latest_cycle_sync(conversation_id: str) -> dict[str, Any] | None:
    _require_cycle_schema()
    with _get_pool().connection() as connection:
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
    with _get_pool().connection() as connection:
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
    with _get_pool().connection() as connection:
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
    with _get_pool().connection() as connection:
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
    with _get_pool().connection() as connection:
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


def _recoverable_cycles_sync(*, limit: int = 100) -> list[dict[str, Any]]:
    _require_cycle_schema()
    with _get_pool().connection() as connection:
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
    with _get_pool().connection() as connection:
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
    with _get_pool().connection() as connection:
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
                              JOIN message_image_extractions AS image
                                ON image.message_id = message.message_id
                              WHERE message.cycle_id = cycle.id
                                AND message.message_type = 'image'
                                AND image.status = 'failed'
                                AND image.attempt_count >= %s
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
                    (max_attempts, max(1, limit)),
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
    with _get_pool().connection() as connection:
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
    with _get_pool().connection() as connection:
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
    if not _schema_capabilities.conversation_cycles:
        return {}
    with _get_pool().connection() as connection:
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


def _get_cycle_result_sync(public_id: str) -> dict[str, Any] | None:
    _require_cycle_schema()
    with _get_pool().connection() as connection:
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
