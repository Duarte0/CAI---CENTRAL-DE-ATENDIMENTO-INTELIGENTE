"""Durable, need-based hydration of individual DigiSac contacts."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from src.core.config import settings
from src.core.db import (
    claim_digisac_contact_hydration,
    mark_digisac_contact_hydration_failure,
    mark_digisac_contact_hydration_success,
    request_digisac_contact_hydration_result,
    upsert_digisac_contact,
)
from src.core.digisac_contact_repository import ContactHydrationRequestResult
from src.core.digisac_client import (
    DigisacClient,
    DigisacClientError,
    DigisacContact,
)


logger = logging.getLogger(__name__)


class ContactHydrationClient(Protocol):
    def get_contact(self, contact_id: str) -> DigisacContact:
        """Fetch one typed contact from the provider."""


async def request_contact_hydration(
    external_id: str, *, requested_at: str | None = None
) -> bool:
    """Record a deduplicated need without contacting DigiSac."""
    result = await request_contact_hydration_result(
        external_id, requested_at=requested_at
    )
    return result.requested


async def request_contact_hydration_result(
    external_id: str, *, requested_at: str | None = None
) -> ContactHydrationRequestResult:
    """Return the sanitized decision for a normal message-triggered request."""
    return await request_digisac_contact_hydration_result(
        external_id, requested_at=requested_at
    )


async def process_one_contact_hydration(
    *,
    client: ContactHydrationClient | None = None,
    lease_seconds: int | None = None,
) -> bool:
    claim = await claim_digisac_contact_hydration(lease_seconds=lease_seconds)
    if claim is None:
        return False
    external_id = str(claim["external_id"])
    try:
        active_client = client or DigisacClient()
        contact = await asyncio.to_thread(
            active_client.get_contact, external_id
        )
        await upsert_digisac_contact(
            contact,
            source="contact_hydration",
        )
    except DigisacClientError as exc:
        retryable = exc.category in {"timeout", "connection", "transient"}
        await mark_digisac_contact_hydration_failure(
            external_id,
            exc.category,
            retryable=retryable,
            expected_lease_until=claim["lease_until"],
        )
        logger.warning(
            "DigiSac contact hydration failed: contact_id=%s category=%s "
            "retryable=%s",
            external_id,
            exc.category,
            retryable,
        )
    except Exception:
        await mark_digisac_contact_hydration_failure(
            external_id,
            "unexpected",
            retryable=False,
            expected_lease_until=claim["lease_until"],
        )
        logger.exception(
            "DigiSac contact hydration failed: contact_id=%s category=unexpected",
            external_id,
        )
    else:
        await mark_digisac_contact_hydration_success(
            external_id,
            expected_lease_until=claim["lease_until"],
        )
    return True


async def contact_hydration_loop() -> None:
    """Recover due contact hydration work outside the webhook request path."""
    while True:
        try:
            processed = await process_one_contact_hydration()
        except Exception:
            logger.exception("DigiSac contact hydration loop iteration failed")
            processed = False
        if not processed:
            await asyncio.sleep(settings.digisac_contact_hydration_interval_seconds)
