"""PostgreSQL repository for the generic DigiSac webhook event ledger."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from src.core.db import get_database_pool


logger = logging.getLogger(__name__)

EVENT_IDEMPOTENCY_TTL_SECONDS = 3600
DEFAULT_CLEANUP_BATCH_SIZE = 100
MAX_CLEANUP_BATCH_SIZE = 1_000
_EVENT_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class WebhookEventDecision:
    """Sanitized result of an atomic event-ledger decision."""

    accepted: bool
    outcome: str


@dataclass(frozen=True)
class WebhookEventCleanupReport:
    """Aggregate cleanup evidence without returning event identities."""

    before_count: int
    removed_count: int
    after_count: int
    batch_size: int


def _validate_event_digest(event_digest: str) -> str:
    if not isinstance(event_digest, str) or not _EVENT_DIGEST_RE.fullmatch(
        event_digest
    ):
        raise ValueError("event_digest must be a lowercase SHA-256 digest")
    return event_digest


def _record_webhook_event_sync(event_digest: str) -> WebhookEventDecision:
    digest = _validate_event_digest(event_digest)
    with get_database_pool().connection() as connection:
        row = connection.execute(
            """
            INSERT INTO webhook_event_keys (
                event_digest, first_seen_at, expires_at
            ) VALUES (
                %s, CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP + INTERVAL '1 hour'
            )
            ON CONFLICT (event_digest) DO UPDATE
            SET first_seen_at = EXCLUDED.first_seen_at,
                expires_at = EXCLUDED.expires_at
            WHERE webhook_event_keys.expires_at <= CURRENT_TIMESTAMP
            RETURNING (xmax = 0) AS inserted
            """,
            (digest,),
        ).fetchone()
    if row is None:
        decision = WebhookEventDecision(False, "duplicate")
    elif bool(row[0]):
        decision = WebhookEventDecision(True, "accepted")
    else:
        decision = WebhookEventDecision(True, "expired_replaced")
    logger.info(
        "Webhook event idempotency decision: outcome=%s",
        decision.outcome,
    )
    return decision


async def record_webhook_event(event_digest: str) -> WebhookEventDecision:
    """Atomically accept a digest or report its active/expired conflict."""
    return await asyncio.to_thread(_record_webhook_event_sync, event_digest)


async def try_mark_webhook_event(event_digest: str) -> bool:
    """Return whether this event is the current one-hour winner."""
    return (await record_webhook_event(event_digest)).accepted


def _cleanup_expired_webhook_event_keys_sync(
    batch_size: int,
) -> WebhookEventCleanupReport:
    if not 1 <= batch_size <= MAX_CLEANUP_BATCH_SIZE:
        raise ValueError(
            f"batch_size must be between 1 and {MAX_CLEANUP_BATCH_SIZE}"
        )
    with get_database_pool().connection() as connection:
        with connection.transaction():
            before = connection.execute(
                """
                SELECT count(*)
                FROM webhook_event_keys
                WHERE expires_at <= CURRENT_TIMESTAMP
                """
            ).fetchone()
            if before is None:
                raise RuntimeError("cleanup count query returned no row")
            removed_rows = connection.execute(
                """
                WITH expired AS (
                    SELECT event_digest
                    FROM webhook_event_keys
                    WHERE expires_at <= CURRENT_TIMESTAMP
                    ORDER BY expires_at, event_digest
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                DELETE FROM webhook_event_keys AS ledger
                USING expired
                WHERE ledger.event_digest = expired.event_digest
                RETURNING 1
                """,
                (batch_size,),
            ).fetchall()
            after = connection.execute(
                """
                SELECT count(*)
                FROM webhook_event_keys
                WHERE expires_at <= CURRENT_TIMESTAMP
                """
            ).fetchone()
            if after is None:
                raise RuntimeError("cleanup post-count query returned no row")
    report = WebhookEventCleanupReport(
        before_count=int(before[0]),
        removed_count=len(removed_rows),
        after_count=int(after[0]),
        batch_size=batch_size,
    )
    logger.info(
        "Webhook event idempotency cleanup: before=%s removed=%s after=%s batch_size=%s",
        report.before_count,
        report.removed_count,
        report.after_count,
        report.batch_size,
    )
    return report


async def cleanup_expired_webhook_event_keys(
    batch_size: int = DEFAULT_CLEANUP_BATCH_SIZE,
) -> WebhookEventCleanupReport:
    """Delete at most one bounded batch of expired ledger rows."""
    return await asyncio.to_thread(
        _cleanup_expired_webhook_event_keys_sync,
        batch_size,
    )


def _count_expired_webhook_event_keys_sync() -> int:
    with get_database_pool().connection() as connection:
        row = connection.execute(
            """
            SELECT count(*)
            FROM webhook_event_keys
            WHERE expires_at <= CURRENT_TIMESTAMP
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("expired event-key count query returned no row")
    return int(row[0])


async def count_expired_webhook_event_keys() -> int:
    """Count expired rows without reading or returning event identities."""
    return await asyncio.to_thread(_count_expired_webhook_event_keys_sync)


def _import_legacy_webhook_event_keys_sync(
    entries: Sequence[tuple[str, int]],
) -> int:
    normalized: list[tuple[str, int]] = []
    for event_digest, ttl_seconds in entries:
        digest = _validate_event_digest(event_digest)
        if not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
            raise ValueError("legacy marker TTL must be positive")
        normalized.append((digest, ttl_seconds))
    imported = 0
    with get_database_pool().connection() as connection:
        with connection.transaction():
            captured_at = datetime.now(timezone.utc)
            for digest, ttl_seconds in normalized:
                expires_at = captured_at + timedelta(seconds=ttl_seconds)
                first_seen_at = expires_at - timedelta(
                    seconds=EVENT_IDEMPOTENCY_TTL_SECONDS
                )
                row = connection.execute(
                    """
                    INSERT INTO webhook_event_keys (
                        event_digest, first_seen_at, expires_at
                    ) VALUES (%s, %s, %s)
                    ON CONFLICT (event_digest) DO UPDATE
                    SET first_seen_at = EXCLUDED.first_seen_at,
                        expires_at = EXCLUDED.expires_at
                    WHERE webhook_event_keys.expires_at <= CURRENT_TIMESTAMP
                    RETURNING 1
                    """,
                    (digest, first_seen_at, expires_at),
                ).fetchone()
                if row is not None:
                    imported += 1
    logger.info(
        "Legacy webhook idempotency handoff: imported=%s inspected=%s",
        imported,
        len(normalized),
    )
    return imported


async def import_legacy_webhook_event_keys(
    entries: Sequence[tuple[str, int]],
) -> int:
    """Import reviewed live Redis markers without deleting their source keys."""
    return await asyncio.to_thread(_import_legacy_webhook_event_keys_sync, entries)
