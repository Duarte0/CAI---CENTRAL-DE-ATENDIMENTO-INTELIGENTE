"""PostgreSQL persistence for the CAI pipeline.

The module keeps the existing async-facing API used by FastAPI and the workers,
while using one thread-safe synchronous psycopg pool per process.  Schema
creation is deliberately not performed here; deploys must apply Alembic
migrations before starting the application.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping, Sequence

import psycopg
from psycopg_pool import ConnectionPool

from src.core.config import settings
from src.core.digisac_client import DigisacContact

if TYPE_CHECKING:
    from src.core.digisac_contact_repository import ContactHydrationRequestResult
    from src.core.webhook_event_repository import (
        WebhookEventCleanupReport,
        WebhookEventDecision,
    )

logger = logging.getLogger(__name__)
CURRENT_SCHEMA_REVISION = "0025_webhook_event_keys"
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
        "0025_webhook_event_keys",
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
    webhook_event_keys: bool = False


_schema_capabilities = SchemaCapabilities()


def _parse_timestamp(  # pyright: ignore[reportUnusedFunction]
    value: str | datetime,
) -> datetime:
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
            set_config('idle_in_transaction_session_timeout', %s, false),
            set_config('timezone', %s, false)
        """,
        (
            f"{settings.database_statement_timeout_ms}ms",
            f"{settings.database_lock_timeout_ms}ms",
            f"{settings.database_idle_transaction_timeout_ms}ms",
            settings.app_timezone,
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
                ) IS NOT NULL,
                to_regclass(
                    current_schema() || '.webhook_event_keys'
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
        webhook_event_keys=bool(capabilities[5]),
    )
    logger.info(
        "PostgreSQL schema verified: revision=%s identity=%s "
        "idempotency=%s normalized_messages=%s conversation_cycles=%s "
        "webhook_event_keys=%s",
        row[0],
        _schema_capabilities.classification_identity_columns,
        _schema_capabilities.classification_idempotency_index,
        _schema_capabilities.classification_messages,
        _schema_capabilities.conversation_cycles,
        _schema_capabilities.webhook_event_keys,
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
    if not _schema_capabilities.webhook_event_keys:
        raise RuntimeError(
            "webhook event idempotency requires migration "
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


# Keep the historical facade imports stable while the implementation lives in
# the focused DigiSac directory repository.
from src.core import digisac_directory_repository as _digisac_directory_repository

directory_refresh_is_due = _digisac_directory_repository.directory_refresh_is_due
mark_directory_sync_attempt = (
    _digisac_directory_repository.mark_directory_sync_attempt
)
upsert_digisac_directory = _digisac_directory_repository.upsert_digisac_directory


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


async def request_digisac_contact_hydration_result(
    external_id: str, *, requested_at: str | datetime | None = None
) -> ContactHydrationRequestResult:
    from src.core.digisac_contact_repository import (
        request_digisac_contact_hydration_result as repository_request_digisac_contact_hydration_result,
    )

    return await repository_request_digisac_contact_hydration_result(
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


from src.core import classification_repository as _classification_repository

ClassificationIdentity = _classification_repository.ClassificationIdentity
classification_exists = _classification_repository.classification_exists
insert_classification = _classification_repository.insert_classification
ticket_has_classification = _classification_repository.ticket_has_classification
update_analysis_protocol = _classification_repository.update_analysis_protocol


# Keep the historical facade imports stable while the implementation lives in
# the focused cycle repository.
from src.core import conversation_cycle_repository as _conversation_cycle_repository

BLOCKED_CYCLE_STATUSES = _conversation_cycle_repository.BLOCKED_CYCLE_STATUSES
RECOVERABLE_CYCLE_STATUSES = _conversation_cycle_repository.RECOVERABLE_CYCLE_STATUSES
TERMINAL_CYCLE_STATUSES = _conversation_cycle_repository.TERMINAL_CYCLE_STATUSES
claim_cycle = _conversation_cycle_repository.claim_cycle
claim_next_cycle = _conversation_cycle_repository.claim_next_cycle
close_cycle = _conversation_cycle_repository.close_cycle
create_open_cycle = _conversation_cycle_repository.create_open_cycle
get_content_states = _conversation_cycle_repository.get_content_states
get_cycle = _conversation_cycle_repository.get_cycle
get_cycles_by_public_ids = _conversation_cycle_repository.get_cycles_by_public_ids
get_cycle_metrics = _conversation_cycle_repository.get_cycle_metrics
get_cycle_work_metrics = _conversation_cycle_repository.get_cycle_work_metrics
get_cycle_result = _conversation_cycle_repository.get_cycle_result
get_latest_cycle = _conversation_cycle_repository.get_latest_cycle
get_previous_cycle = _conversation_cycle_repository.get_previous_cycle
get_recoverable_cycles = _conversation_cycle_repository.get_recoverable_cycles
list_cycles = _conversation_cycle_repository.list_cycles
release_cycle_publication = _conversation_cycle_repository.release_cycle_publication
save_cycle_messages = _conversation_cycle_repository.save_cycle_messages
transition_cycle = _conversation_cycle_repository.transition_cycle
wake_unblocked_media_cycles = _conversation_cycle_repository.wake_unblocked_media_cycles


async def cleanup_expired_webhook_event_keys(
    batch_size: int = 100,
) -> WebhookEventCleanupReport:
    from src.core.webhook_event_repository import (
        cleanup_expired_webhook_event_keys as repository_cleanup,
    )

    return await repository_cleanup(batch_size)


async def count_expired_webhook_event_keys() -> int:
    from src.core.webhook_event_repository import (
        count_expired_webhook_event_keys as repository_count,
    )

    return await repository_count()


async def import_legacy_webhook_event_keys(entries: Sequence[tuple[str, int]]) -> int:
    from src.core.webhook_event_repository import (
        import_legacy_webhook_event_keys as repository_import,
    )

    return await repository_import(entries)


async def record_webhook_event(event_digest: str) -> WebhookEventDecision:
    from src.core.webhook_event_repository import (
        record_webhook_event as repository_record,
    )

    return await repository_record(event_digest)


async def try_mark_webhook_event(event_digest: str) -> bool:
    from src.core.webhook_event_repository import (
        try_mark_webhook_event as repository_try_mark,
    )

    return await repository_try_mark(event_digest)


# Keep the historical facade import stable for IA sender-name projection.
resolve_user_names = _digisac_directory_repository.resolve_user_names


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
claim_next_transcription = _durable_media_repository.claim_next_transcription
claim_next_image_extraction = (
    _durable_media_repository.claim_next_image_extraction
)
get_transcription = _durable_media_repository.get_transcription
get_transcription_work_metrics = (
    _durable_media_repository.get_transcription_work_metrics
)
get_image_extraction_work_metrics = (
    _durable_media_repository.get_image_extraction_work_metrics
)
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
