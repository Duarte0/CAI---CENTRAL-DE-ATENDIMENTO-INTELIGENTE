from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest
import psycopg
import requests

from src.core.digisac_client import (
    DigisacClient,
    DigisacClientError,
    DigisacContact,
    DigisacContactPage,
    DigisacResponseError,
    normalize_contact,
)
from src.core.config import settings
from src.core.digisac_contact_backfill import (
    DigisacContactBackfillError,
    acquire_contact_backfill,
    run_contact_backfill,
)
from src.core.db import (
    get_digisac_contact,
    publish_digisac_contact_backfill,
)


def contact_payload(
    *,
    contact_id: str = "contact-1",
    name: str = "Synthetic Contact",
    updated_at: str = "2026-08-14T12:00:00Z",
) -> dict[str, Any]:
    return {
        "id": contact_id,
        "name": name,
        "number": "synthetic-number",
        "isGroup": False,
        "updatedAt": updated_at,
    }


class FakeResponse:
    def __init__(
        self,
        payload: Mapping[str, Any],
        *,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = dict(headers or {})

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self) -> Mapping[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def page_payload(
    *,
    data: list[dict[str, Any]],
    total: int,
    limit: int,
    current_page: int,
    last_page: int,
) -> dict[str, Any]:
    return {
        "data": data,
        "total": total,
        "limit": limit,
        "currentPage": current_page,
        "lastPage": last_page,
    }


def make_client(session: FakeSession, *, max_attempts: int = 1) -> DigisacClient:
    return DigisacClient(
        base_url="https://example.test/api/v1",
        api_key="synthetic-key",
        timeout_seconds=1,
        max_attempts=max_attempts,
        retry_base_seconds=0.001,
        session=session,
    )


def test_contacts_page_uses_typed_endpoint_and_validates_one_page_result() -> None:
    session = FakeSession(
        [
            FakeResponse(
                page_payload(
                    data=[contact_payload()],
                    total=1,
                    limit=5000,
                    current_page=1,
                    last_page=1,
                )
            )
        ]
    )
    client = make_client(session)

    page = client.get_contacts_page(page=1, per_page=5000)

    assert isinstance(page, DigisacContactPage)
    assert page.contacts[0].external_id == "contact-1"
    assert session.calls[0]["url"] == "https://example.test/api/v1/contacts"
    assert session.calls[0]["params"] == {"perPage": 5000, "page": 1}
    assert "Authorization" not in str(page)


def test_contact_backfill_fetches_pages_and_deduplicates_by_opaque_id() -> None:
    session = FakeSession(
        [
            FakeResponse(
                page_payload(
                    data=[contact_payload(contact_id="contact-1")],
                    total=3,
                    limit=2,
                    current_page=1,
                    last_page=2,
                )
            ),
            FakeResponse(
                page_payload(
                    data=[
                        contact_payload(
                            contact_id="contact-1",
                            name="Duplicate",
                            updated_at="2026-08-14T13:00:00Z",
                        ),
                        contact_payload(contact_id="contact-2", name="Second"),
                    ],
                    total=3,
                    limit=2,
                    current_page=2,
                    last_page=2,
                )
            ),
        ]
    )

    snapshot = asyncio.run(
        acquire_contact_backfill(client=make_client(session), per_page=2)
    )

    assert [contact.external_id for contact in snapshot.contacts] == [
        "contact-1",
        "contact-2",
    ]
    assert snapshot.contacts[0].name == "Duplicate"
    assert snapshot.page_count == 2
    assert snapshot.duplicate_count == 1
    assert [call["params"] for call in session.calls] == [
        {"perPage": 2, "page": 1},
        {"perPage": 2, "page": 2},
    ]


@pytest.mark.parametrize(
    "payload",
    [
        page_payload(
            data=[contact_payload()],
            total=1,
            limit=1,
            current_page=2,
            last_page=1,
        ),
        page_payload(
            data=[], total=2, limit=1, current_page=1, last_page=2
        ),
        page_payload(
            data=[contact_payload()],
            total=1,
            limit=2,
            current_page=1,
            last_page=2,
        ),
    ],
)
def test_contacts_page_rejects_invalid_pagination(payload: dict[str, Any]) -> None:
    client = make_client(FakeSession([FakeResponse(payload)]))

    with pytest.raises(DigisacResponseError):
        client.get_contacts_page(page=1, per_page=2)


def test_contacts_page_rejects_missing_contact_identity() -> None:
    payload = page_payload(
        data=[{"name": "missing identity"}],
        total=1,
        limit=2,
        current_page=1,
        last_page=1,
    )
    client = make_client(FakeSession([FakeResponse(payload)]))

    with pytest.raises(DigisacResponseError, match="contact response"):
        client.get_contacts_page(page=1, per_page=2)


def test_contacts_page_retries_rate_limit_without_exposing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("src.core.digisac_client.time.sleep", sleeps.append)
    session = FakeSession(
        [
            FakeResponse({}, status_code=429, headers={"Retry-After": "0"}),
            FakeResponse(
                page_payload(
                    data=[contact_payload()],
                    total=1,
                    limit=5000,
                    current_page=1,
                    last_page=1,
                )
            ),
        ]
    )

    page = make_client(session, max_attempts=2).get_contacts_page(
        page=1, per_page=5000
    )

    assert page.total == 1
    assert sleeps == [0.0]
    assert "synthetic-key" not in repr(page)


@pytest.mark.asyncio
async def test_failed_acquisition_does_not_attempt_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingClient:
        def get_contacts_page(self, *, page: int, per_page: int) -> DigisacContactPage:
            raise DigisacClientError("provider unavailable", category="transient")

    published = False

    async def publish(_contacts: tuple[DigisacContact, ...]):
        nonlocal published
        published = True
        return {"published_count": 0}

    monkeypatch.setattr(
        "src.core.digisac_contact_backfill.publish_digisac_contact_backfill",
        publish,
    )

    with pytest.raises(DigisacClientError):
        await run_contact_backfill(client=FailingClient())
    assert published is False


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_backfill_publication_is_idempotent_concurrent_and_preserves_absence():
    newer = normalize_contact(
        {**contact_payload(name="newer"), "updatedAt": "2026-08-14T12:00:00Z"}
    )
    older = normalize_contact(
        {**contact_payload(name="older"), "updatedAt": "2026-08-14T11:00:00Z"}
    )
    absent = normalize_contact(contact_payload(contact_id="contact-absent"))

    await asyncio.gather(
        *[
            publish_digisac_contact_backfill((newer, newer))
            for _ in range(3)
        ]
    )
    await publish_digisac_contact_backfill((older, absent))

    row = await get_digisac_contact("contact-1")
    assert row is not None
    assert row["name"] == "newer"
    assert await get_digisac_contact("contact-absent") is not None
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM digisac_contacts WHERE external_id = %s",
            ("contact-1",),
        ).fetchone() == (1,)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_backfill_publication_rolls_back_all_rows_on_commit_failure():
    valid = normalize_contact(contact_payload(contact_id="rollback-valid"))
    invalid = DigisacContact(external_id=" ")

    with pytest.raises(psycopg.errors.CheckViolation):
        await publish_digisac_contact_backfill((valid, invalid))

    assert await get_digisac_contact("rollback-valid") is None


def test_normalizer_keeps_contact_identity_opaque() -> None:
    contact = normalize_contact(
        {
            **contact_payload(contact_id="opaque-id"),
            "idFromService": "phone-derived-id",
            "data": {"jidId": "jid-derived-id"},
        }
    )

    assert contact.external_id == "opaque-id"
