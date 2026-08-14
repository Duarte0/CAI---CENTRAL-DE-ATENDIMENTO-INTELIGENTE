from __future__ import annotations

import asyncio
from typing import Any

import psycopg
import pytest
import requests
from fastapi import Response

from src.api import routes
from src.core.digisac_client import (
    DigisacClient,
    DigisacClientError,
    DigisacContact,
    normalize_contact,
)
from src.core.config import settings
from src.core.digisac_contact_hydration import process_one_contact_hydration
from src.core.db import (
    claim_digisac_contact_hydration,
    get_digisac_contact,
    get_digisac_contact_hydration,
    request_digisac_contact_hydration,
    upsert_digisac_contact,
)


class FakeResponse:
    def __init__(
        self,
        payload: Any,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> Any:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def contact_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "contact-1",
        "name": "synthetic-name",
        "alternativeName": "synthetic-alternative",
        "internalName": "synthetic-internal",
        "isGroup": False,
        "accountId": "account-1",
        "serviceId": "service-1",
        "createdAt": "2026-08-14T10:00:00Z",
        "updatedAt": "2026-08-14T11:00:00Z",
        "deletedAt": None,
        "data": {
            "number": " +٤٤ (１１) 9 ",
            "jidId": "forbidden-jid",
            "lidId": "forbidden-lid",
        },
        "idFromService": "forbidden-service-id",
    }
    payload.update(overrides)
    return payload


def test_contact_normalization_preserves_raw_and_ascii_digits_without_identity_aliases():
    contact = normalize_contact(contact_payload())

    assert contact.external_id == "contact-1"
    assert contact.raw_number == " +٤٤ (１１) 9 "
    assert contact.normalized_number == "٤٤".translate(
        str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    ) + "119"
    assert contact.is_group is False
    assert contact.account_id == "account-1"
    assert contact.service_id == "service-1"
    assert not hasattr(contact, "id_from_service")
    assert not hasattr(contact, "jid_id")
    assert not hasattr(contact, "lid_id")


@pytest.mark.parametrize("number", [None, "", "  ", "abc---"])
def test_contact_normalization_does_not_create_empty_number(number: Any):
    contact = normalize_contact(contact_payload(data={"number": number}))
    if number in (None, "", "  "):
        assert contact.raw_number is None
    else:
        assert contact.raw_number == "abc---"
    assert contact.normalized_number is None


def test_contact_client_fetches_only_the_individual_configured_endpoint():
    session = FakeSession([FakeResponse({"data": contact_payload()})])
    client = DigisacClient(
        base_url="https://example.test/api/v1",
        api_key="secret",
        timeout_seconds=1,
        max_attempts=1,
        session=session,
    )

    result = client.get_contact("contact-1")

    assert result.external_id == "contact-1"
    assert session.calls[0][0] == "https://example.test/api/v1/contacts/contact-1"
    assert session.calls[0][1]["headers"] == {"Authorization": "Bearer secret"}


def test_contact_client_retries_rate_limit_and_rejects_invalid_shape(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("src.core.digisac_client.time.sleep", sleeps.append)
    session = FakeSession(
        [
            FakeResponse({}, status_code=429, headers={"Retry-After": "0"}),
            FakeResponse({"data": {"name": "missing-id"}}),
        ]
    )
    client = DigisacClient(
        base_url="https://example.test/api/v1",
        api_key="secret",
        timeout_seconds=1,
        max_attempts=2,
        retry_base_seconds=0.001,
        session=session,
    )

    with pytest.raises(DigisacClientError, match="contact response"):
        client.get_contact("contact-1")
    assert sleeps == [0.0]


@pytest.mark.asyncio
async def test_message_contact_reference_schedules_hydration_without_inline_provider_call(
    monkeypatch: pytest.MonkeyPatch,
):
    scheduled: list[str] = []

    async def schedule(contact_id: str, **_kwargs: Any) -> bool:
        scheduled.append(contact_id)
        return True

    monkeypatch.setattr(routes, "request_contact_hydration", schedule)
    monkeypatch.setattr(routes, "reserve_transcription", lambda *_args: None)
    redis = type(
        "Redis",
        (),
        {
            "set": lambda _self, *_args, **_kwargs: asyncio.sleep(0, result=True),
            "get": lambda _self, *_args: asyncio.sleep(0, result=None),
            "rpush": lambda _self, *_args: asyncio.sleep(0, result=1),
        },
    )()
    payload = {
        "event": "message.created",
        "data": {
            "id": "message-contact",
            "ticketId": "ticket-contact",
            "type": "chat",
            "text": "Olá",
            "contactId": "contact-1",
            "isFromMe": False,
            "isFromBot": False,
        },
    }
    async def parse(_request: Any):
        return payload, None

    monkeypatch.setattr(routes, "parse_webhook_payload", parse)

    result = await routes.digisac_webhook(
        request=None, response=Response(), redis=redis
    )

    assert result["status"] == "received"
    assert scheduled == ["contact-1"]


@pytest.mark.asyncio
async def test_ticket_contact_snapshot_is_converted_and_persisted_additively(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: list[tuple[Any, str, str | None]] = []

    async def persist(contact: Any, *, source: str, observed_at: str | None):
        captured.append((contact, source, observed_at))
        return {}

    monkeypatch.setattr(routes, "upsert_digisac_contact", persist)
    assert await routes.capture_contact_snapshot(
        {"event": "ticket.updated"},
        {"contact": contact_payload(), "updatedAt": "2026-08-14T13:00:00Z"},
    )
    assert captured[0][0].external_id == "contact-1"
    assert captured[0][1:] == ("ticket_webhook", "2026-08-14T13:00:00+00:00")


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_contact_schema_rejects_blank_identity_and_non_numeric_normalization():
    with psycopg.connect(settings.database_url) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                INSERT INTO digisac_contacts (
                    external_id, last_seen_at, last_source
                ) VALUES ('   ', CURRENT_TIMESTAMP, 'test')
                """
            )
    with psycopg.connect(settings.database_url) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                INSERT INTO digisac_contacts (
                    external_id, normalized_number, last_seen_at, last_source
                ) VALUES ('contact-invalid', 'not-digits', CURRENT_TIMESTAMP, 'test')
                """
            )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_contact_upsert_is_idempotent_and_preserves_newer_observation():
    newer = normalize_contact(
        contact_payload(name="newer-synthetic", updatedAt="2026-08-14T12:00:00Z")
    )
    older = normalize_contact(
        contact_payload(name="older-synthetic", updatedAt="2026-08-14T11:00:00Z")
    )

    await asyncio.gather(
        *[upsert_digisac_contact(newer, source="ticket_webhook") for _ in range(5)]
    )
    await upsert_digisac_contact(older, source="contact_hydration")

    row = await get_digisac_contact("contact-1")
    assert row is not None
    assert row["name"] == "newer-synthetic"
    assert row["normalized_number"] == newer.normalized_number
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM digisac_contacts WHERE external_id = %s",
            ("contact-1",),
        ).fetchone() == (1,)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_contact_hydration_is_deduplicated_claimed_and_recoverable():
    assert await request_digisac_contact_hydration("contact-claim") is True
    claims = await asyncio.gather(
        *[claim_digisac_contact_hydration(lease_seconds=60) for _ in range(2)]
    )
    assert sum(claim is not None for claim in claims) == 1

    assert await request_digisac_contact_hydration("contact-2") is True
    assert await request_digisac_contact_hydration("contact-2") is False

    class HydratingClient:
        def get_contact(self, contact_id: str):
            assert contact_id == "contact-2"
            return normalize_contact(
                contact_payload(id=contact_id, name="hydrated-synthetic")
            )

    assert await process_one_contact_hydration(client=HydratingClient()) is True
    assert await claim_digisac_contact_hydration(lease_seconds=60) is None
    assert (await get_digisac_contact("contact-2"))["name"] == "hydrated-synthetic"
    state = await get_digisac_contact_hydration("contact-2")
    assert state is not None
    assert state["status"] == "succeeded"


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_hydration_failure_preserves_last_valid_contact_data():
    snapshot = DigisacContact(external_id="contact-3", name="Known")
    await upsert_digisac_contact(snapshot, source="ticket_webhook")
    assert await request_digisac_contact_hydration("contact-3") is True

    class FailingClient:
        def get_contact(self, _contact_id: str):
            raise DigisacClientError("rate limit exhausted", category="transient")

    assert await process_one_contact_hydration(client=FailingClient()) is True
    row = await get_digisac_contact("contact-3")
    assert row is not None
    assert row["name"] == "Known"
    state = await get_digisac_contact_hydration("contact-3")
    assert state is not None
    assert state["status"] == "failed"
    assert state["failure_category"] == "transient"
