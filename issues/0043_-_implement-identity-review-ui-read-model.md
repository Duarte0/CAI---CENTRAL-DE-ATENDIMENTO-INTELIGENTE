---
id: 0043
title: "Implement the identity-review queue, detail, and company-search UI"
type: feature
status: closed
priority: high
phase: 2
created_at: 2026-08-21
updated_at: 2026-08-21
closed_at: 2026-08-21
related_issues:
  - "0041"
  - "0042"
blocked_by:
  - "0042"
affects:
  - src/api/
  - src/core/identity_admin.py
  - tests/
  - README.md
  - ARCHITECTURE.md
  - specs/README.md
  - IMPLEMENTATION_PLAN.md
---

## Description

The authenticated SPEC-0012 API already exposes sanitized identity-link,
contact-detail, and active-company projections, but the repository has no
operator-facing queue, detail panel, or company selector. Open issue 0042 owns
the login/logout routes, signed session, and in-process BFF/session boundary;
this issue consumes that boundary and delivers the read-only operational view
described by SPEC-0013 v1.2.

The outcome is a usable first viewport that lets an authenticated operator
filter and paginate the identity queue, inspect one canonical contact, and
search eligible companies. It remains a thin client of the existing
SPEC-0012 projections: matching, evidence interpretation, identity state
calculation, and PostgreSQL authority remain on the server.

## Scope

### In scope

- Build the protected page content at `GET /admin/acessorias/ui` on top of
  issue 0042's local HTML/CSS/JavaScript shell and session/BFF context.
- Add the session-authenticated, same-process read bridge required by the page
  to consume the existing SPEC-0012 read projections without placing
  `ADMIN_API_TOKEN` in browser requests. Preserve the direct Bearer-protected
  API routes for non-UI clients.
- Render a queue with explicit `candidate`, `ambiguous`, and `unresolved`
  filters, bounded pages, an opaque `next_cursor`, loading/empty/error states,
  and a stable selection for the contact detail.
- Render the selected contact's sanitized identity state, display metadata,
  link state, evidence categories/counts/timestamps, and candidate companies
  from `GET /admin/acessorias/contacts/{id}/identity`. Support candidate-free,
  group, and multiple-candidate projections without inventing a match.
- Provide display-only company search using only present and active companies
  returned by `GET /admin/acessorias/companies`; do not create a local company
  cache or repeat directory queries from browser code.
- Add basic responsive, keyboard-usable structure, visible focus, associated
  labels, and announced loading/empty/error messages for this read-only view.
- Add focused HTTP, projection-adapter, and UI contract coverage for the
  expected and negative read flows, including stale selection protection and
  sanitized output.

### Explicitly out of scope

- Login, logout, password comparison, cookie/session configuration, absolute
  expiry, and the foundational BFF/session dependency owned by issue 0042.
- Confirmation, rejection, identity discovery, modal action flows, idempotency
  key generation, command retries, or any write to `identity_admin_commands`
  or identity history.
- New matching, ranking, evidence semantics, identity states, directory
  synchronization, contact hydration, provider calls, cycle resolution,
  Request creation, PostgreSQL schema, Alembic migrations, or Redis state.
- Browser access to PostgreSQL, Redis, DigiSac, Acessórias, or any provider;
  external assets, CDN resources, analytics, persistent browser storage, and
  exposure of any administrative/provider secret.
- Full mutation-flow accessibility/security testing, visual regression across
  all states, and production rollout/acceptance; those remain follow-up slices
  after the read-only view and command flows are available.

## Implementation Plan

1. Confirm issue 0042's delivered route/session/BFF boundary, then trace the
   existing `admin_router` read handlers and `src/core/identity_admin.py`
   projections. Reuse their typed response shapes and cursor semantics; do
   not copy their SQL or recalculate state in JavaScript.
2. Add the smallest same-process session-authenticated read bridge needed by
   the page. It must invoke the existing SPEC-0012 application boundary with
   the original `state`, `query`, `limit`, and opaque `cursor` parameters,
   preserve sanitized response/error semantics, and keep direct Bearer
   authentication unchanged. A browser request must carry only the session
   cookie and never the API token.
3. Implement the local page assets and state model in the existing FastAPI
   image. Keep filter values limited to the approved queue states, treat
   cursors as opaque, reset pagination when a filter changes, and prevent an
   older detail/search response from replacing the currently selected contact
   or query result. Use the server projections as the sole source of display
   state.
4. Implement the queue, detail, and active-company search views. Show only
   external IDs, permitted display names, states, counts, categories, and safe
   timestamps. Represent no results as an explicit empty state; represent
   groups, candidate-free contacts, multiple candidates, unavailable company
   results, and missing contacts without fallback matching or fabricated
   actions.
5. Map `401`, `404`, `429`, timeout, and network failures to safe visible UI
   states. On `401`, use the session-expired behavior from issue 0042; on
   detail `404`, clear or refresh the stale selection; on transient read
   failures, retain the last safe projection and offer a bounded manual retry
   without issuing any mutation. Do not echo response bodies that could carry
   secrets or PII.
6. Add deterministic tests for session-protected reads, filter and cursor
   propagation, empty and sanitized projections, group/multiple-candidate
   rendering, active-company filtering, stale-response ordering, error
   handling, and unchanged direct Bearer behavior. Assert that these flows do
   not call providers, Redis, discovery, or command persistence.
7. Run the focused tests, the canonical offline verification, compile/type
   checks, targeted secret/PII checks, and `git diff --check`. On completion,
   synchronize only verified read-view evidence in SPEC-0013's traceability,
   the specifications index, README, ARCHITECTURE, and
   `IMPLEMENTATION_PLAN.md`; run `graphify update .` and close this issue only
   through that plan synchronization and one focused commit.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migration:** no database or Alembic migration is expected. PostgreSQL
  remains the authority through the existing read projections; the UI must not
  persist a second identity model, cache raw directory data, or write any
  identity/admin-command state.
- **Compatibility:** preserve all six SPEC-0012 route paths, their direct
  Bearer requirement and response/error contracts, cursor signing, state
  semantics, and the public API/OpenAPI surface. The UI bridge is internal to
  the same FastAPI process and is not a new public API or service.
- **Security/privacy:** the browser may receive only the fields authorized by
  SPEC-0012. Never expose `ADMIN_API_TOKEN`, `ADMIN_UI_PASSWORD`,
  `ADMIN_SESSION_SECRET`, authorization headers, phone/email values, raw
  evidence, conversation content, provider payloads, or cursor-signing
  material in HTML, JavaScript, URLs, storage, logs, metrics, cache, or error
  bodies. Keep external resources disabled.
- **Observability:** record only bounded read outcome categories and safe
  request IDs if the existing logging boundary requires diagnostics. Do not
  log query contents when they could contain PII, response bodies, cookies, or
  secrets; distinguish session expiry, empty results, missing detail, and
  transient read failure without leaking sensitive values.
- **Rollout:** ship in the existing API image/service after issue 0042 and
  SPEC-0012 are available and the three administrative credentials are
  securely provisioned. Production HTTPS, internal perimeter, and acceptance
  remain separate operational gates.

## Tests

- **HTTP/BFF:** authenticated queue, detail, and company-search reads;
  session-expiry `401`; missing contact `404`; safe handling of `429`, timeout,
  and network failures; opaque cursor and filter propagation; unchanged
  direct Bearer behavior for all SPEC-0012 routes.
- **UI contract:** candidate/ambiguous/unresolved filters, cursor reset and
  pagination, empty/loading/error states, group and candidate-free details,
  multiple candidates, active-company-only search results, keyboard labels and
  focus, and stale-response ordering.
- **Privacy/side effects:** assert that permitted projections contain no phone,
  email, raw evidence, conversation content, tokens, authorization headers, or
  provider payloads, and that read interactions do not invoke providers,
  Redis, discovery, command persistence, or directory synchronization.
- **Verification commands:** `PYTHONPATH=/app python -m pytest -q` (excluding
  only tests that require unavailable external prerequisites),
  `PYTHONPATH=/app python scripts/verify.py`,
  `PYTHONPATH=/app python -m compileall -q src tests alembic scripts`, the
  repository's strict Pyright command, targeted secret/PII scans, and
  `git diff --check`. Run the repository browser harness for the read-only
  states if issue 0042 or the repository provides one; report unavailable
  browser/PostgreSQL prerequisites separately.

## Acceptance Criteria

- [x] The authenticated page loads the queue through the issue-0042 session/BFF
  boundary, while unauthenticated or expired sessions follow the generic
  session-expired behavior and direct Bearer API clients remain compatible.
- [x] The queue exposes only the approved `candidate`, `ambiguous`, and
  `unresolved` filters, propagates bounded limits, treats `next_cursor` as
  opaque, and resets pagination when its filter changes without skipping or
  duplicating a page.
- [x] The queue and detail views render the sanitized SPEC-0012 projections;
  candidate-free contacts, groups, multiple candidates, rejected history, and
  unresolved states remain explicit and no matching or fallback state is
  invented in browser code.
- [x] Company search displays only `is_present=true` and `is_active=true`
  results returned by the existing projection, with no client-side directory
  reconstruction or inactive-company fallback.
- [x] Loading, empty, `401`, `404`, `429`, timeout, and network states are
  visible and safe; retrying a read does not create a command, transition,
  discovery, provider call, Redis operation, or duplicate durable state.
- [x] Out-of-order detail or company-search responses cannot overwrite the
  currently selected contact or current query/filter state.
- [x] The view is usable from keyboard and a narrow viewport with local assets,
  visible focus, associated labels, and non-color-only status/error cues; no
  external resource is required.
- [x] No browser-visible asset, storage entry, URL, log, metric, cache, or error
  body contains an administrative/provider secret, authorization header,
  phone/email value, raw evidence, conversation content, or provider payload.
- [x] Focused HTTP/UI/privacy tests cover positive, negative, empty, expiry,
  retry, pagination, stale-response, and side-effect cases, and the focused
  tests plus canonical offline, compile/type, secret-scan, and
  `git diff --check` validations pass (with external prerequisites reported
  separately).
- [x] SPEC-0013, its index/traceability, README, ARCHITECTURE, and
  `IMPLEMENTATION_PLAN.md` record only verified completion evidence; no
  production acceptance is claimed.
- [x] The implementation runs `graphify update .` when required by the
  repository workflow, verifies the graph metadata change, and closes this
  issue through `IMPLEMENTATION_PLAN.md` synchronization and one focused
  commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — Phase 2, item 2, Identity-review UI.
- Primary spec: `specs/0013-administrative-identity-link-review-ui.md` v1.2.
- Dependency contract: `specs/0012-administrative-contact-company-link-management.md` v1.1.
- Session/BFF prerequisite: `issues/0042_-_implement-authenticated-identity-review-ui-shell.md`.
- Implemented API/read projections: issues `0038`, `0039`, and `0040`; current
  route boundary in `src/api/admin_routes.py` and projection boundary in
  `src/core/identity_admin.py`.
- Authorization/documentation alignment: `issues/0041_-_reconcile-current-baseline-and-admin-api-traceability.md`.

---

## Resolution

### Implementation

- Replaced the issue-0042 placeholder shell with a protected, responsive
  queue/detail/company-search view using local CSS and modular inline
  JavaScript.
- Added session-cookie BFF reads at `/admin/acessorias/ui/api/identity-links`,
  `/admin/acessorias/ui/api/contacts/{id}/identity`, and
  `/admin/acessorias/ui/api/companies`. The bridge reuses the existing
  sanitized projections and signed cursor semantics, limits queue state to
  `candidate`, `ambiguous`, and `unresolved`, and leaves direct Bearer routes
  unchanged.
- Added explicit loading, empty, stale-selection, retry, session-expiry,
  missing-contact, rate-limit, timeout, and network states. Browser state keeps
  cursors in memory only, uses no external resources or storage, and guards
  detail/search responses with request generations.

### Tests and validation

- Added HTTP/BFF and UI privacy/accessibility contract tests in
  `tests/test_admin_ui.py`, including session protection, filter/limit/cursor
  propagation, sanitized detail, active-company projection, missing-contact
  handling, no-store errors, local assets, and no browser token/storage use.
- `PYTHONPATH=/app python -m pytest -q tests/test_admin_ui.py tests/test_identity_admin.py tests/test_openapi_contract.py` — 26 passed, 1 skipped.
- Embedded JavaScript syntax check, `python -m compileall -q src tests alembic scripts`, `npx --yes pyright`, `black --check src/api/admin_ui.py tests/test_admin_ui.py`, `git diff --check`, and `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py` — passed; offline suite 249 passed, 76 skipped.
- `APP_TIMEZONE=UTC PYTHONPATH=/app python scripts/verify.py` — passed compileall, strict Pyright, offline pytest, disposable PostgreSQL 16 connectivity/Alembic head `0022_identity_discovery_command`, and 76 PostgreSQL tests.
- No repository browser harness was present; the local UI contract and embedded JavaScript syntax were validated instead. Production credentials, provider access, Redis, and production acceptance remain unclaimed.

### Migrations and documentation

- No database or Alembic migration was required; the read bridge remains
  PostgreSQL-authoritative through the existing projection services and does
  not write identity state.
- Synchronized SPEC-0013 v1.4, `specs/README.md`, README, PRD,
  ARCHITECTURE, and `IMPLEMENTATION_PLAN.md` with the completed read-model
  slice and the remaining issue-0044 command slice. Graphify metadata was
  refreshed with `graphify update .`.

### Key decisions

- The browser uses only same-origin session-cookie BFF paths. The administrative
  Bearer token stays server-side, and the existing direct API routes retain
  their authentication and response contracts.
- The queue intentionally exposes only the three approved review states;
  matching, evidence interpretation, and all writes remain backend concerns.
