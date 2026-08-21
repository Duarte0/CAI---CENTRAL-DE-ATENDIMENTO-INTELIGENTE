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

from src.api.admin_routes import (
    CompanyListResponse,
    IdentityContactDetail,
    IdentityLinkListResponse,
    _decode_cursor,
    _encode_cursor,
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
    button:hover { border-color: var(--accent); }
    button:focus-visible, input:focus-visible, select:focus-visible { outline: .2rem solid #f3b61f; outline-offset: .15rem; }
    button.primary { background: var(--accent); border-color: var(--accent); color: white; font-weight: 700; }
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
  <script>
    (() => {
      "use strict";

      const state = {
        queueState: "candidate",
        queueProjection: null,
        queueRequest: 0,
        selectedContact: null,
        detailRequest: 0,
        companyQuery: "",
        companyProjection: null,
        companyRequest: 0,
      };

      const element = (id) => document.getElementById(id);
      const queueStatus = element("queue-status");
      const detailStatus = element("detail-status");
      const companyStatus = element("company-status");
      const globalMessage = element("global-message");

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
            list.append(row);
          });
          content.append(list);
        });
      }

      async function selectContact(contactId) {
        state.selectedContact = contactId;
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
            list.append(row);
          });
          setStatus(companyStatus, `${visible.length} active compan${visible.length === 1 ? "y" : "ies"} found.`);
        }
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

      document.querySelectorAll("input[name=queue-state]").forEach((input) => {
        input.addEventListener("change", () => {
          state.queueState = input.value;
          state.queueProjection = null;
          loadQueue();
        });
      });
      element("refresh-queue").addEventListener("click", () => loadQueue());
      element("company-search-form").addEventListener("submit", (event) => {
        event.preventDefault();
        state.companyQuery = element("company-query").value.trim();
        state.companyProjection = null;
        loadCompanies();
      });
      setStatus(globalMessage, "Reads are session-protected and use server projections.");
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
