"""Tests for the authenticated, read-only identity triage surface."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from main import app
from src.api import admin_routes
from src.core.config import Settings, require_admin_api_token, settings
from src.core.db import upsert_digisac_contact
from src.core.digisac_client import DigisacContact
from src.core.identity_admin import (
    get_identity_contact_projection,
    list_active_company_projection,
    list_identity_link_projection,
)


ADMIN_TOKEN = "test-only-admin-token"


def _empty_identity_page(**_: Any) -> dict[str, Any]:
    return {"items": [], "next_after": None}


async def _empty_identity_page_async(**_: Any) -> dict[str, Any]:
    return _empty_identity_page()


async def _empty_contact(_: str) -> dict[str, Any] | None:
    return None


async def _empty_companies(**_: Any) -> dict[str, Any]:
    return _empty_identity_page()


@pytest.fixture
def admin_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)
    monkeypatch.setattr(admin_routes, "list_identity_link_projection", _empty_identity_page_async)
    monkeypatch.setattr(admin_routes, "get_identity_contact_projection", _empty_contact)
    monkeypatch.setattr(admin_routes, "list_active_company_projection", _empty_companies)
    return TestClient(app)


def test_settings_require_admin_token_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    missing = Settings(_env_file=None, admin_api_token=None)
    monkeypatch.setattr(settings, "admin_api_token", missing.admin_api_token)
    with pytest.raises(RuntimeError, match="ADMIN_API_TOKEN"):
        require_admin_api_token()

    with pytest.raises(ValueError, match="must not be empty"):
        Settings(_env_file=None, admin_api_token=" ")


def test_missing_configuration_stops_application_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_api_token", None)
    with pytest.raises(RuntimeError, match="ADMIN_API_TOKEN"):
        with TestClient(app):
            pass


@pytest.mark.parametrize(
    "headers",
    [None, {"Authorization": "Basic not-a-bearer"}, {"Authorization": "Bearer wrong"}],
)
def test_all_admin_routes_use_one_generic_unauthorized_response(
    admin_client: TestClient,
    headers: dict[str, str] | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="src.api.admin_routes")
    paths = [
        "/admin/acessorias/identity-links",
        "/admin/acessorias/contacts/missing-contact/identity",
        "/admin/acessorias/companies",
    ]
    responses = [admin_client.get(path, headers=headers or {}) for path in paths]
    assert [(response.status_code, response.json()) for response in responses] == [
        (401, {"detail": "Invalid administrative credentials"}),
        (401, {"detail": "Invalid administrative credentials"}),
        (401, {"detail": "Invalid administrative credentials"}),
    ]
    assert ADMIN_TOKEN not in caplog.text


def test_valid_admin_read_routes_and_safe_missing_contact(admin_client: TestClient) -> None:
    assert admin_client.get(
        "/admin/acessorias/identity-links", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
    ).json() == {"items": [], "next_cursor": None}
    assert admin_client.get(
        "/admin/acessorias/companies", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
    ).json() == {"items": [], "next_cursor": None}
    missing = admin_client.get(
        "/admin/acessorias/contacts/missing-contact/identity",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert missing.status_code == 404
    assert missing.json() == {"detail": "DigiSac contact not found"}


def test_cursor_is_signed_scope_bound_and_limit_errors_are_400(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int] | None] = []

    async def fake_list(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["after"])
        if kwargs["after"] is None:
            return {"items": [], "next_after": ("contact-1", 1)}
        return {"items": [], "next_after": None}

    monkeypatch.setattr(admin_routes, "list_identity_link_projection", fake_list)
    headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    first = admin_client.get(
        "/admin/acessorias/identity-links?state=unresolved&limit=1", headers=headers
    )
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    assert isinstance(cursor, str)

    second = admin_client.get(
        f"/admin/acessorias/identity-links?state=unresolved&limit=1&cursor={cursor}",
        headers=headers,
    )
    assert second.status_code == 200
    assert calls == [None, ("contact-1", 1)]

    tampered = cursor[:-1] + ("0" if cursor[-1] != "0" else "1")
    assert admin_client.get(
        f"/admin/acessorias/identity-links?cursor={tampered}", headers=headers
    ).status_code == 400
    assert admin_client.get(
        f"/admin/acessorias/companies?cursor={cursor}", headers=headers
    ).status_code == 400
    assert admin_client.get(
        "/admin/acessorias/identity-links?limit=0", headers=headers
    ).status_code == 400
    assert admin_client.get(
        "/admin/acessorias/identity-links?limit=not-a-number", headers=headers
    ).status_code == 400
    assert admin_client.get(
        "/admin/acessorias/identity-links?state=made-up", headers=headers
    ).status_code == 400


def _create_company(
    external_id: str,
    *,
    active: bool = True,
    present: bool = True,
    trade_name: str = "",
) -> int:
    with psycopg.connect(settings.database_url) as connection:
        row = connection.execute(
            """
            INSERT INTO acessorias_companies (
                external_id, provider_id, legal_name, trade_name,
                is_present, is_active, synced_at
            ) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            RETURNING id
            """,
            (external_id, f"provider-{external_id}", f"Legal {external_id}", trade_name, present, active),
        ).fetchone()
    assert row is not None
    return int(row[0])


async def _create_contact(
    external_id: str,
    *,
    is_group: bool | None = False,
) -> int:
    row = await upsert_digisac_contact(
        DigisacContact(
            external_id=external_id,
            name="Operator-visible name",
            raw_number="5511998765432",
            normalized_number="5511998765432",
            raw_email="private@example.test",
            normalized_email="private@example.test",
            is_group=is_group,
        ),
        source="identity_admin_test",
    )
    return int(row["id"])


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_read_projections_are_safe_and_use_existing_directory_state() -> None:
    active_company = _create_company("admin-company-active", trade_name="Visible Active")
    _create_company("admin-company-inactive", active=False)
    candidate_contact = await _create_contact("admin-contact-candidate")
    group_contact = await _create_contact("admin-contact-group", is_group=True)

    with psycopg.connect(settings.database_url) as connection:
        company_contact = connection.execute(
            """
            INSERT INTO acessorias_company_contacts (
                company_id, external_key, name, normalized_mobile,
                normalized_email, synced_at
            ) VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            RETURNING id
            """,
            (active_company, "admin-directory-contact", "Directory name", "5511998765432", "private@example.test"),
        ).fetchone()
        assert company_contact is not None
        link = connection.execute(
            """
            INSERT INTO identity_company_links (
                digisac_contact_id, acessorias_company_id, state, source
            ) VALUES (%s, %s, 'candidate', 'automatic')
            RETURNING id
            """,
            (candidate_contact, active_company),
        ).fetchone()
        assert link is not None
        connection.execute(
            """
            INSERT INTO identity_company_link_transitions (
                link_id, from_state, to_state, source, reason, transition_key
            ) VALUES (%s, NULL, 'candidate', 'automatic', 'discovery', %s)
            """,
            (link[0], "admin-read-transition"),
        )
        connection.execute(
            """
            INSERT INTO identity_match_evidence (
                digisac_contact_id, acessorias_company_contact_id,
                acessorias_company_id, evidence_type, value_fingerprint,
                source, rule_version, observed_at
            ) VALUES (%s, %s, %s, 'exact_phone', %s, 'automatic', 'spec0009-v1.1', CURRENT_TIMESTAMP)
            """,
            (candidate_contact, company_contact[0], active_company, "a" * 64),
        )

    candidate_page = await list_identity_link_projection(
        state="candidate", after=None, limit=100
    )
    assert [item["digisac_contact_external_id"] for item in candidate_page["items"]] == [
        "admin-contact-candidate"
    ]
    serialized = json.dumps(candidate_page, ensure_ascii=False)
    assert "5511998765432" not in serialized
    assert "private@example.test" not in serialized
    assert "exact_phone" in serialized
    assert "Visible Active" in serialized
    candidate_detail = await get_identity_contact_projection("admin-contact-candidate")
    assert candidate_detail is not None
    assert candidate_detail["state"] == "candidate"
    assert candidate_detail["transitions"][0]["reason"] == "discovery"

    unresolved_page = await list_identity_link_projection(
        state="unresolved", after=None, limit=100
    )
    assert "admin-contact-group" in {
        item["digisac_contact_external_id"] for item in unresolved_page["items"]
    }
    detail = await get_identity_contact_projection("admin-contact-group")
    assert detail is not None
    assert detail["state"] == "unresolved"
    assert detail["candidate_company_count"] == 0
    assert detail["links"] == []

    companies = await list_active_company_projection(query="Visible", after=None, limit=100)
    assert [item["acessorias_company_external_id"] for item in companies["items"]] == [
        "admin-company-active"
    ]
    assert await get_identity_contact_projection("does-not-exist") is None
    concurrent_pages = await asyncio.gather(
        *[
            list_identity_link_projection(state=None, after=None, limit=100)
            for _ in range(3)
        ]
    )
    assert concurrent_pages[0] == concurrent_pages[1] == concurrent_pages[2]
