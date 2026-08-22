---
id: 0044
title: "Implement identity-review confirmation, rejection, and discovery actions"
type: feature
status: closed
priority: high
phase: 2
created_at: 2026-08-21
updated_at: 2026-08-22
closed_at: 2026-08-22
related_issues:
  - "0041"
  - "0042"
  - "0043"
blocked_by:
  - "0042"
  - "0043"
affects:
  - src/api/
  - src/core/identity_resolution.py
  - tests/
  - README.md
  - ARCHITECTURE.md
  - specs/README.md
  - IMPLEMENTATION_PLAN.md
---

## Description

The authenticated identity-review UI will have the session/BFF foundation from
issue 0042 and the read-only queue, detail, and company-search projections from
issue 0043, but it will still have no safe way for an operator to apply a
reviewed decision. SPEC-0013 v1.2 authorizes three thin-client actions over the
existing SPEC-0012 command surface: confirm one selected company, reject one
explicit link, and rerun deterministic discovery for one contact.

Implement these actions as same-process session-authenticated UI/BFF calls to
the existing durable command boundary. The result must preserve the backend's
actor, validation, locking, idempotency, and audit semantics; browser code must
only coordinate explicit operator intent and render sanitized results.

## Scope

### In scope

- Extend the issue-0042 session/BFF boundary used by the issue-0043 page with
  authenticated UI access to the existing SPEC-0012 confirmation, rejection,
  and discovery commands.
- Add an explicit confirmation step for each mutation. Confirmation must target
  the selected present/active company, send the fixed reason
  `operator_verified`, and generate one opaque idempotency key for that action.
- Add explicit rejection for one selected contact/company link, using the
  fixed reason `operator_rejected` and one opaque idempotency key. Rejection
  must not select or promote another company.
- Add an explicit deterministic-discovery action for the selected canonical
  contact, sending only its external ID and an opaque idempotency key.
- Disable the relevant action while its request is in flight; preserve the same
  idempotency key across a retry whose outcome is uncertain, and refresh the
  queue/detail projections after a successful or replayed command.
- Render safe success, replay, conflict, unavailable-company, missing-contact,
  expired-session, rate-limit, timeout, and network states without echoing
  response bodies or sensitive fields.
- Add focused HTTP/BFF, command-state, idempotency, concurrency, privacy, and
  accessibility/browser coverage for the three action flows while preserving
  direct Bearer API behavior for non-UI clients.

### Explicitly out of scope

- Login, logout, password comparison, signed-session configuration, and the
  foundational BFF/session dependency owned by issue 0042.
- The queue, contact detail, company search, read projections, pagination, and
  filtering owned by issue 0043, except for refreshing those views after a
  command result.
- New matching, ranking, evidence interpretation, identity states, directory
  synchronization, contact hydration, provider calls, cycle resolution,
  Request creation/recovery, PostgreSQL schema changes, Alembic migrations, or
  Redis state.
- Free-text reasons, browser-side actor/timestamp fields, automatic retries,
  automatic company selection, fallback matching, or mutation of historical
  cycle resolutions.
- User accounts, RBAC, IdP, JWT, an additional CSRF token, external assets,
  persistent browser storage, production secret provisioning, and production
  acceptance.
- A new public API or service. The existing six SPEC-0012 routes and their
  direct Bearer authentication remain the backend contract for API clients.

## Implementation Plan

1. Verify that issues 0042 and 0043 expose the protected page and same-process
   session/BFF context, then reuse the existing SPEC-0012 command schemas and
   application functions from issues 0038–0040. Keep `ADMIN_API_TOKEN` on the
   server, preserve the `admin_api` actor/source derived by the command layer,
   and do not duplicate identity SQL or command-ledger logic in the UI bridge.
2. Wire the three UI actions to the existing command contracts. Confirmation
   must use the explicitly selected active/present company and
   `operator_verified`; rejection must use the explicitly selected link and
   `operator_rejected`; discovery must target only the selected contact. Reject
   missing or stale selections before sending a command, and never infer a
   target from names, evidence, search text, phone, email, or a different link.
3. Generate an opaque key once per user action and retain it in transient action
   state until the command has a definitive result. Treat `201` as a new
   transition and `200` as an idempotent replay of the same result. Do not
   generate a second key or issue an automatic second POST after timeout,
   network failure, `429`, or an ambiguous response; a user-requested retry
   reuses the original key and target. Prevent duplicate clicks and stale
   responses from applying a second visible transition.
4. Implement the explicit review/confirmation interaction and post-command
   refresh. On success or replay, show the sanitized command projection and
   reload the queue and selected contact. Map `401` to issue-0042's
   session-expired flow, `404` to stale-reference refresh, `409` to a conflict
   requiring detail reload, `422` to removing the unavailable company from the
   current selection, and transient failures to a safe retry state that retains
   the key. Discovery results must render only the approved external-ID,
   state, link, count, and timestamp projections.
5. Test the action bridge and browser state against repeated and concurrent
   requests. Prove that retries with the same key converge to one ledger result,
   incompatible key reuse is surfaced as a conflict, confirmation concurrency
   remains governed by the existing contact lock, rejection preserves history,
   and discovery remains local/deterministic. Prove that no action invokes a
   provider, Redis, Request flow, or historical-cycle mutation.
6. Run the focused tests, the canonical offline verification, compile/type
   checks, targeted secret/PII scans, browser coverage if available, and
   `git diff --check`. On completion, synchronize only verified action-flow
   evidence in SPEC-0013, the specifications index, README, ARCHITECTURE, and
   `IMPLEMENTATION_PLAN.md`; run `graphify update .` when required by the
   repository workflow and close this issue only through that plan sync and one
   focused commit.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migration:** no migration is expected. PostgreSQL remains the durable
  authority through the command ledger, identity links, and transition history
  already implemented by SPEC-0012. The UI must not create a second command or
  identity store.
- **Command invariants:** confirmation remains explicit and exclusive per
  contact; rejection appends history and never promotes another company;
  discovery uses existing deterministic local facts and preserves confirmed
  precedence. Historical cycle resolutions and Request eligibility are
  unchanged.
- **Compatibility:** preserve all six SPEC-0012 route paths, request/response
  status semantics (`201` new command, `200` replay, `400`, `404`, `409`, and
  `422` as applicable), direct Bearer authentication, and the OpenAPI surface.
  The UI bridge is internal to the same FastAPI process.
- **Security/privacy:** the browser may receive only the sanitized projections
  authorized by SPEC-0012. Never expose `ADMIN_API_TOKEN`,
  `ADMIN_UI_PASSWORD`, `ADMIN_SESSION_SECRET`, authorization headers, phone or
  email values, raw evidence, conversation content, provider payloads, or
  command-ledger details in assets, storage, URLs, logs, metrics, cache, or
  error bodies. SameSite/HttpOnly session protection remains the issue-0042
  boundary.
- **Observability:** record only bounded action outcome categories and safe
  request IDs if the existing boundary requires diagnostics. Distinguish
  success, replay, conflict, stale reference, unavailable company, session
  expiry, and transient failure without logging keys, cookies, request bodies,
  response bodies, or secrets.
- **Rollout:** ship in the existing API image/service after issues 0042 and
  0043 and SPEC-0012 are available, with the three administrative credentials
  provisioned securely. HTTPS, internal perimeter, and production acceptance
  remain separate operational gates.

## Tests

- **HTTP/BFF:** authenticated confirm, reject, and discovery calls; fixed
  reasons; opaque key validation; `201` versus `200` replay; `400`, `401`,
  `404`, `409`, `422`, `429`, timeout, and network handling; unchanged direct
  Bearer behavior for all SPEC-0012 routes.
- **Command/idempotency:** same-key replay without a duplicate command or
  transition; incompatible key reuse; concurrent confirmation serialization;
  rejection history preservation; and discovery idempotency.
- **UI/browser:** explicit confirmation step, disabled in-flight controls,
  target validation, fixed reason categories, retry with the same key, success
  versus replay rendering, stale-reference refresh, session-expired flow,
  keyboard operation, visible focus, narrow viewport, and non-color-only
  status/error cues.
- **Privacy/side effects:** assert no token, credential, authorization header,
  phone/email, raw evidence, conversation content, provider payload, or
  idempotency key is exposed outside the intended request; assert no provider,
  Redis, Request, discovery side effect beyond the explicit discovery command,
  or historical-cycle mutation occurs.
- **Verification commands:** `PYTHONPATH=/app python -m pytest -q`,
  `PYTHONPATH=/app python scripts/verify.py`,
  `PYTHONPATH=/app python -m compileall -q src tests alembic scripts`, the
  repository's strict Pyright command, targeted secret/PII scans, the available
  browser harness, and `git diff --check`. Report unavailable external
  prerequisites separately.

## Acceptance Criteria

- [x] An authenticated operator must explicitly confirm a selected present and
  active company; the UI sends only the approved target, `reason=operator_verified`,
  and one opaque idempotency key.
- [x] An authenticated operator must explicitly reject one selected link; the
  UI sends `reason=operator_rejected` and never promotes or selects another
  company as a side effect.
- [x] An authenticated operator can explicitly request deterministic discovery
  for one selected contact using only its external ID and one opaque
  idempotency key.
- [x] Confirmation, rejection, and discovery use the existing same-process
  session/BFF boundary; `ADMIN_API_TOKEN` and authorization headers never reach
  browser code, assets, URLs, storage, logs, metrics, or response bodies, and
  idempotency keys exist only in transient action state and the intended
  command request.
- [x] New commands return and render their `201` result, idempotent replays
  return and render the same `200` result, and neither path creates duplicate
  command-ledger rows or identity transitions.
- [x] A retry after timeout, network failure, `429`, or an otherwise ambiguous
  result reuses the original key and target only when the operator requests it;
  no automatic second POST or regenerated key is issued.
- [x] Concurrent confirmation attempts preserve the existing single-confirmed
  company invariant; rejection preserves prior evidence and transition history;
  discovery does not alter confirmed links or historical cycle resolutions.
- [x] `401`, `404`, `409`, `422`, timeout, and network outcomes produce safe
  visible states, do not echo response bodies, and refresh or clear stale UI
  projections as required.
- [x] Action flows do not call DigiSac/Acessórias providers, Redis, Request
  creation/recovery, or unrelated identity operations; discovery uses only the
  existing local deterministic command boundary.
- [x] Focused HTTP, idempotency/concurrency, privacy, accessibility, and browser
  tests cover positive, negative, retry, replay, stale-reference, and failure
  paths, and the focused tests plus canonical offline, compile/type,
  secret-scan, browser, and `git diff --check` validations pass.
  Browser-specific validation is N/A for this checkout because no repository
  browser harness or browser executable is available; the issue's
  implementation plan permits reporting that prerequisite separately, and the
  local script/HTTP/accessibility contract checks passed.
- [x] SPEC-0013, its index/traceability, README, ARCHITECTURE, and
  `IMPLEMENTATION_PLAN.md` record only verified action-flow completion evidence;
  no production acceptance is claimed.
- [x] The implementation runs `graphify update .` when required, verifies any
  graph metadata change, and closes this issue through `IMPLEMENTATION_PLAN.md`
  synchronization and one focused commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — Phase 2, item 2, Identity-review UI.
- Primary spec: `specs/0013-administrative-identity-link-review-ui.md` v1.5,
  especially the action flow, API contract, error states, and decomposition.
- Command contract: `specs/0012-administrative-contact-company-link-management.md`
  v1.1, especially confirmation, rejection, discovery, idempotency, and
  concurrency rules.
- Session/BFF prerequisite: `issues/0042_-_implement-authenticated-identity-review-ui-shell.md`.
- Read-model prerequisite: `issues/0043_-_implement-identity-review-ui-read-model.md`.
- Implemented command boundaries: issues `0038`, `0039`, and `0040`.
- Authorization/documentation alignment: `issues/0041_-_reconcile-current-baseline-and-admin-api-traceability.md`.

---

## Resolution

### Implementation

- Added session-authenticated BFF POST routes for confirmation, rejection, and
  deterministic discovery in `src/api/admin_ui.py`. The bridge reuses the
  existing SPEC-0012 command functions, enforces `operator_verified` and
  `operator_rejected` server-side, preserves `201` versus `200` replay status,
  validates opaque request keys, and maps command failures to sanitized
  no-store responses.
- Extended the local HTML/CSS/JavaScript review surface with active-company and
  link selection, explicit confirmation dialog, disabled in-flight controls,
  transient UUID keys, same-key manual retry for uncertain results, safe error
  states, and queue/detail refresh after command success or replay. No browser
  storage, external assets, provider, Redis, Request, migration, or historical
  cycle mutation was introduced.
- Updated the shared validation handler so malformed UI command bodies retain
  the existing sanitized `400` contract and `Cache-Control: no-store` policy.

### Tests and validation

- Added focused UI/BFF tests for session protection, fixed reasons, `201`/`200`
  replay, discovery, safe command conflicts, malformed bodies, no-store
  responses, explicit local action markup, and secret/storage boundaries.
- Passed `PYTHONPATH=/app python -m pytest -q tests/test_admin_ui.py tests/test_identity_admin.py`
  (**25 passed, 1 skipped**), `PYTHONPATH=/app python -m pytest -q
  --ignore=tests/test_webhook_local.py` (**253 passed, 76 skipped**), embedded
  JavaScript syntax validation, `python -m compileall -q src tests alembic
  scripts`, `npx --yes pyright`, focused Black checks, targeted secret scan,
  and `git diff --check`.
- Passed `APP_TIMEZONE=UTC PYTHONPATH=/app python scripts/verify.py`, including
  compileall, Pyright, offline pytest, disposable PostgreSQL 16, Alembic head
  `0022_identity_discovery_command`, and **76 PostgreSQL tests**.
- Browser-rendered QA is N/A for this environment: no repository browser
  harness or browser executable was available, and no new browser dependency
  was installed. The embedded script, HTTP contract, accessibility markup, and
  narrow-layout CSS were validated locally; no production acceptance is claimed.

### Migrations and documentation

- No migration was required; existing PostgreSQL command-ledger, identity-link
  locking, audit, and idempotency semantics remain authoritative.
- Synchronized SPEC-0013 v1.5, `specs/README.md`, `README.md`,
  `ARCHITECTURE.md`, and `IMPLEMENTATION_PLAN.md` with the completed local
  action slice and the browser-evidence boundary. Graphify was refreshed after
  the final source and documentation changes.

### Key decisions

- The UI BFF accepts only opaque target/key inputs and supplies the approved
  reasons itself, so free-text reasons and browser actor fields cannot enter the
  action contract.
- Uncertain transport results retain one transient action object and require a
  user-requested retry with the same key; conflicts and stale references force
  a detail refresh rather than a second automatic command.
