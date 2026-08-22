"""Focused tests for the authenticated administrative UI shell."""

from __future__ import annotations

import asyncio
from http.cookies import SimpleCookie
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from main import app
from src.api import admin_ui
from src.core.config import Settings, settings


UI_PASSWORD = "test-only-ui-password"
UI_SECRET = "test-only-ui-session-secret"


@pytest.fixture
def ui_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "admin_api_token", "test-only-admin-token")
    monkeypatch.setattr(settings, "admin_ui_password", UI_PASSWORD)
    monkeypatch.setattr(settings, "admin_session_secret", UI_SECRET)
    monkeypatch.setattr(settings, "environment", "development")
    return TestClient(app)


def _session_cookie(response) -> SimpleCookie[str]:
    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    return cookies


def _login(ui_client: TestClient) -> None:
    response = ui_client.post(
        "/admin/acessorias/login",
        data={"password": UI_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_ui_settings_reject_blank_values() -> None:
    with pytest.raises(ValueError, match="ADMIN_UI_PASSWORD must not be empty"):
        Settings(_env_file=None, admin_ui_password=" ")
    with pytest.raises(ValueError, match="ADMIN_SESSION_SECRET must not be empty"):
        Settings(_env_file=None, admin_session_secret=" ")


def test_ui_login_is_generic_when_configuration_is_unavailable(
    ui_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_ui_password", None)
    monkeypatch.setattr(settings, "admin_session_secret", None)

    unavailable = ui_client.post(
        "/admin/acessorias/login", data={"password": UI_PASSWORD}
    )

    monkeypatch.setattr(settings, "admin_ui_password", "different-password")
    monkeypatch.setattr(settings, "admin_session_secret", UI_SECRET)
    invalid = ui_client.post("/admin/acessorias/login", data={"password": UI_PASSWORD})

    assert unavailable.status_code == invalid.status_code == 401
    assert unavailable.text == invalid.text
    assert "ADMIN_UI_PASSWORD" not in unavailable.text
    assert "ADMIN_SESSION_SECRET" not in unavailable.text
    assert unavailable.headers["cache-control"] == "no-store"


def test_login_protects_shell_with_fixed_signed_cookie(ui_client: TestClient) -> None:
    before = ui_client.get("/admin/acessorias/ui")
    assert before.status_code == 401
    assert before.headers["cache-control"] == "no-store"
    assert 'action="/admin/acessorias/login"' in before.text

    login = ui_client.post(
        "/admin/acessorias/login",
        data={"password": UI_PASSWORD},
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert login.headers["location"] == "/admin/acessorias/ui"
    assert login.headers["cache-control"] == "no-store"

    cookie = _session_cookie(login)["cai_admin_session"]
    assert cookie["httponly"]
    assert cookie["samesite"].lower() == "strict"
    assert cookie["secure"] == ""
    assert cookie["max-age"] == "3600"
    assert UI_SECRET not in cookie.value
    assert UI_PASSWORD not in cookie.value

    shell = ui_client.get("/admin/acessorias/ui")
    assert shell.status_code == 200
    assert shell.headers["cache-control"] == "no-store"
    assert "set-cookie" not in shell.headers
    assert "Identity review" in shell.text
    assert "ADMIN_API_TOKEN" not in shell.text
    assert "https://" not in shell.text
    assert "http://" not in shell.text


def test_ui_read_workspace_has_local_accessible_contract(
    ui_client: TestClient,
) -> None:
    _login(ui_client)

    shell = ui_client.get("/admin/acessorias/ui")

    assert shell.status_code == 200
    assert 'id="identity-queue"' in shell.text
    assert 'value="candidate"' in shell.text
    assert 'value="ambiguous"' in shell.text
    assert 'value="unresolved"' in shell.text
    assert 'id="company-search"' in shell.text
    assert 'aria-live="polite"' in shell.text
    assert "/admin/acessorias/ui/api/identity-links" in shell.text
    assert "/admin/acessorias/ui/api/companies" in shell.text
    assert "localStorage" not in shell.text
    assert "sessionStorage" not in shell.text
    assert "ADMIN_API_TOKEN" not in shell.text
    assert "https://" not in shell.text


def test_ui_action_bridge_uses_fixed_reasons_and_durable_replay(
    ui_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    link_result: dict[str, Any] = {
        "digisac_contact_external_id": "contact-1",
        "acessorias_company_external_id": "company-1",
        "state": "confirmed",
        "source": "admin_api",
        "confirmation_source": "admin_api",
        "confirmed_at": "2026-08-21T12:00:00Z",
        "rejection_reason": None,
        "created_at": "2026-08-21T11:00:00Z",
        "updated_at": "2026-08-21T12:00:00Z",
    }
    discovery_result: dict[str, Any] = {
        "digisac_contact_external_id": "contact-1",
        "state": "candidate",
        "matched_company_external_ids": ["company-1"],
        "links": [],
        "matched_company_count": 1,
        "evidence_count": 1,
        "observed_at": "2026-08-21T12:00:00Z",
    }

    async def fake_confirm(
        contact: str,
        company: str,
        *,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        calls.append(("confirm", contact, company, reason, idempotency_key))
        return {
            "replayed": len([call for call in calls if call[0] == "confirm"]) > 1,
            "result": link_result,
        }

    async def fake_reject(
        contact: str,
        company: str,
        *,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        calls.append(("reject", contact, company, reason, idempotency_key))
        return {
            "replayed": False,
            "result": {
                **link_result,
                "state": "rejected",
                "confirmation_source": None,
                "confirmed_at": None,
                "rejection_reason": "operator_rejected",
            },
        }

    async def fake_discovery(
        contact: str,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        calls.append(("discovery", contact, idempotency_key))
        return {"replayed": False, "result": discovery_result}

    monkeypatch.setattr(admin_ui, "confirm_identity_link_admin", fake_confirm)
    monkeypatch.setattr(admin_ui, "reject_identity_link_admin", fake_reject)
    monkeypatch.setattr(admin_ui, "discover_identity_admin", fake_discovery)

    unauthenticated = TestClient(app).post(
        "/admin/acessorias/ui/api/contacts/contact-1/identity-links/confirm",
        json={
            "acessorias_company_external_id": "company-1",
            "idempotency_key": "key-1",
        },
    )
    assert unauthenticated.status_code == 401

    _login(ui_client)
    confirm_path = "/admin/acessorias/ui/api/contacts/contact-1/identity-links/confirm"
    body = {"acessorias_company_external_id": "company-1", "idempotency_key": "key-1"}
    created = ui_client.post(confirm_path, json=body)
    replay = ui_client.post(confirm_path, json=body)
    rejected = ui_client.post(
        "/admin/acessorias/ui/api/contacts/contact-1/identity-links/company-1/reject",
        json={"idempotency_key": "key-2"},
    )
    discovered = ui_client.post(
        "/admin/acessorias/ui/api/contacts/contact-1/identity-discovery",
        json={"idempotency_key": "key-3"},
    )

    assert created.status_code == 201
    assert replay.status_code == 200
    assert rejected.status_code == 201
    assert discovered.status_code == 200
    assert all(
        response.headers["cache-control"] == "no-store"
        for response in (created, replay, rejected, discovered)
    )
    assert calls == [
        ("confirm", "contact-1", "company-1", "operator_verified", "key-1"),
        ("confirm", "contact-1", "company-1", "operator_verified", "key-1"),
        ("reject", "contact-1", "company-1", "operator_rejected", "key-2"),
        ("discovery", "contact-1", "key-3"),
    ]
    assert "key-1" not in created.text
    assert "operator_verified" not in created.text


def test_ui_action_bridge_returns_safe_command_errors(
    ui_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core.identity_resolution import IdentityConflictError

    async def fail_confirm(*_: object, **__: object) -> dict[str, Any]:
        raise IdentityConflictError()

    monkeypatch.setattr(admin_ui, "confirm_identity_link_admin", fail_confirm)
    _login(ui_client)

    response = ui_client.post(
        "/admin/acessorias/ui/api/contacts/contact-1/identity-links/confirm",
        json={
            "acessorias_company_external_id": "company-1",
            "idempotency_key": "key-1",
        },
    )

    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": "Identity confirmation conflict"}
    assert "company-1" not in response.text


def test_ui_action_bridge_maps_unavailable_company_without_echoing_target(
    ui_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core.identity_resolution import IdentityResolutionError

    async def fail_confirm(*_: object, **__: object) -> dict[str, Any]:
        raise IdentityResolutionError(
            "directory_company_unavailable", "unsafe provider detail"
        )

    monkeypatch.setattr(admin_ui, "confirm_identity_link_admin", fail_confirm)
    _login(ui_client)

    response = ui_client.post(
        "/admin/acessorias/ui/api/contacts/contact-1/identity-links/confirm",
        json={
            "acessorias_company_external_id": "company-1",
            "idempotency_key": "key-1",
        },
    )

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": "Acessórias company unavailable"}
    assert "company-1" not in response.text


def test_ui_action_bridge_rejects_invalid_body_without_cacheable_details(
    ui_client: TestClient,
) -> None:
    _login(ui_client)

    response = ui_client.post(
        "/admin/acessorias/ui/api/contacts/contact-1/identity-links/confirm",
        json={"acessorias_company_external_id": "company-1"},
    )

    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": "Invalid administrative command body"}


def test_ui_action_shell_exposes_explicit_local_action_contract(
    ui_client: TestClient,
) -> None:
    _login(ui_client)

    shell = ui_client.get("/admin/acessorias/ui")

    assert shell.status_code == 200
    assert 'id="action-confirmation"' in shell.text
    assert 'id="confirm-action"' in shell.text
    assert 'id="reject-action"' in shell.text
    assert 'id="discover-action"' in shell.text
    assert "/admin/acessorias/ui/api/contacts/" in shell.text
    assert "/identity-links/confirm" in shell.text
    assert "/identity-links/" in shell.text and "/reject" in shell.text
    assert "/identity-discovery" in shell.text
    assert "crypto.randomUUID" in shell.text
    assert "isUncertainActionError" in shell.text
    assert "Retry with the same key" in shell.text
    assert "localStorage" not in shell.text
    assert "sessionStorage" not in shell.text
    assert "ADMIN_API_TOKEN" not in shell.text


def test_ui_read_bridge_requires_session_and_preserves_opaque_cursor(
    ui_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    async def fake_list(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "items": [
                {
                    "digisac_contact_external_id": "contact-1",
                    "display_name": "Safe display name",
                    "is_group": False,
                    "state": "candidate",
                    "candidate_company_count": 1,
                    "links": [],
                    "evidence": [],
                }
            ],
            "next_after": ("contact-1", 1) if kwargs["after"] is None else None,
        }

    monkeypatch.setattr(admin_ui, "list_identity_link_projection", fake_list)

    unauthenticated = TestClient(app).get(
        "/admin/acessorias/ui/api/identity-links?state=candidate&limit=1"
    )
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["cache-control"] == "no-store"

    _login(ui_client)
    first = ui_client.get(
        "/admin/acessorias/ui/api/identity-links",
        params={"state": "candidate", "limit": "1"},
    )
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    assert isinstance(cursor, str)
    assert calls == [{"state": "candidate", "after": None, "limit": 1}]

    second = ui_client.get(
        "/admin/acessorias/ui/api/identity-links",
        params={"state": "candidate", "limit": "1", "cursor": cursor},
    )
    assert second.status_code == 200
    assert calls[1] == {"state": "candidate", "after": ("contact-1", 1), "limit": 1}
    invalid = ui_client.get(
        "/admin/acessorias/ui/api/identity-links",
        params={"state": "confirmed", "limit": "1"},
    )
    assert invalid.status_code == 400
    assert invalid.headers["cache-control"] == "no-store"


def test_ui_read_bridge_returns_sanitized_detail_and_active_companies(
    ui_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_contact(contact: str) -> dict[str, object] | None:
        if contact == "missing":
            return None
        return {
            "digisac_contact_external_id": "contact-1",
            "display_name": "Safe display name",
            "is_group": True,
            "state": "unresolved",
            "candidate_company_count": 0,
            "links": [],
            "evidence": [],
            "transitions": [],
            "candidate_companies": [],
        }

    async def fake_companies(**kwargs: object) -> dict[str, object]:
        assert kwargs == {"query": "visible", "after": None, "limit": 25}
        return {
            "items": [
                {
                    "acessorias_company_external_id": "company-1",
                    "display_name": "Visible Active",
                    "is_present": True,
                    "is_active": True,
                    "available": True,
                }
            ],
            "next_after": None,
        }

    monkeypatch.setattr(admin_ui, "get_identity_contact_projection", fake_contact)
    monkeypatch.setattr(admin_ui, "list_active_company_projection", fake_companies)
    _login(ui_client)

    detail = ui_client.get("/admin/acessorias/ui/api/contacts/contact-1/identity")
    companies = ui_client.get(
        "/admin/acessorias/ui/api/companies",
        params={"query": "visible", "limit": "25"},
    )

    assert detail.status_code == 200
    assert detail.json()["is_group"] is True
    assert companies.status_code == 200
    assert companies.json() == {
        "items": [
            {
                "acessorias_company_external_id": "company-1",
                "display_name": "Visible Active",
                "is_present": True,
                "is_active": True,
                "available": True,
            }
        ],
        "next_cursor": None,
    }
    assert (
        detail.headers["cache-control"]
        == companies.headers["cache-control"]
        == "no-store"
    )

    missing = ui_client.get("/admin/acessorias/ui/api/contacts/missing/identity")
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "no-store"


def test_production_login_marks_session_cookie_secure(
    ui_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "environment", "production")

    login = ui_client.post(
        "/admin/acessorias/login",
        data={"password": UI_PASSWORD},
        follow_redirects=False,
    )

    assert login.status_code == 303
    assert _session_cookie(login)["cai_admin_session"]["secure"]


def test_expired_cookie_fails_closed_without_sliding_expiry(
    ui_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api import admin_ui

    monkeypatch.setattr(admin_ui, "time", lambda: 2_000_000_000)
    expired = admin_ui._encode_session_cookie(
        expires_at=1_999_999_999, secret=UI_SECRET
    )
    ui_client.cookies.set("cai_admin_session", expired, path="/admin/acessorias")

    response = ui_client.get("/admin/acessorias/ui")

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    assert "Administrative review workspace" not in response.text
    assert "Invalid credentials" in response.text


def test_logout_is_repeatable_and_clears_session(ui_client: TestClient) -> None:
    login = ui_client.post(
        "/admin/acessorias/login",
        data={"password": UI_PASSWORD},
        follow_redirects=False,
    )
    assert login.status_code == 303

    first = ui_client.post("/admin/acessorias/logout", follow_redirects=False)
    second = ui_client.post("/admin/acessorias/logout", follow_redirects=False)

    assert first.status_code == second.status_code == 303
    assert (
        first.headers["cache-control"] == second.headers["cache-control"] == "no-store"
    )
    assert _session_cookie(first)["cai_admin_session"]["max-age"] == "0"
    assert ui_client.get("/admin/acessorias/ui").status_code == 401


def test_concurrent_sessions_do_not_share_logout_state(
    ui_client: TestClient,
) -> None:
    async def exercise_sessions() -> tuple[int, int, int]:
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://test") as first,
            httpx.AsyncClient(transport=transport, base_url="http://test") as second,
        ):
            first_login, second_login = await asyncio.gather(
                first.post(
                    "/admin/acessorias/login",
                    data={"password": UI_PASSWORD},
                    follow_redirects=False,
                ),
                second.post(
                    "/admin/acessorias/login",
                    data={"password": UI_PASSWORD},
                    follow_redirects=False,
                ),
            )
            first_shell, second_shell = await asyncio.gather(
                first.get("/admin/acessorias/ui"),
                second.get("/admin/acessorias/ui"),
            )
            await first.post("/admin/acessorias/logout", follow_redirects=False)
            second_after_first_logout = await second.get("/admin/acessorias/ui")
            return (
                first_login.status_code,
                first_shell.status_code,
                min(
                    second_login.status_code,
                    second_shell.status_code,
                    second_after_first_logout.status_code,
                ),
            )

    first_login_status, first_shell_status, second_minimum_status = asyncio.run(
        exercise_sessions()
    )
    assert first_login_status == 303
    assert first_shell_status == 200
    assert second_minimum_status == 200


def test_ui_routes_are_not_added_to_openapi(ui_client: TestClient) -> None:
    document = ui_client.get("/openapi.json").json()

    assert "/admin/acessorias/ui" not in document["paths"]
    assert "/admin/acessorias/login" not in document["paths"]
    assert "/admin/acessorias/logout" not in document["paths"]
    assert "/admin/acessorias/identity-links" in document["paths"]
