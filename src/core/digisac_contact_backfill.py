"""Acquire and publish a complete DigiSac Contacts snapshot."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Protocol

from src.core.config import settings
from src.core.db import publish_digisac_contact_backfill
from src.core.digisac_client import (
    DigisacClient,
    DigisacClientError,
    DigisacContact,
    DigisacContactPage,
)


class DigisacContactBackfillError(RuntimeError):
    """Sanitized validation or execution failure for a Contacts backfill."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.safe_message = message


@dataclass(frozen=True)
class DigisacContactBackfillSnapshot:
    """A complete, validated, globally deduplicated Contacts snapshot."""

    contacts: tuple[DigisacContact, ...]
    page_count: int
    duplicate_count: int
    total: int


class ContactBackfillClient(Protocol):
    def get_contacts_page(self, *, page: int, per_page: int) -> DigisacContactPage:
        """Fetch one validated page from the Contacts provider boundary."""
        ...


def _merge_duplicate_contacts(
    existing: DigisacContact, incoming: DigisacContact
) -> DigisacContact:
    """Retain the newest duplicate while filling absent metadata conservatively."""
    if (
        existing.provider_updated_at is not None
        and incoming.provider_updated_at is not None
    ):
        preferred, fallback = (
            (incoming, existing)
            if incoming.provider_updated_at > existing.provider_updated_at
            else (existing, incoming)
        )
    elif incoming.provider_updated_at is not None:
        preferred, fallback = incoming, existing
    else:
        preferred, fallback = existing, incoming
    fields = {
        field: (
            getattr(preferred, field)
            if getattr(preferred, field) is not None
            else getattr(fallback, field)
        )
        for field in (
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
    }
    return replace(preferred, **fields)


def _validate_page(
    page: DigisacContactPage, *, requested_page: int, expected_total: int | None
) -> None:
    if not isinstance(page, DigisacContactPage):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise DigisacContactBackfillError(
            "invalid_response", "DigiSac contacts page has an invalid type"
        )
    if page.current_page != requested_page:
        raise DigisacContactBackfillError(
            "invalid_response", "DigiSac contacts page did not advance"
        )
    if page.last_page < page.current_page:
        raise DigisacContactBackfillError(
            "invalid_response", "DigiSac contacts page metadata is inconsistent"
        )
    if page.total < 0 or page.limit <= 0 or page.last_page <= 0:
        raise DigisacContactBackfillError(
            "invalid_response", "DigiSac contacts page metadata is invalid"
        )
    if (
        page.last_page != max(1, (page.total + page.limit - 1) // page.limit)
        or len(page.contacts) > page.limit
        or (page.total == 0 and page.contacts)
    ):
        raise DigisacContactBackfillError(
            "invalid_response", "DigiSac contacts page metadata is inconsistent"
        )
    if expected_total is not None and page.total != expected_total:
        raise DigisacContactBackfillError(
            "invalid_response", "DigiSac contacts total changed during pagination"
        )
    if page.total > 0 and not page.contacts:
        raise DigisacContactBackfillError(
            "invalid_response", "DigiSac returned an empty page before completion"
        )
    if page.current_page < page.last_page and not page.contacts:
        raise DigisacContactBackfillError(
            "invalid_response", "DigiSac returned an empty intermediate page"
        )


async def acquire_contact_backfill(
    *,
    client: ContactBackfillClient | None = None,
    per_page: int | None = None,
) -> DigisacContactBackfillSnapshot:
    """Acquire every validated page before any contact is persisted."""
    page_size = (
        settings.digisac_contact_backfill_per_page
        if per_page is None
        else per_page
    )
    if page_size <= 0:
        raise ValueError("Contacts backfill page size must be positive")
    active_client = client or DigisacClient()
    requested_page = 1
    expected_total: int | None = None
    expected_last_page: int | None = None
    pages: list[DigisacContactPage] = []
    contacts_by_id: dict[str, DigisacContact] = {}
    duplicate_count = 0

    while True:
        try:
            response = await asyncio.to_thread(
                active_client.get_contacts_page,
                page=requested_page,
                per_page=page_size,
            )
        except DigisacClientError:
            raise
        except Exception as exc:
            raise DigisacContactBackfillError(
                "provider", "DigiSac contacts backfill request failed"
            ) from exc
        _validate_page(
            response,
            requested_page=requested_page,
            expected_total=expected_total,
        )
        if expected_total is None:
            expected_total = response.total
            expected_last_page = response.last_page
        elif response.last_page != expected_last_page:
            raise DigisacContactBackfillError(
                "invalid_response",
                "DigiSac contacts lastPage changed during pagination",
            )
        pages.append(response)
        for contact in response.contacts:
            if contact.external_id in contacts_by_id:
                duplicate_count += 1
                contacts_by_id[contact.external_id] = _merge_duplicate_contacts(
                    contacts_by_id[contact.external_id], contact
                )
                continue
            contacts_by_id[contact.external_id] = contact
        if response.current_page == response.last_page:
            break
        requested_page += 1
        if requested_page > response.last_page:
            raise DigisacContactBackfillError(
                "invalid_response", "DigiSac contacts pagination did not terminate"
            )

    return DigisacContactBackfillSnapshot(
        contacts=tuple(contacts_by_id.values()),
        page_count=len(pages),
        duplicate_count=duplicate_count,
        total=expected_total or 0,
    )


@dataclass(frozen=True)
class DigisacContactBackfillResult:
    page_count: int
    acquired_count: int
    duplicate_count: int
    published_count: int


async def run_contact_backfill(
    *,
    client: ContactBackfillClient | None = None,
    per_page: int | None = None,
) -> DigisacContactBackfillResult:
    """Acquire, validate, and publish one complete Contacts snapshot."""
    snapshot = await acquire_contact_backfill(client=client, per_page=per_page)
    try:
        published = await publish_digisac_contact_backfill(snapshot.contacts)
    except Exception as exc:
        raise DigisacContactBackfillError(
            "persistence", "DigiSac contacts backfill publication failed"
        ) from exc
    return DigisacContactBackfillResult(
        page_count=snapshot.page_count,
        acquired_count=len(snapshot.contacts),
        duplicate_count=snapshot.duplicate_count,
        published_count=int(published["published_count"]),
    )
