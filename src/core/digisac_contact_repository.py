"""PostgreSQL repository for DigiSac contact identity and hydration state.

The public async functions in this module use the process-local pool owned by
``src.core.db``.  Contact persistence stays here so its durable invariants are
isolated from unrelated database domains while the database facade remains the
compatibility boundary used by existing callers.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from psycopg.rows import dict_row

from src.core.config import settings
from src.core.db import _parse_timestamp, _row_dict, get_database_pool
from src.core.digisac_client import DigisacContact


_CONTACT_PROVIDER_FIELDS = (
    "name",
    "alternative_name",
    "internal_name",
    "raw_number",
    "normalized_number",
    "raw_email",
    "normalized_email",
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
                raw_number, normalized_number, raw_email, normalized_email,
                is_group, account_id,
                service_id, provider_created_at, provider_updated_at,
                provider_deleted_at, last_seen_at, last_source
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
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
                raw_email = %s,
                normalized_email = %s,
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


def upsert_digisac_contact_cursor(
    cursor: Any,
    contact: DigisacContact,
    normalized_source: str,
    observed: datetime,
) -> Mapping[str, Any]:
    """Publish one contact through the repository's timestamp-aware boundary."""
    return _upsert_digisac_contact_cursor(
        cursor, contact, normalized_source, observed
    )


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
    with get_database_pool().connection() as connection:
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
    with get_database_pool().connection() as connection:
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
    with get_database_pool().connection() as connection:
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
    with get_database_pool().connection() as connection:
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
    with get_database_pool().connection() as connection:
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
    with get_database_pool().connection() as connection:
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
    with get_database_pool().connection() as connection:
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
    with get_database_pool().connection() as connection:
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
