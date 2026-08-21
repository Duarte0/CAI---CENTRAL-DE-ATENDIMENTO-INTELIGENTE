---
id: 0042
title: "Implement the authenticated identity-review UI shell and BFF session boundary"
type: feature
status: closed
priority: high
phase: 2
created_at: 2026-08-21
updated_at: 2026-08-21
closed_at: 2026-08-21
related_issues:
  - "0041"
blocked_by:
  - "0038"
  - "0039"
  - "0040"
affects:
  - src/api/
  - src/core/config.py
  - tests/
  - specs/
  - README.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
---

## Description

The current checkout has the complete authenticated SPEC-0012 API, but it has
no administrative web route, operator-password bootstrap, signed browser
session, or in-process BFF boundary. SPEC-0013 v1.2 is approved for issue
decomposition and explicitly makes this shell/session boundary the first slice;
the existing Bearer-protected API remains the backend authority.

Implement the smallest usable foundation: a FastAPI-served administrative UI
shell with login/logout and a fixed-lifetime signed `HttpOnly` session, plus a
server-only authentication/BFF context that later UI slices can use to call
SPEC-0012 services without sending `ADMIN_API_TOKEN` to the browser. The shell
may be visually minimal and need not render the triage queue yet.

## Scope

### In scope

- Add the `ADMIN_UI_PASSWORD` and `ADMIN_SESSION_SECRET` configuration inputs
  with nonblank validation and fail-closed behavior when the UI is not safely
  configured.
- Mount `GET /admin/acessorias/ui`, `POST /admin/acessorias/login`, and
  `POST /admin/acessorias/logout` in the existing FastAPI process. Keep the UI
  route out of the generated API contract when it is an HTML asset route.
- Serve a local, same-image shell and login form with no CDN, external font,
  analytics, remote script, or required frontend bundler.
- Establish the in-process BFF/session dependency used by subsequent UI data
  slices. It must call existing SPEC-0012 application services/routers in
  process and preserve their sanitized projections; it must not proxy through
  a second HTTP service or access PostgreSQL/Redis from browser code.
- Use a signed cookie session with `HttpOnly`, `SameSite=Strict`, `Secure` in
  production, a 60-minute fixed lifetime, and no sliding renewal. Store only a
  non-sensitive authenticated marker and expiry information in the cookie.
- Compare the submitted operator password securely with
  `ADMIN_UI_PASSWORD`; return a generic invalid-credential response and
  redirect a successful login to `/admin/acessorias/ui`.
- Make logout safe to repeat, clear the session, and prevent UI/login/logout
  responses from being cached.
- Preserve the existing six SPEC-0012 Bearer routes and their generic
  authentication behavior for direct API clients.

### Explicitly out of scope

- The triage queue, pagination/filter UI, contact detail, company search, or
  visual design beyond the protected shell/login states.
- Confirmation, rejection, or identity-discovery actions, including their
  request-body construction and idempotency-key generation.
- New matching, identity, directory, cycle, Request, PostgreSQL, or Redis
  behavior; this slice must not write `identity_admin_commands` or alter
  historical cycle resolutions.
- Users, registration, password recovery/rotation, RBAC, IdP, JWT, an extra
  CSRF token, a network/VPN layer, or a new service/container.
- Production secret provisioning or production acceptance; document these as
  rollout prerequisites rather than embedding credentials or values.

## Implementation Plan

1. Reconfirm the current app composition in `src/api/routes.py`, the direct
   Bearer dependency in `src/api/admin_routes.py`, and the settings/lifespan
   behavior in `src/core/config.py`. Preserve direct API authentication and
   avoid duplicating SPEC-0012 projections.
2. Add the two UI settings and validation, then choose one approved signed
   session mechanism already compatible with the FastAPI/Starlette stack. Set
   the cookie attributes from the contract, enforce the 60-minute absolute
   expiry on every session check, and never refresh the expiry on ordinary
   reads. Do not place the API token, password, or session secret in session
   data.
3. Add the login, logout, and protected shell routes. Invalid credentials,
   missing configuration, expired sessions, and malformed cookies must fail
   closed with generic safe responses; valid login must not disclose whether a
   username or operator exists because there is no username dimension.
4. Add a server-side UI/BFF authentication context that future data routes can
   use to invoke the existing SPEC-0012 service functions in process. Keep
   authorization separation explicit: browser requests carry only the signed
   session cookie, while `ADMIN_API_TOKEN` remains server configuration and
   direct Bearer API calls remain supported.
5. Add focused HTTP/configuration tests for successful and failed bootstrap,
   cookie flags, absolute expiry, logout/repeated logout, no-store headers,
   token/credential non-disclosure, direct API compatibility, and concurrent
   independent sessions. Ensure retries of login/logout cannot create durable
   identity transitions or duplicate command-ledger rows.
6. Run the focused tests, the canonical offline suite, compile/type checks,
   secret/PII scans, and `git diff --check`. Update the SPEC-0013 status and
   traceability, the specifications index, the README/architecture surface,
   and `IMPLEMENTATION_PLAN.md` only with verified implementation evidence.

## Data, migration, compatibility, security, observability, and rollout

- No database or Alembic migration is expected. Authentication state is a
  signed cookie and must not become a second durable identity authority.
- Existing `/admin/acessorias` Bearer clients must continue to receive the
  current responses and error semantics. The UI route must not expose those
  routes as unauthenticated browser endpoints or weaken their direct token
  requirement.
- The browser, HTML, JavaScript, CSS, logs, metrics, cache, and error bodies
  must not contain `ADMIN_API_TOKEN`, `ADMIN_UI_PASSWORD`,
  `ADMIN_SESSION_SECRET`, phone/email values, raw evidence, webhook content, or
  provider credentials. Use only safe request IDs/categories in diagnostics.
- Cookie/session failures and missing configuration must be observable through
  bounded reason categories without recording submitted passwords, cookie
  contents, or secrets. Do not add a new provider or external network call.
- Ship in the existing API image/service after the three credentials are
  securely provisioned. Production must use HTTPS so `Secure` cookies are
  effective; production deployment/acceptance remains a separate gate.

## Tests

- **Unit/configuration:** settings validation, secure password comparison
  boundary, session expiry helper, and safe error/logging behavior.
- **HTTP:** login success/failure, generic invalid response, redirect,
  protected shell, logout/repeated logout, expired/malformed cookie, and
  `Cache-Control: no-store` behavior.
- **Security/compatibility:** cookie attributes by environment, absence of all
  three UI/API secrets from responses/logs/assets, no external resource URLs,
  unchanged direct Bearer behavior for all six SPEC-0012 routes, and concurrent
  sessions that do not share mutable authentication state.
- **Commands:** `PYTHONPATH=/app python -m pytest -q`;
  `PYTHONPATH=/app python -m compileall -q src tests`; the repository's strict
  type-check command; targeted secret/PII checks; and `git diff --check`.
- **Browser foundation:** exercise login, protected-shell, logout, expired
  session, and narrow viewport states with the repository's available browser
  harness if one is present; the full triage/command visual QA belongs to the
  follow-up UI issues.

## Acceptance Criteria

- [x] `POST /admin/acessorias/login` securely compares the submitted password
  with `ADMIN_UI_PASSWORD`, returns a generic failure for invalid credentials
  or unavailable UI configuration, and redirects a valid login to the shell.
- [x] A valid login creates a signed `HttpOnly` session with
  `SameSite=Strict`, `Secure` in production, and an absolute 60-minute expiry;
  ordinary reads do not slide or renew that expiry.
- [x] `GET /admin/acessorias/ui` is inaccessible without a valid session and
  serves only the local protected shell; it does not expose an API token or
  call an external provider.
- [x] `POST /admin/acessorias/logout` clears the session, is safe to repeat,
  and leaves no durable identity/admin-command state behind.
- [x] UI, login, and logout responses use `Cache-Control: no-store`, and
  malformed, expired, or missing cookies fail closed without leaking session
  contents or distinguishing sensitive configuration state.
- [x] The BFF context is in-process, invokes only the existing SPEC-0012
  application boundary, and never places `ADMIN_API_TOKEN` in HTML, JavaScript,
  cookies, URLs, browser storage, logs, metrics, or response bodies.
- [x] Existing direct Bearer authentication and response/error behavior for all
  six SPEC-0012 routes remain compatible, including rejection of missing or
  invalid Bearer credentials.
- [x] Repeated or concurrent login/logout requests do not create identity
  transitions, command-ledger rows, or shared mutable-session cross-talk; a
  session retry remains local to session state.
- [x] Tests cover expected, negative, expiry, security, compatibility, and
  concurrency behavior, and the focused tests plus canonical offline,
  compile/type, secret-scan, and `git diff --check` validations pass.
- [x] SPEC-0013, its index/traceability, README/architecture documentation,
  and `IMPLEMENTATION_PLAN.md` record only verified completion evidence; no
  production acceptance is claimed.
- [x] The implementation runs `graphify update .` when required by the
  repository workflow, verifies the graph metadata change, and closes this
  issue through `IMPLEMENTATION_PLAN.md` synchronization and one focused
  commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — Phase 2, item 2, Identity-review UI.
- Primary spec: `specs/0013-administrative-identity-link-review-ui.md` v1.2.
- Dependency contract: `specs/0012-administrative-contact-company-link-management.md` v1.1.
- Related authorization/documentation issue: `issues/0041_-_reconcile-current-baseline-and-admin-api-traceability.md`.
- Implemented API slices: issues `0038`, `0039`, and `0040`.

---

## Resolution

### Implementation

- Added validated `ADMIN_UI_PASSWORD` and `ADMIN_SESSION_SECRET` settings and
  the protected `/admin/acessorias/ui`, `/login`, and `/logout` HTML routes.
- Added a local responsive login/protected shell, repeatable logout, generic
  fail-closed responses, no-store cache policy, and a fixed 60-minute signed
  `itsdangerous` session cookie with SHA-256 signing, `HttpOnly`,
  `SameSite=Strict`, and production-only `Secure`.
- Added `AdminUIContext`, an in-process server-side bridge to the existing
  SPEC-0012 projection and command services. Browser responses contain no
  administrative token or provider data, and the UI routes are excluded from
  OpenAPI.
- Added the pinned `itsdangerous==2.1.2` dependency. No database, Alembic,
  Redis, provider, identity, or command-ledger behavior changed.

### Tests and validation

- Added focused HTTP/configuration/security/concurrency tests in
  `tests/test_admin_ui.py`, including malformed/expired session failure,
  cookie attributes, no-store headers, repeated logout, concurrent sessions,
  no external assets, and unchanged OpenAPI/direct-admin surface.
- `PYTHONPATH=/app python -m pytest -q tests/test_admin_ui.py tests/test_identity_admin.py tests/test_openapi_contract.py` — 23 passed, 1 skipped.
- `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py` — 246 passed, 76 skipped.
- `python -m compileall -q src tests alembic scripts`, `npx --yes pyright`,
  `black --check src/api/admin_ui.py tests/test_admin_ui.py`, and
  `git diff --check` — passed.
- `APP_TIMEZONE=UTC PYTHONPATH=/app python scripts/verify.py` — all stages
  passed, including Alembic `0022_identity_discovery_command` and 76
  disposable PostgreSQL tests. The same runner without the established UTC
  override retained the pre-existing timezone assertion in
  `tests/test_department_mapping.py`; it did not involve this issue.
- `graphify update .` rebuilt the graph; the resulting metadata contains the
  new `admin_ui.py`, `test_admin_ui.py`, and issue-0042 nodes.

### Migrations and documentation

- No migration was required. Updated SPEC-0013 to v1.3 with the shell/session
  evidence, synchronized `specs/README.md`, README configuration/API notes,
  PRD, ARCHITECTURE, and `IMPLEMENTATION_PLAN.md` with the partial C.2 status.
- Production secret provisioning, HTTPS deployment, and the remaining read and
  action UI slices remain explicit follow-up gates; no production acceptance is
  claimed.
