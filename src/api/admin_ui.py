"""Authenticated administrative UI shell and in-process BFF boundary."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
from dataclasses import dataclass
from time import time
from typing import Any, cast
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadData, URLSafeTimedSerializer

from src.core.config import require_admin_ui_configuration, settings
from src.core.identity_admin import (
    get_identity_contact_projection,
    list_active_company_projection,
    list_identity_link_projection,
)
from src.core.identity_resolution import (
    confirm_identity_link_admin,
    discover_identity_admin,
    reject_identity_link_admin,
)


SESSION_COOKIE_NAME = "cai_admin_session"
SESSION_LIFETIME_SECONDS = 60 * 60
SESSION_COOKIE_PATH = "/admin/acessorias"
_SESSION_VERSION = 1
_MAX_SESSION_COOKIE_LENGTH = 4096
_GENERIC_AUTH_ERROR = "Invalid credentials"


@dataclass(frozen=True)
class AdminUIContext:
    """Authenticated server-side bridge to SPEC-0012 application services."""

    request: Request
    expires_at: int

    async def list_identity_links(self, **kwargs: Any) -> dict[str, Any]:
        return await list_identity_link_projection(**kwargs)

    async def get_identity_contact(
        self, contact_external_id: str
    ) -> dict[str, Any] | None:
        return await get_identity_contact_projection(contact_external_id)

    async def list_active_companies(self, **kwargs: Any) -> dict[str, Any]:
        return await list_active_company_projection(**kwargs)

    async def confirm_identity_link(
        self, contact_external_id: str, company_external_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        return await confirm_identity_link_admin(
            contact_external_id, company_external_id, **kwargs
        )

    async def reject_identity_link(
        self, contact_external_id: str, company_external_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        return await reject_identity_link_admin(
            contact_external_id, company_external_id, **kwargs
        )

    async def discover_identity(
        self, contact_external_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        return await discover_identity_admin(contact_external_id, **kwargs)


def _session_payload(*, expires_at: int) -> bytes:
    return json.dumps(
        {
            "authenticated": True,
            "expires_at": expires_at,
            "version": _SESSION_VERSION,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _session_serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        secret,
        salt="cai-admin-session",
        signer_kwargs={"digest_method": hashlib.sha256},
    )


def _encode_session_cookie(*, expires_at: int, secret: str) -> str:
    payload = json.loads(_session_payload(expires_at=expires_at).decode("utf-8"))
    return _session_serializer(secret).dumps(payload)


def _decode_session_cookie(value: str | None, *, secret: str, now: int) -> int | None:
    if not value or len(value) > _MAX_SESSION_COOKIE_LENGTH:
        return None
    try:
        payload = _session_serializer(secret).loads(
            value, max_age=SESSION_LIFETIME_SECONDS
        )
        if not isinstance(payload, dict):
            return None
        payload = cast(dict[str, Any], payload)
        if set(payload) != {"authenticated", "expires_at", "version"}:
            return None
        if (
            payload["authenticated"] is not True
            or payload["version"] != _SESSION_VERSION
        ):
            return None
        expires_at = payload["expires_at"]
        if not isinstance(expires_at, int) or isinstance(expires_at, bool):
            return None
        if expires_at <= now:
            return None
        return expires_at
    except (BadData, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return None


def _session_expiry(request: Request) -> int | None:
    try:
        _, secret = require_admin_ui_configuration()
    except RuntimeError:
        return None
    return _decode_session_cookie(
        request.cookies.get(SESSION_COOKIE_NAME), secret=secret, now=int(time())
    )


def _is_production() -> bool:
    return settings.environment.strip().lower() in {"production", "prod", "release"}


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    return response


def _clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path=SESSION_COOKIE_PATH)


def _set_session(response: Response, *, expires_at: int, secret: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        _encode_session_cookie(expires_at=expires_at, secret=secret),
        max_age=SESSION_LIFETIME_SECONDS,
        expires=expires_at,
        path=SESSION_COOKIE_PATH,
        secure=_is_production(),
        httponly=True,
        samesite="strict",
    )


def _generic_auth_response(
    *,
    status_code: int = status.HTTP_401_UNAUTHORIZED,
    error: str | None = _GENERIC_AUTH_ERROR,
) -> HTMLResponse:
    response = HTMLResponse(_login_page(error), status_code=status_code)
    _no_store(response)
    return response


def _login_page(error: str | None = None) -> str:
    message = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Identity review sign in</title>
  <style>
    :root {{ color-scheme: light; font-family: system-ui, sans-serif; }}
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #f4f6f8; color: #17202a; }}
    main {{ width: min(28rem, calc(100% - 2rem)); padding: 2rem; background: white; border: 1px solid #d8dee4; border-radius: .75rem; box-shadow: 0 .5rem 1.5rem #17202a18; }}
    label {{ display: block; margin: 1rem 0 .4rem; font-weight: 600; }}
    input, button {{ box-sizing: border-box; width: 100%; min-height: 2.75rem; padding: .6rem .75rem; font: inherit; border: 1px solid #8c98a4; border-radius: .35rem; }}
    button {{ margin-top: 1.25rem; background: #145da0; color: white; border-color: #145da0; font-weight: 700; cursor: pointer; }}
    .error {{ color: #a61b1b; }}
  </style>
</head>
<body>
  <main>
    <h1>Identity review</h1>
    <p>Sign in to access the administrative review shell.</p>
    {message}
    <form method="post" action="/admin/acessorias/login" autocomplete="off">
      <label for="password">Operator password</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required>
      <button type="submit">Sign in</button>
    </form>
  </main>
</body>
</html>"""


def _protected_shell() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Identity review</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, sans-serif; }
    body { margin: 0; background: #f4f6f8; color: #17202a; }
    header { display: flex; flex-wrap: wrap; gap: 1rem; align-items: center; justify-content: space-between; padding: 1rem clamp(1rem, 4vw, 3rem); background: #12344d; color: white; }
    main { width: min(72rem, calc(100% - 2rem)); margin: 2rem auto; }
    section { padding: clamp(1rem, 3vw, 2rem); background: white; border: 1px solid #d8dee4; border-radius: .75rem; }
    .status { color: #285943; font-weight: 700; }
    button { min-height: 2.5rem; padding: .5rem .8rem; font: inherit; border-radius: .35rem; border: 1px solid #8c98a4; cursor: pointer; }
  </style>
</head>
<body>
  <header>
    <div><strong>Identity review</strong><div class="status">Signed-in session active</div></div>
    <form method="post" action="/admin/acessorias/logout"><button type="submit">Sign out</button></form>
  </header>
  <main>
    <section aria-labelledby="shell-heading">
      <h1 id="shell-heading">Administrative review workspace</h1>
      <p>The review queue and contact projections will appear here.</p>
    </section>
  </main>
</body>
</html>"""


def _read_password(body: bytes, content_type: str) -> str | None:
    if len(body) > 16 * 1024:
        return None
    try:
        if content_type.split(";", 1)[0].strip().lower() == "application/json":
            payload = json.loads(body.decode("utf-8"))
            password = payload.get("password") if isinstance(payload, dict) else None
        else:
            fields = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            values = fields.get("password", [])
            password = values[0] if values else None
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, ValueError):
        return None
    return password if isinstance(password, str) else None


def _password_matches(submitted: str | None, configured: str | None) -> bool:
    if submitted is None or configured is None:
        return False
    return hmac.compare_digest(submitted, configured)


async def require_admin_ui_context(request: Request) -> AdminUIContext:
    expires_at = _session_expiry(request)
    if expires_at is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_AUTH_ERROR
        )
    return AdminUIContext(request=request, expires_at=expires_at)


admin_ui_router = APIRouter(prefix="/admin/acessorias")


@admin_ui_router.get("/ui", include_in_schema=False, response_class=HTMLResponse)
async def administrative_ui(request: Request) -> Response:
    if _session_expiry(request) is None:
        response = _generic_auth_response(
            error=_GENERIC_AUTH_ERROR
            if request.cookies.get(SESSION_COOKIE_NAME)
            else None
        )
        if request.cookies.get(SESSION_COOKIE_NAME):
            _clear_session(response)
        return response
    return _no_store(HTMLResponse(_protected_shell()))


@admin_ui_router.post("/login", include_in_schema=False, response_class=HTMLResponse)
async def administrative_login(request: Request) -> Response:
    submitted_password = _read_password(
        await request.body(), request.headers.get("content-type", "")
    )
    try:
        configured_password, secret = require_admin_ui_configuration()
    except RuntimeError:
        return _generic_auth_response()
    if not _password_matches(submitted_password, configured_password):
        return _generic_auth_response()

    expires_at = int(time()) + SESSION_LIFETIME_SECONDS
    response = RedirectResponse(
        "/admin/acessorias/ui", status_code=status.HTTP_303_SEE_OTHER
    )
    _set_session(response, expires_at=expires_at, secret=secret)
    return _no_store(response)


@admin_ui_router.post("/logout", include_in_schema=False)
async def administrative_logout() -> Response:
    response = RedirectResponse(
        "/admin/acessorias/ui", status_code=status.HTTP_303_SEE_OTHER
    )
    _clear_session(response)
    return _no_store(response)
