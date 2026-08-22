"""Authenticated administrative UI shell and in-process BFF boundary."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
from dataclasses import dataclass
from time import time
from typing import Annotated, Any, cast
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadData, URLSafeTimedSerializer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.api.admin_routes import (
    CompanyListResponse,
    IdentityContactDetail,
    IdentityDiscoveryResponse,
    IdentityLinkCommandResponse,
    IdentityLinkListResponse,
    _decode_cursor,
    _encode_cursor,
    _raise_identity_command_http_error,
    _validated_limit,
    _validated_query,
)
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
_UI_IDENTITY_STATES = frozenset({"candidate", "ambiguous", "unresolved"})


def _validate_ui_opaque_value(value: str, field_name: str) -> str:
    if value != value.strip() or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ValueError(f"{field_name} must be opaque and nonblank")
    return value


class UIIdentityCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _validate_ui_opaque_value(value, "idempotency_key")


class UIIdentityLinkConfirmRequest(UIIdentityCommandRequest):
    acessorias_company_external_id: str = Field(min_length=1, max_length=200)

    @field_validator("acessorias_company_external_id")
    @classmethod
    def validate_company_external_id(cls, value: str) -> str:
        return _validate_ui_opaque_value(value, "acessorias_company_external_id")


class UIIdentityLinkRejectRequest(UIIdentityCommandRequest):
    pass


class UIIdentityDiscoveryRequest(UIIdentityCommandRequest):
    pass


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


def _ui_http_error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=detail,
        headers={"Cache-Control": "no-store"},
    )


def _ui_wrap_http_error(exc: HTTPException) -> HTTPException:
    return _ui_http_error(exc.status_code, str(exc.detail))


def _ui_raise_identity_command_http_error(exc: Exception) -> None:
    try:
        _raise_identity_command_http_error(exc)
    except HTTPException as mapped:
        raise _ui_wrap_http_error(mapped) from exc
    raise _ui_http_error(409, "Identity command could not be completed") from exc


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
    :root { color-scheme: light; font-family: system-ui, sans-serif; --ink: #17202a; --muted: #52606d; --line: #d8dee4; --panel: #fff; --page: #f4f6f8; --accent: #145da0; --accent-soft: #e8f1f8; --danger: #a61b1b; --success: #285943; }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--page); color: var(--ink); line-height: 1.45; }
    header { display: flex; flex-wrap: wrap; gap: 1rem; align-items: center; justify-content: space-between; padding: 1rem clamp(1rem, 4vw, 3rem); background: #12344d; color: white; }
    header h1 { margin: 0; font-size: 1.35rem; }
    header p { margin: .2rem 0 0; color: #d9edf9; }
    main { width: min(88rem, calc(100% - 2rem)); margin: 1.5rem auto 3rem; }
    section { min-width: 0; padding: clamp(1rem, 2.5vw, 1.5rem); background: var(--panel); border: 1px solid var(--line); border-radius: .75rem; }
    h2, h3 { margin-top: 0; }
    button, input, select { min-height: 2.5rem; padding: .5rem .7rem; font: inherit; border-radius: .35rem; border: 1px solid #8c98a4; }
    button { cursor: pointer; background: white; color: var(--ink); }
    button:disabled { cursor: not-allowed; opacity: .55; }
    button:hover { border-color: var(--accent); }
    button:focus-visible, input:focus-visible, select:focus-visible { outline: .2rem solid #f3b61f; outline-offset: .15rem; }
    button.primary { background: var(--accent); border-color: var(--accent); color: white; font-weight: 700; }
    button.danger { color: var(--danger); border-color: #d98b8b; }
    .toolbar { display: flex; flex-wrap: wrap; align-items: end; justify-content: space-between; gap: 1rem; margin-bottom: 1rem; }
    .toolbar h2 { margin-bottom: .25rem; }
    .toolbar p { margin: 0; color: var(--muted); }
    .filters { display: flex; flex-wrap: wrap; gap: .5rem; }
    .filters label { display: inline-flex; align-items: center; gap: .35rem; padding: .45rem .65rem; border: 1px solid var(--line); border-radius: .35rem; cursor: pointer; }
    .filters input { min-height: auto; accent-color: var(--accent); }
    .workspace { display: grid; grid-template-columns: minmax(15rem, .8fr) minmax(20rem, 1.4fr) minmax(15rem, .8fr); gap: 1rem; align-items: start; }
    .workspace section { min-height: 18rem; }
    .status { min-height: 1.5rem; color: var(--muted); }
    .status.error { color: var(--danger); font-weight: 600; }
    .status.success { color: var(--success); font-weight: 600; }
    .queue-list, .company-list, .detail-list { display: grid; gap: .55rem; padding: 0; margin: 1rem 0; list-style: none; }
    .queue-item { width: 100%; display: block; text-align: left; padding: .75rem; border: 1px solid var(--line); border-radius: .45rem; background: white; }
    .queue-item[aria-pressed="true"] { border-color: var(--accent); background: var(--accent-soft); box-shadow: 0 0 0 .1rem #145da033; }
    .queue-item strong, .queue-item span { display: block; }
    .queue-item span, .muted { color: var(--muted); }
    .state { display: inline-block; width: fit-content; margin-top: .35rem; padding: .1rem .4rem; border-radius: .25rem; background: #edf0f2; color: var(--ink); font-size: .86rem; font-weight: 700; }
    .detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .75rem 1rem; margin-bottom: 1.25rem; }
    .detail-grid dt { color: var(--muted); font-size: .86rem; }
    .detail-grid dd { margin: 0; font-weight: 600; overflow-wrap: anywhere; }
    .subheading { margin: 1.25rem 0 .5rem; font-size: 1rem; }
    .detail-card, .company-result { padding: .65rem; border: 1px solid var(--line); border-radius: .35rem; }
    .detail-card p, .company-result p { margin: .15rem 0; }
    .detail-card .action-button, .company-result .action-button { margin-top: .5rem; }
    .action-panel { margin-top: 1.5rem; padding-top: 1rem; border-top: 1px solid var(--line); }
    .action-controls { display: flex; flex-wrap: wrap; gap: .5rem; }
    .action-target { min-height: 1.5rem; color: var(--muted); }
    dialog { width: min(32rem, calc(100% - 2rem)); padding: 1.25rem; color: var(--ink); border: 1px solid var(--line); border-radius: .65rem; box-shadow: 0 1rem 3rem #17202a44; }
    dialog::backdrop { background: #17202a99; }
    dialog h3 { margin-top: 0; }
    .dialog-actions { display: flex; justify-content: flex-end; gap: .5rem; margin-top: 1rem; }
    .search-form { display: flex; gap: .5rem; align-items: end; }
    .search-form label { flex: 1; }
    .search-form input { width: 100%; margin-top: .3rem; }
    .pagination { display: flex; justify-content: flex-end; margin-top: 1rem; }
    .visually-hidden { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
    .global-message { min-height: 1.5rem; margin-bottom: 1rem; }
    @media (max-width: 72rem) { .workspace { grid-template-columns: minmax(15rem, .8fr) minmax(20rem, 1.2fr); } .workspace section:last-child { grid-column: 1 / -1; } }
    @media (max-width: 48rem) { main { width: min(100% - 1rem, 42rem); margin-top: .75rem; } .workspace { display: block; } .workspace section { margin-bottom: 1rem; } .search-form { align-items: stretch; flex-direction: column; } .detail-grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <header>
    <div><h1>Identity review</h1><p id="session-status" aria-live="polite">Signed-in session active</p></div>
    <form method="post" action="/admin/acessorias/logout"><button type="submit">Sign out</button></form>
  </header>
  <main>
    <section class="toolbar" aria-labelledby="queue-heading">
      <div>
        <h2 id="queue-heading">Identity queue</h2>
        <p>Review server-projected contacts and summarized evidence.</p>
      </div>
      <div class="filters" role="group" aria-label="Queue filters">
        <label><input type="radio" name="queue-state" value="candidate" checked> Candidate</label>
        <label><input type="radio" name="queue-state" value="ambiguous"> Ambiguous</label>
        <label><input type="radio" name="queue-state" value="unresolved"> Unresolved</label>
        <button class="primary" id="refresh-queue" type="button">Refresh</button>
      </div>
    </section>
    <p id="global-message" class="global-message status" role="alert" aria-live="polite"></p>
    <div class="workspace">
      <section id="identity-queue" aria-labelledby="queue-list-heading">
        <h3 id="queue-list-heading">Contacts</h3>
        <p id="queue-status" class="status" role="status" aria-live="polite">Loading queue…</p>
        <ul id="queue-items" class="queue-list"></ul>
        <div class="pagination"><button id="queue-next" type="button" hidden>Next page</button></div>
      </section>
      <section id="contact-detail" aria-labelledby="detail-heading">
        <h3 id="detail-heading">Contact detail</h3>
        <p id="detail-status" class="status" role="status" aria-live="polite">Select a contact to inspect its projection.</p>
        <div id="detail-content"></div>
        <div id="review-actions" class="action-panel" aria-labelledby="review-actions-heading">
          <h4 id="review-actions-heading" class="subheading">Review actions</h4>
          <p id="selected-target" class="action-target" role="status" aria-live="polite">Select a contact before choosing an action.</p>
          <p id="action-status" class="status" role="status" aria-live="polite"></p>
          <div class="action-controls">
            <button id="confirm-action" class="primary" type="button" disabled>Confirm selected company</button>
            <button id="reject-action" class="danger" type="button" disabled>Reject selected link</button>
            <button id="discover-action" type="button" disabled>Run deterministic discovery</button>
          </div>
        </div>
      </section>
      <section id="company-search" aria-labelledby="company-heading">
        <h3 id="company-heading">Active company search</h3>
        <form id="company-search-form" class="search-form">
          <label for="company-query">Search present and active companies
            <input id="company-query" name="query" type="search" maxlength="120" autocomplete="off">
          </label>
          <button class="primary" type="submit">Search</button>
        </form>
        <p id="company-status" class="status" role="status" aria-live="polite">No search submitted.</p>
        <ul id="company-results" class="company-list"></ul>
        <div class="pagination"><button id="company-next" type="button" hidden>Next page</button></div>
      </section>
    </div>
  </main>
  <dialog id="action-confirmation" aria-labelledby="action-confirmation-heading">
    <h3 id="action-confirmation-heading">Confirm review action</h3>
    <p id="action-confirmation-text"></p>
    <div class="dialog-actions">
      <button id="cancel-action" type="button">Cancel</button>
      <button id="confirm-dialog-action" class="primary" type="button">Continue</button>
    </div>
  </dialog>
  <script>
    (() => {
      "use strict";

      const state = {
        queueState: "candidate",
        queueProjection: null,
        queueRequest: 0,
        selectedContact: null,
        detailRequest: 0,
        detailProjection: null,
        companyQuery: "",
        companyProjection: null,
        companyRequest: 0,
        selectedCompanyExternalId: null,
        selectedLinkExternalId: null,
        actionRequest: 0,
        actionInFlight: false,
        pendingAction: null,
        confirmationAction: null,
      };

      const element = (id) => document.getElementById(id);
      const queueStatus = element("queue-status");
      const detailStatus = element("detail-status");
      const companyStatus = element("company-status");
      const globalMessage = element("global-message");
      const selectedTarget = element("selected-target");
      const actionStatus = element("action-status");
      const actionDialog = element("action-confirmation");
      const actionDialogText = element("action-confirmation-text");

      function setStatus(target, message, kind = "") {
        target.textContent = message;
        target.className = `status ${kind}`.trim();
      }

      function failure(code, statusCode) {
        const error = new Error("read request failed");
        error.code = code;
        error.status = statusCode;
        return error;
      }

      function newIdempotencyKey() {
        if (window.crypto && typeof window.crypto.randomUUID === "function") {
          return window.crypto.randomUUID();
        }
        const bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
      }

      async function readJSON(path) {
        const controller = new AbortController();
        const timer = window.setTimeout(() => controller.abort(), 8000);
        try {
          const response = await fetch(path, {
            credentials: "same-origin",
            headers: { "Accept": "application/json" },
            signal: controller.signal,
          });
          if (response.status === 401) {
            window.location.assign("/admin/acessorias/ui");
            throw failure("session-expired", 401);
          }
          if (!response.ok) throw failure("http", response.status);
          return await response.json();
        } catch (error) {
          if (error && error.name === "AbortError") throw failure("timeout");
          if (error && error.code) throw error;
          throw failure("network");
        } finally {
          window.clearTimeout(timer);
        }
      }

      function errorText(error, resource) {
        if (error.code === "session-expired") return "Your session expired. Sign in again.";
        if (error.code === "timeout") return `${resource} timed out. Retry when ready.`;
        if (error.code === "network") return `${resource} is unavailable. Check the connection and retry.`;
        if (error.status === 404) return resource === "Contact" ? "The selected contact is no longer available." : `${resource} was not found.`;
        if (error.status === 429) return `${resource} is rate limited. Retry shortly.`;
        return `${resource} could not be loaded safely. Retry the read.`;
      }

      function retryButton(target, callback) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = "Retry";
        button.addEventListener("click", callback, { once: true });
        target.append(document.createTextNode(" "), button);
      }

      function addText(parent, text, className = "") {
        const node = document.createElement("span");
        node.textContent = text;
        if (className) node.className = className;
        parent.append(node);
        return node;
      }

      function renderQueue(projection) {
        state.queueProjection = projection;
        const list = element("queue-items");
        list.replaceChildren();
        if (!projection.items.length) {
          addText(list, "Queue is empty for this filter.", "muted");
          setStatus(queueStatus, "No contacts match this filter.");
        } else {
          projection.items.forEach((item) => {
            const row = document.createElement("li");
            const button = document.createElement("button");
            button.type = "button";
            button.className = "queue-item";
            button.setAttribute("aria-pressed", String(item.digisac_contact_external_id === state.selectedContact));
            addText(button, item.display_name || "Unnamed contact");
            addText(button, item.digisac_contact_external_id, "muted");
            addText(button, `${item.candidate_company_count} candidate compan${item.candidate_company_count === 1 ? "y" : "ies"}`, "muted");
            addText(button, item.state, "state");
            button.addEventListener("click", () => selectContact(item.digisac_contact_external_id));
            row.append(button);
            list.append(row);
          });
          setStatus(queueStatus, `${projection.items.length} contact${projection.items.length === 1 ? "" : "s"} loaded.`);
        }
        const next = element("queue-next");
        next.hidden = !projection.next_cursor;
        next.onclick = () => loadQueue(projection.next_cursor);
      }

      async function loadQueue(cursor = null) {
        const request = ++state.queueRequest;
        const filter = state.queueState;
        setStatus(queueStatus, "Loading queue…");
        const params = new URLSearchParams({ state: filter, limit: "25" });
        if (cursor) params.set("cursor", cursor);
        try {
          const projection = await readJSON(`/admin/acessorias/ui/api/identity-links?${params.toString()}`);
          if (request !== state.queueRequest || filter !== state.queueState) return;
          renderQueue(projection);
        } catch (error) {
          if (request !== state.queueRequest || filter !== state.queueState) return;
          setStatus(queueStatus, errorText(error, "Queue"), "error");
          retryButton(queueStatus, () => loadQueue(cursor));
        }
      }

      function addField(parent, label, value) {
        const item = document.createElement("div");
        const term = document.createElement("dt");
        term.textContent = label;
        const description = document.createElement("dd");
        description.textContent = value;
        item.append(term, description);
        parent.append(item);
      }

      function renderDetail(projection) {
        state.detailProjection = projection;
        const content = element("detail-content");
        content.replaceChildren();
        const grid = document.createElement("dl");
        grid.className = "detail-grid";
        addField(grid, "Contact", projection.display_name || "Unnamed contact");
        addField(grid, "External ID", projection.digisac_contact_external_id);
        addField(grid, "State", projection.state);
        addField(grid, "Group", projection.is_group === true ? "Yes" : projection.is_group === false ? "No" : "Not specified");
        addField(grid, "Candidate companies", String(projection.candidate_company_count));
        content.append(grid);

        const sections = [
          ["Links", projection.links, (link) => `${link.display_name} · ${link.state} · ${link.acessorias_company_external_id}`],
          ["Evidence summaries", projection.evidence, (item) => `${item.evidence_type} · ${item.count} observed · ${item.latest_observed_at}`],
          ["History", projection.transitions, (item) => `${item.to_state} · ${item.reason} · ${item.created_at}`],
          ["Candidate companies", projection.candidate_companies, (item) => `${item.display_name} · ${item.available === true ? "available" : "unavailable"}`],
        ];
        sections.forEach(([heading, items, formatter]) => {
          const title = document.createElement("h4");
          title.className = "subheading";
          title.textContent = heading;
          content.append(title);
          const list = document.createElement("ul");
          list.className = "detail-list";
          if (!items.length) addText(list, `No ${heading.toLowerCase()} recorded.`, "muted");
          items.forEach((item) => {
            const row = document.createElement("li");
            row.className = "detail-card";
            row.textContent = formatter(item);
            if (heading === "Links") {
              const button = document.createElement("button");
              button.type = "button";
              button.className = "action-button danger";
              button.textContent = "Select for rejection";
              button.disabled = state.actionInFlight || state.pendingAction !== null;
              button.addEventListener("click", () => selectLink(item));
              row.append(button);
            }
            if (heading === "Candidate companies" && item.available === true) {
              const button = document.createElement("button");
              button.type = "button";
              button.className = "action-button";
              button.textContent = "Select for confirmation";
              button.disabled = state.actionInFlight || state.pendingAction !== null;
              button.addEventListener("click", () => selectCompany(item));
              row.append(button);
            }
            list.append(row);
          });
          content.append(list);
        });
        updateActionPanel();
      }

      async function selectContact(contactId) {
        const changedContact = state.selectedContact !== contactId;
        state.selectedContact = contactId;
        if (changedContact) {
          state.selectedCompanyExternalId = null;
          state.selectedLinkExternalId = null;
          state.detailProjection = null;
          state.actionRequest += 1;
          state.actionInFlight = false;
          state.pendingAction = null;
          closeActionDialog();
          setStatus(actionStatus, "");
        }
        const request = ++state.detailRequest;
        setStatus(detailStatus, "Loading contact detail…");
        try {
          const projection = await readJSON(`/admin/acessorias/ui/api/contacts/${encodeURIComponent(contactId)}/identity`);
          if (request !== state.detailRequest || contactId !== state.selectedContact) return;
          renderDetail(projection);
          setStatus(detailStatus, "Contact detail loaded.", "success");
          if (state.queueProjection) renderQueue(state.queueProjection);
        } catch (error) {
          if (request !== state.detailRequest || contactId !== state.selectedContact) return;
          if (error.status === 404) {
            state.selectedContact = null;
            element("detail-content").replaceChildren();
          }
          setStatus(detailStatus, errorText(error, "Contact"), "error");
          retryButton(detailStatus, () => selectContact(contactId));
        }
      }

      function renderCompanies(projection) {
        state.companyProjection = projection;
        const list = element("company-results");
        list.replaceChildren();
        const visible = projection.items.filter((item) => item.is_present === true && item.is_active === true && item.available === true);
        if (!visible.length) {
          addText(list, "No present and active companies found.", "muted");
          setStatus(companyStatus, "No companies match this search.");
        } else {
          visible.forEach((item) => {
            const row = document.createElement("li");
            row.className = "company-result";
            addText(row, item.display_name);
            addText(row, item.acessorias_company_external_id, "muted");
            const button = document.createElement("button");
            button.type = "button";
            button.className = "action-button";
            button.textContent = state.selectedCompanyExternalId === item.acessorias_company_external_id ? "Selected for confirmation" : "Select for confirmation";
            button.disabled = state.actionInFlight || state.pendingAction !== null;
            button.addEventListener("click", () => selectCompany(item));
            row.append(button);
            list.append(row);
          });
          setStatus(companyStatus, `${visible.length} active compan${visible.length === 1 ? "y" : "ies"} found.`);
        }
        updateActionPanel();
        const next = element("company-next");
        next.hidden = !projection.next_cursor;
        next.onclick = () => loadCompanies(projection.next_cursor);
      }

      async function loadCompanies(cursor = null) {
        const request = ++state.companyRequest;
        const query = state.companyQuery;
        setStatus(companyStatus, "Loading companies…");
        const params = new URLSearchParams({ limit: "25" });
        if (query) params.set("query", query);
        if (cursor) params.set("cursor", cursor);
        try {
          const projection = await readJSON(`/admin/acessorias/ui/api/companies?${params.toString()}`);
          if (request !== state.companyRequest || query !== state.companyQuery) return;
          renderCompanies(projection);
        } catch (error) {
          if (request !== state.companyRequest || query !== state.companyQuery) return;
          setStatus(companyStatus, errorText(error, "Company search"), "error");
          retryButton(companyStatus, () => loadCompanies(cursor));
        }
      }

      function currentContactReady() {
        return state.selectedContact !== null
          && state.detailProjection !== null
          && state.detailProjection.digisac_contact_external_id === state.selectedContact;
      }

      function selectedCompany() {
        if (!state.selectedCompanyExternalId) return null;
        const sources = [];
        if (state.companyProjection) sources.push(...state.companyProjection.items);
        if (state.detailProjection) sources.push(...state.detailProjection.candidate_companies);
        return sources.find((item) =>
          item.acessorias_company_external_id === state.selectedCompanyExternalId
          && item.is_present === true
          && item.is_active === true
          && item.available === true
        ) || null;
      }

      function selectedLink() {
        if (!state.selectedLinkExternalId || !state.detailProjection) return null;
        return state.detailProjection.links.find((item) =>
          item.acessorias_company_external_id === state.selectedLinkExternalId
        ) || null;
      }

      function updateActionPanel() {
        const confirmButton = element("confirm-action");
        const rejectButton = element("reject-action");
        const discoverButton = element("discover-action");
        const blocked = state.actionInFlight || state.pendingAction !== null || state.confirmationAction !== null;
        const contactReady = currentContactReady();
        const company = selectedCompany();
        const link = selectedLink();
        confirmButton.disabled = blocked || !contactReady || !company;
        rejectButton.disabled = blocked || !contactReady || !link;
        discoverButton.disabled = blocked || !contactReady;
        if (!contactReady) {
          selectedTarget.textContent = "Select a contact before choosing an action.";
        } else if (company) {
          selectedTarget.textContent = `Confirmation target: ${company.acessorias_company_external_id}`;
        } else if (link) {
          selectedTarget.textContent = `Rejection target: ${link.acessorias_company_external_id}`;
        } else {
          selectedTarget.textContent = "Choose an active company to confirm or a link to reject.";
        }
      }

      function selectCompany(item) {
        if (
          state.actionInFlight
          || state.pendingAction !== null
          || item.is_present !== true
          || item.is_active !== true
          || item.available !== true
        ) return;
        state.selectedCompanyExternalId = item.acessorias_company_external_id;
        state.selectedLinkExternalId = null;
        setStatus(actionStatus, `Company ${item.acessorias_company_external_id} selected for confirmation.`);
        updateActionPanel();
        renderCompanies(state.companyProjection || { items: [], next_cursor: null });
        if (state.detailProjection) renderDetail(state.detailProjection);
      }

      function selectLink(item) {
        if (state.actionInFlight || state.pendingAction !== null || !currentContactReady()) return;
        if (!state.detailProjection.links.some((link) =>
          link.acessorias_company_external_id === item.acessorias_company_external_id
        )) return;
        state.selectedLinkExternalId = item.acessorias_company_external_id;
        state.selectedCompanyExternalId = null;
        setStatus(actionStatus, `Link ${item.acessorias_company_external_id} selected for rejection.`);
        updateActionPanel();
        renderDetail(state.detailProjection);
      }

      function closeActionDialog() {
        state.confirmationAction = null;
        if (actionDialog.open && typeof actionDialog.close === "function") actionDialog.close();
        else actionDialog.removeAttribute("open");
        updateActionPanel();
      }

      function openActionConfirmation(kind) {
        if (!currentContactReady()) {
          setStatus(actionStatus, "Select a current contact before submitting an action.", "error");
          return;
        }
        const company = selectedCompany();
        const link = selectedLink();
        if (kind === "confirm" && !company) {
          setStatus(actionStatus, "Select a present and active company before confirming.", "error");
          return;
        }
        if (kind === "reject" && !link) {
          setStatus(actionStatus, "Select an existing link before rejecting it.", "error");
          return;
        }
        state.confirmationAction = {
          kind,
          contactId: state.selectedContact,
          companyId: kind === "confirm" ? company.acessorias_company_external_id : kind === "reject" ? link.acessorias_company_external_id : null,
        };
        const target = kind === "discover"
          ? `contact ${state.selectedContact}`
          : `${kind === "confirm" ? "company" : "link"} ${kind === "confirm" ? company.acessorias_company_external_id : link.acessorias_company_external_id} for contact ${state.selectedContact}`;
        actionDialogText.textContent = `Review this explicit action for ${target}.`;
        if (typeof actionDialog.showModal === "function") actionDialog.showModal();
        else actionDialog.setAttribute("open", "");
        element("confirm-dialog-action").focus();
        updateActionPanel();
      }

      async function commandJSON(path, payload) {
        const controller = new AbortController();
        const timer = window.setTimeout(() => controller.abort(), 8000);
        try {
          const response = await fetch(path, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Accept": "application/json", "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            signal: controller.signal,
          });
          if (response.status === 401) {
            window.location.assign("/admin/acessorias/ui");
            throw failure("session-expired", 401);
          }
          if (!response.ok) throw failure("http", response.status);
          let result;
          try {
            result = await response.json();
          } catch (_error) {
            throw failure("empty");
          }
          return { status: response.status, result };
        } catch (error) {
          if (error && error.name === "AbortError") throw failure("timeout");
          if (error && error.code) throw error;
          throw failure("network");
        } finally {
          window.clearTimeout(timer);
        }
      }

      function actionErrorText(error) {
        if (error.code === "session-expired") return "Your session expired. Sign in again.";
        if (error.code === "timeout") return "The action timed out. Retry with the same key if you want to check it again.";
        if (error.code === "network") return "The action could not reach the server. Retry with the same key if you want to check it again.";
        if (error.code === "empty") return "The action returned no safe result. Retry with the same key if you want to check it again.";
        if (error.status === 404) return "The selected contact or link is stale. Refreshing its detail is required.";
        if (error.status === 409) return "The action conflicts with a newer decision. Reload the contact detail before trying again.";
        if (error.status === 422) return "The selected company is no longer available. Choose another active company.";
        if (error.status === 429) return "The action is rate limited. Retry with the same key shortly.";
        if (error.status >= 500) return "The server did not confirm the action. Retry with the same key if you want to check it again.";
        return "The action could not be completed safely.";
      }

      function isUncertainActionError(error) {
        return error.code === "timeout"
          || error.code === "network"
          || error.code === "empty"
          || error.status === 408
          || error.status === 429
          || error.status >= 500;
      }

      function renderActionResult(action, outcome) {
        const result = outcome.result || {};
        const replayed = outcome.status === 200 && action.kind !== "discover";
        if (action.kind === "discover") {
          const count = Number.isSafeInteger(result.matched_company_count) ? result.matched_company_count : 0;
          setStatus(actionStatus, `Discovery completed for contact ${action.contactId}; ${count} matching compan${count === 1 ? "y" : "ies"} reported.`, "success");
        } else {
          const companyId = result.acessorias_company_external_id || action.companyId;
          const actionName = action.kind === "confirm" ? "Confirmation" : "Rejection";
          setStatus(actionStatus, `${actionName} ${replayed ? "was already applied" : "completed"} for company ${companyId}${replayed ? " (replay)" : ""}.`, "success");
        }
      }

      async function refreshAfterCommand(contactId) {
        await loadQueue();
        if (state.selectedContact === contactId) await selectContact(contactId);
      }

      function actionPathAndBody(action) {
        const contact = encodeURIComponent(action.contactId);
        if (action.kind === "confirm") {
          return {
            path: `/admin/acessorias/ui/api/contacts/${contact}/identity-links/confirm`,
            body: { acessorias_company_external_id: action.companyId, idempotency_key: action.key },
          };
        }
        if (action.kind === "reject") {
          return {
            path: `/admin/acessorias/ui/api/contacts/${contact}/identity-links/${encodeURIComponent(action.companyId)}/reject`,
            body: { idempotency_key: action.key },
          };
        }
        return {
          path: `/admin/acessorias/ui/api/contacts/${contact}/identity-discovery`,
          body: { idempotency_key: action.key },
        };
      }

      async function runAction(action) {
        if (state.actionInFlight) return;
        const request = ++state.actionRequest;
        state.actionInFlight = true;
        updateActionPanel();
        setStatus(actionStatus, "Submitting action…");
        const requestDetails = actionPathAndBody(action);
        try {
          const outcome = await commandJSON(requestDetails.path, requestDetails.body);
          if (request !== state.actionRequest || state.selectedContact !== action.contactId) return;
          state.actionInFlight = false;
          state.pendingAction = null;
          state.selectedCompanyExternalId = null;
          state.selectedLinkExternalId = null;
          renderActionResult(action, outcome);
          updateActionPanel();
          await refreshAfterCommand(action.contactId);
        } catch (error) {
          if (request !== state.actionRequest) return;
          state.actionInFlight = false;
          if (isUncertainActionError(error)) {
            state.pendingAction = action;
            setStatus(actionStatus, actionErrorText(error), "error");
            retryButton(actionStatus, () => {
              if (state.pendingAction && state.pendingAction.key === action.key) runAction(state.pendingAction);
            });
          } else {
            state.pendingAction = null;
            setStatus(actionStatus, actionErrorText(error), "error");
            if (error.status === 422) state.selectedCompanyExternalId = null;
            if ((error.status === 404 || error.status === 409) && state.selectedContact === action.contactId) {
              await selectContact(action.contactId);
            }
          }
          updateActionPanel();
        }
      }

      function startConfirmedAction() {
        const selection = state.confirmationAction;
        closeActionDialog();
        if (!selection || state.selectedContact !== selection.contactId || !currentContactReady()) {
          setStatus(actionStatus, "The selected contact changed before confirmation. Review it again.", "error");
          return;
        }
        if (
          (selection.kind === "confirm" && (!selectedCompany() || selectedCompany().acessorias_company_external_id !== selection.companyId))
          || (selection.kind === "reject" && (!selectedLink() || selectedLink().acessorias_company_external_id !== selection.companyId))
        ) {
          setStatus(actionStatus, "The selected target changed before confirmation. Review it again.", "error");
          return;
        }
        const action = {
          kind: selection.kind,
          contactId: selection.contactId,
          companyId: selection.companyId,
          key: newIdempotencyKey(),
        };
        state.pendingAction = action;
        runAction(action);
      }

      document.querySelectorAll("input[name=queue-state]").forEach((input) => {
        input.addEventListener("change", () => {
          state.queueState = input.value;
          state.queueProjection = null;
          loadQueue();
        });
      });
      element("refresh-queue").addEventListener("click", () => loadQueue());
      element("confirm-action").addEventListener("click", () => openActionConfirmation("confirm"));
      element("reject-action").addEventListener("click", () => openActionConfirmation("reject"));
      element("discover-action").addEventListener("click", () => openActionConfirmation("discover"));
      element("cancel-action").addEventListener("click", closeActionDialog);
      element("confirm-dialog-action").addEventListener("click", startConfirmedAction);
      element("company-search-form").addEventListener("submit", (event) => {
        event.preventDefault();
        state.companyQuery = element("company-query").value.trim();
        state.companyProjection = null;
        loadCompanies();
      });
      setStatus(globalMessage, "Reads are session-protected and use server projections.");
      updateActionPanel();
      loadQueue();
    })();
  </script>
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


async def require_admin_ui_context(
    request: Request, response: Response
) -> AdminUIContext:
    _no_store(response)
    expires_at = _session_expiry(request)
    if expires_at is None:
        raise _ui_http_error(status.HTTP_401_UNAUTHORIZED, _GENERIC_AUTH_ERROR)
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


def _ui_state(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in _UI_IDENTITY_STATES:
        raise _ui_http_error(400, "Invalid identity state")
    return normalized


def _ui_limit(value: str) -> int:
    try:
        return _validated_limit(value)
    except HTTPException as exc:
        raise _ui_wrap_http_error(exc) from exc


def _ui_query(value: str | None) -> str | None:
    try:
        return _validated_query(value)
    except HTTPException as exc:
        raise _ui_wrap_http_error(exc) from exc


def _ui_after(
    value: str | None,
    *,
    scope: str,
    parameters: dict[str, Any],
) -> tuple[str, int] | None:
    if not value:
        return None
    try:
        return _decode_cursor(value, scope=scope, parameters=parameters)
    except HTTPException as exc:
        raise _ui_wrap_http_error(exc) from exc


@admin_ui_router.get(
    "/ui/api/identity-links",
    include_in_schema=False,
    response_model=IdentityLinkListResponse,
)
async def ui_identity_link_list(
    response: Response,
    context: Annotated[AdminUIContext, Depends(require_admin_ui_context)],
    state: str = "candidate",
    cursor: str | None = None,
    limit: str = "25",
) -> IdentityLinkListResponse:
    selected_state = _ui_state(state)
    selected_limit = _ui_limit(limit)
    parameters = {"state": selected_state}
    after = _ui_after(cursor, scope="identity-links", parameters=parameters)
    projection = await context.list_identity_links(
        state=selected_state, after=after, limit=selected_limit
    )
    next_after = projection["next_after"]
    next_cursor = (
        _encode_cursor(scope="identity-links", parameters=parameters, after=next_after)
        if next_after is not None
        else None
    )
    response.headers["Cache-Control"] = "no-store"
    return IdentityLinkListResponse(items=projection["items"], next_cursor=next_cursor)


@admin_ui_router.get(
    "/ui/api/contacts/{digisac_contact_external_id}/identity",
    include_in_schema=False,
    response_model=IdentityContactDetail,
)
async def ui_identity_contact_detail(
    digisac_contact_external_id: str,
    response: Response,
    context: Annotated[AdminUIContext, Depends(require_admin_ui_context)],
) -> IdentityContactDetail:
    projection = await context.get_identity_contact(digisac_contact_external_id)
    if projection is None:
        raise _ui_http_error(404, "DigiSac contact not found")
    response.headers["Cache-Control"] = "no-store"
    return IdentityContactDetail.model_validate(projection)


@admin_ui_router.get(
    "/ui/api/companies",
    include_in_schema=False,
    response_model=CompanyListResponse,
)
async def ui_active_company_list(
    response: Response,
    context: Annotated[AdminUIContext, Depends(require_admin_ui_context)],
    query: str | None = None,
    cursor: str | None = None,
    limit: str = "25",
) -> CompanyListResponse:
    selected_query = _ui_query(query)
    selected_limit = _ui_limit(limit)
    parameters = {"query": selected_query}
    after = _ui_after(cursor, scope="companies", parameters=parameters)
    projection = await context.list_active_companies(
        query=selected_query, after=after, limit=selected_limit
    )
    next_after = projection["next_after"]
    next_cursor = (
        _encode_cursor(scope="companies", parameters=parameters, after=next_after)
        if next_after is not None
        else None
    )
    response.headers["Cache-Control"] = "no-store"
    return CompanyListResponse(items=projection["items"], next_cursor=next_cursor)


@admin_ui_router.post(
    "/ui/api/contacts/{digisac_contact_external_id}/identity-links/confirm",
    include_in_schema=False,
    response_model=IdentityLinkCommandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ui_identity_link_confirm(
    digisac_contact_external_id: str,
    payload: UIIdentityLinkConfirmRequest,
    response: Response,
    context: Annotated[AdminUIContext, Depends(require_admin_ui_context)],
) -> IdentityLinkCommandResponse:
    try:
        command = await context.confirm_identity_link(
            digisac_contact_external_id,
            payload.acessorias_company_external_id,
            reason="operator_verified",
            idempotency_key=payload.idempotency_key,
        )
    except Exception as exc:
        _ui_raise_identity_command_http_error(exc)
    response.status_code = 200 if command["replayed"] else status.HTTP_201_CREATED
    response.headers["Cache-Control"] = "no-store"
    return IdentityLinkCommandResponse.model_validate(command["result"])


@admin_ui_router.post(
    "/ui/api/contacts/{digisac_contact_external_id}/identity-links/{acessorias_company_external_id}/reject",
    include_in_schema=False,
    response_model=IdentityLinkCommandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ui_identity_link_reject(
    digisac_contact_external_id: str,
    acessorias_company_external_id: str,
    payload: UIIdentityLinkRejectRequest,
    response: Response,
    context: Annotated[AdminUIContext, Depends(require_admin_ui_context)],
) -> IdentityLinkCommandResponse:
    try:
        command = await context.reject_identity_link(
            digisac_contact_external_id,
            acessorias_company_external_id,
            reason="operator_rejected",
            idempotency_key=payload.idempotency_key,
        )
    except Exception as exc:
        _ui_raise_identity_command_http_error(exc)
    response.status_code = 200 if command["replayed"] else status.HTTP_201_CREATED
    response.headers["Cache-Control"] = "no-store"
    return IdentityLinkCommandResponse.model_validate(command["result"])


@admin_ui_router.post(
    "/ui/api/contacts/{digisac_contact_external_id}/identity-discovery",
    include_in_schema=False,
    response_model=IdentityDiscoveryResponse,
    status_code=status.HTTP_200_OK,
)
async def ui_identity_discovery(
    digisac_contact_external_id: str,
    payload: UIIdentityDiscoveryRequest,
    response: Response,
    context: Annotated[AdminUIContext, Depends(require_admin_ui_context)],
) -> IdentityDiscoveryResponse:
    try:
        command = await context.discover_identity(
            digisac_contact_external_id, idempotency_key=payload.idempotency_key
        )
    except Exception as exc:
        _ui_raise_identity_command_http_error(exc)
    response.headers["Cache-Control"] = "no-store"
    return IdentityDiscoveryResponse.model_validate(command["result"])


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
