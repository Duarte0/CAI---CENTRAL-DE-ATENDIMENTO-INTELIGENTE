"""Focused tests for the authenticated administrative UI shell."""

from __future__ import annotations

import asyncio
from http.cookies import SimpleCookie

import httpx
import pytest
from fastapi.testclient import TestClient

from main import app
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
