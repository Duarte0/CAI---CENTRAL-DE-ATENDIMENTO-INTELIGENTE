---
id: 0038
title: "Implement the authenticated read-only identity-link triage API"
type: feature
status: closed
priority: high
phase: 4
created_at: 2026-08-21
updated_at: 2026-08-21
closed_at: 2026-08-21
related_issues:
  - "0008"
  - "0015"
  - "0026"
blocked_by: []
affects:
  - src/api/routes.py
  - src/api/openapi.py
  - src/core/config.py
  - src/core/identity_resolution.py
  - src/core/db.py
  - tests/
  - README.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
  - specs/README.md
---

## Description

SPEC-0009 currently provides PostgreSQL-authoritative identity evidence,
candidate links, conservative resolution, and the domain boundary for manual
confirmation. The checkout has no authenticated administrative HTTP surface for
an operator to review those facts. The current FastAPI application exposes the
webhook, readiness/queue views, and conversation/cycle queries only; its
configuration has no `ADMIN_API_TOKEN`, and the generated OpenAPI contract
defines only the DigiSac webhook HMAC security scheme.

SPEC-0012 v1.1 defines the next approved contract as Milestone C.1, with
`ADMIN_API_TOKEN` authentication and a read-only triage surface before mutation
commands. The specification is marked ready for decomposition in the active
specification index, and its dependencies are locally implemented: SPEC-0001,
SPEC-0006, SPEC-0007, SPEC-0008, and SPEC-0009. No open or in-progress issue
covers these administrative read paths.

The current implementation boundary has `discover_identity()`,
`list_identity_evidence()`, and the PostgreSQL link/transition state in
`src/core/identity_resolution.py`, but no safe projection for an operator and
no endpoint that can distinguish an absent contact from an existing contact
with no candidate. The missing slice must expose only stable IDs, allowed
display metadata, states, counts, and timestamps; phone numbers, email values,
evidence values, tokens, conversation content, and raw provider payloads must
remain unavailable.

**Plan/spec references:** the approved Acessórias follow-on item is Milestone
C.1 — administrative identity operations, represented by **SPEC-0012 v1.1**;
cross-cutting contracts are SPEC-0001 v1.5, SPEC-0006 v1.1, SPEC-0007 v1.1,
SPEC-0008 v1.4, and SPEC-0009 v1.2. The current `IMPLEMENTATION_PLAN.md`
lists Milestones C–E as complete and Milestone F as blocked, but does not yet
enumerate C.1; this issue records that traceability gap and requires the plan
sync at completion without changing the plan in this issue-creation pass.

**Dependencies:** the closed implementation issues `0008`, `0015`, and `0026`;
Alembic identity and directory revisions through the current head; the existing
FastAPI application/OpenAPI installation; the PostgreSQL pool and identity
resolution boundary; and the disposable PostgreSQL verification runner. A
protected `ADMIN_API_TOKEN` must be provisionable before deployment, but no
production secret or network rollout is part of this issue.

**Verified gap:** targeted inspection found no `ADMIN_API_TOKEN`, no admin
router, no administrative bearer security scheme, and no read projection for
identity links. The existing identity functions are internal PostgreSQL-backed
operations, while `confirm_identity_link()` and `reject_identity_link()` do
not provide the SPEC-0012 command idempotency contract. This issue therefore
covers read-only triage and authentication only; it does not expose those
mutations prematurely.

**Expected outcome:** an authenticated operator can page through identity-link
triage, inspect one existing DigiSac contact, and search active directory
companies for a manual decision. The endpoints are PostgreSQL reads only,
never trigger provider calls or discovery, and do not alter identity links,
cycle resolutions, mappings, or Request operations.

## Scope

### In scope

- Add the protected `ADMIN_API_TOKEN` configuration boundary. The service must
  fail startup when the setting is absent, empty, or otherwise invalid under the
  approved configuration contract; tests must use an explicitly supplied
  non-production token and must not persist or print it.
- Add an internal router under `/admin/acessorias` with bearer authentication
  applied to the router:
  - `GET /admin/acessorias/identity-links` with the SPEC-0012 state filter,
    opaque cursor, and limit `1..100`;
  - `GET /admin/acessorias/contacts/{digisac_contact_external_id}/identity`;
  - `GET /admin/acessorias/companies` with optional display-only query, opaque
    cursor, and limit `1..100`.
- Define explicit request/response projections. Use DigiSac and Acessórias
  external IDs as API references; include only the allowed group flag, current
  resolution/link states, candidate counts, safe display names, evidence-type
  counts/latest timestamps, transition metadata, and current company
  availability required by SPEC-0012.
- Keep reads PostgreSQL-authoritative and transactionally consistent enough for
  each projection. Avoid Redis, provider calls, hydration, directory sync,
  discovery side effects, N+1 contact queries, and direct domain-table writes
  from HTTP handlers.
- Update the generated OpenAPI contract with an internal/admin tag, explicit
  bearer security scheme, route schemas, allowed response codes, and the
  distinction between generic `401`, invalid filters/cursors (`400`), missing
  contacts (`404`), and successful empty/no-candidate projections.
- Add focused authentication, HTTP projection, privacy, pagination, and
  disposable-PostgreSQL coverage, while preserving all existing public route
  paths, response behavior, webhook HMAC behavior, and OpenAPI expectations.

### Explicitly out of scope

- `POST` confirmation, rejection, or identity-discovery commands; command
  idempotency keys, mutation audit transitions, contact locks for mutations,
  and the SPEC-0012 follow-up slice that depends on this read surface.
- Any matching-rule change, automatic confirmation, name/CNPJ matching,
  ranking, fallback company selection, contact hydration, DigiSac/Acessórias
  provider call, directory synchronization, or backfill.
- Re-evaluating or changing historical cycle identity resolutions; preparation,
  department mapping, Request creation/recovery, and any provider POST.
- A frontend, user table, IdP, JWT, RBAC, session storage, public API, or
  deployment/VPN/secret-manager rollout.
- Returning phone/email/evidence values, raw provider data, conversation
  content, authorization headers, token fingerprints, or PII in responses,
  logs, metrics, audit rows, fixtures, or examples.
- Schema changes, data rewrites, Redis state changes, broad refactoring of the
  identity module, or unrelated cleanup. An additive index is not authorized
  by this slice unless current query evidence proves one is required and the
  change is kept migration-safe and contract-neutral.

## Implementation Plan

1. Confirm the current route registration, custom OpenAPI decoration,
   `Settings` construction, identity tables, external-ID columns, existing
   indexes, and test application fixture. Establish a test-only token injection
   path that does not weaken production startup validation or place a real
   credential in source, logs, fixtures, or snapshots.
2. Add the configuration and authentication dependency for the admin router.
   Compare the presented bearer value to the configured token in constant time;
   return the same generic `401` detail for missing, malformed, and invalid
   credentials without revealing whether a contact, company, or route exists.
   Log only `missing_admin_token` or `invalid_admin_token` plus an already-safe
   request/correlation ID. Do not log the header, token, fingerprint, or
   resource identifier as a substitute for the token.
3. Implement a read-side identity projection boundary over the existing
   PostgreSQL pool. For link listing, apply only the six allowed resolution
   states, deterministic ordering, bounded limit, and an opaque cursor that
   rejects tampering, wrong scope, malformed values, and non-progressing
   pagination. Include unresolved contacts with zero candidates when requested.
   For contact detail, return `404` only when the canonical contact is absent;
   return `200` for an existing group or candidate-free contact. For company
   search, return only present and active directory companies; treat the query
   solely as a display filter and never feed it into matching or candidate
   creation.
4. Build each response from stable external IDs and approved metadata. Evidence
   summaries may expose only the evidence categories, counts, and safe latest
   timestamps; never select or serialize normalized/raw phone or email values.
   Company display names are metadata only. Keep handler code limited to HTTP
   validation, authentication, request ID propagation, and sanitized exception
   translation; keep SQL, projection joins, and PostgreSQL authority in the
   read/domain boundary.
5. Mount the router without changing the eight existing business paths or
   their authentication behavior. Extend the generated OpenAPI document and
   schemas with the internal/admin tag and HTTP Bearer scheme. The admin
   operations must be marked protected in the document, while the existing
   webhook remains protected by its HMAC scheme and existing query routes remain
   unchanged.
6. Add deterministic offline HTTP tests and PostgreSQL-marked tests for the
   positive and negative contracts. Include query-count or equivalent evidence
   for bounded projection access, no provider/Redis/discovery calls, safe
   logging, cursor validation, group/no-candidate detail, inactive-company
   exclusion, and the absence of PII. Run the focused tests, compile/type
   checks, `git diff --check`, and the canonical verification runner; report
   unavailable external-runtime prerequisites separately.
7. After implementation and verification, update the affected OpenAPI,
   operational, architecture, specification-index, and
   `IMPLEMENTATION_PLAN.md` traceability statements with local evidence, run
   `graphify update .`, and close this issue only through the synchronized plan
   entry and one focused commit.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migration:** this read-only slice consumes the existing directory,
  contact, evidence, link, and transition tables. It must not write identity or
  cycle state and should require no migration. PostgreSQL remains the sole
  authority; Redis is not a cache of administrative decisions.
- **Compatibility:** preserve all existing route paths, response shapes,
  webhook signature ordering, worker imports, and current `manual_db` domain
  semantics. The new admin API is internal and unversioned under the exact
  `/admin/acessorias` prefix defined by SPEC-0012.
- **Security/privacy:** `ADMIN_API_TOKEN` comes only from protected environment
  or secret-manager configuration. Use constant-time comparison, generic
  unauthorized responses, no token logging, and no PII/evidence-value output.
  Do not claim secret-manager or network/VPN verification from local tests.
- **Observability:** retain only sanitized authentication categories, stable
  operation names, bounded counts, and request/correlation IDs. Projection
  failures must not log SQL parameters containing contact/company content.
- **Rollout:** the API is not deployable until an operator provisions the
  protected token and restricts the service to the authorized internal network.
  Those operational prerequisites are documented but not performed here.

## Tests

- **Authentication and HTTP contract:** add focused tests for all three routes
  proving valid bearer access, generic `401` for missing/invalid/malformed
  credentials, safe startup failure for missing configuration, allowed `400`,
  `404`, and `200` cases, and unchanged existing public routes.
- **Identity-link projections:** cover every permitted state filter, opaque
  cursor replay/tampering/scope mismatch, limit bounds, deterministic ordering,
  unresolved contacts, group contacts, candidate-free contacts, evidence
  category/count summaries, transition projection, and stable external IDs.
- **Company projection:** cover active/present inclusion, inactive/absent
  exclusion, pagination, display-only query behavior, and proof that search
  does not create evidence or candidate links.
- **Privacy and side effects:** assert phone/email values, raw evidence values,
  bearer tokens, headers, conversation content, and provider payloads are absent
  from response bodies and captured logs; assert no Redis, provider, hydration,
  sync, discovery, Request, or cycle-resolution call occurs.
- **PostgreSQL and static validation:** run the relevant focused offline and
  PostgreSQL tests through `PYTHONPATH=/app python scripts/verify.py`, plus
  `python -m compileall -q src tests alembic scripts`, strict Pyright, and
  `git diff --check`. Run `graphify update .` after implementation changes.

## Acceptance Criteria

- [x] The service rejects missing, empty, or invalid `ADMIN_API_TOKEN`
  configuration at startup, and valid test configuration is injected without
  persisting or exposing a real credential.
- [x] Every `/admin/acessorias` route requires the exact configured bearer token
  using constant-time comparison and returns the same generic `401` detail for
  missing, malformed, and invalid credentials without resource enumeration.
- [x] `GET /admin/acessorias/identity-links` implements the SPEC-0012 state,
  opaque-cursor, and `1..100` limit contract with deterministic pagination and
  includes candidate-free unresolved contacts when requested.
- [x] `GET /admin/acessorias/contacts/{digisac_contact_external_id}/identity`
  returns `404` only for a missing canonical contact and returns a safe `200`
  projection for existing group and no-candidate contacts.
- [x] `GET /admin/acessorias/companies` returns only present active companies;
  its optional query is display-only and cannot create evidence, links, ranking,
  confirmation, or a matching fallback.
- [x] Responses expose only approved stable IDs, states, safe display metadata,
  evidence categories/counts/timestamps, and availability; phone, email, raw
  evidence values, conversation content, tokens, and provider payloads are
  absent from responses, logs, metrics, and fixtures.
- [x] Projection reads use PostgreSQL as authority, do not use Redis or call
  either provider, do not trigger hydration/sync/discovery, do not mutate
  identity or cycle state, and avoid unbounded N+1 database access.
- [x] The custom OpenAPI document declares the three admin routes, explicit
  HTTP Bearer security, internal/admin tagging, schemas, and error responses;
  existing webhook HMAC and public query documentation remain unchanged.
- [x] Focused offline and disposable-PostgreSQL tests cover expected and
  negative behavior, pagination/cursor integrity, privacy, side-effect safety,
  and concurrent read consistency; compileall, strict Pyright, `git diff
  --check`, and the applicable verification runner pass.
- [x] README/ARCHITECTURE, the specification index, and
  `IMPLEMENTATION_PLAN.md` record the implemented C.1 slice, its local
  evidence, and the remaining mutation/discovery follow-up boundary.
- [x] Graphify metadata is updated with `graphify update .`, and the issue is
  closed only after the synchronized `IMPLEMENTATION_PLAN.md` update and one
  focused commit.

## References

- `specs/0012-administrative-contact-company-link-management.md` v1.1 —
  canonical administrative API contract and acceptance requirements.
- `specs/0009-digisac-acessorias-identity-resolution.md` v1.2 — identity
  states, evidence provenance, conservative matching, and immutable cycle
  resolution.
- `specs/0001-shared-data-and-analysis-contract.md` v1.5 — PostgreSQL
  authority, privacy, and Redis boundary.
- `specs/0006-api-documentation-and-openapi-contract.md` v1.1 — current HTTP
  and OpenAPI documentation boundary.
- `issues/0015_-_implement-digisac-acessorias-identity-resolution.md` and
  `issues/0026_-_prepare-identity-and-department-mapping-before-acessorias-request.md`
  — implemented identity and downstream preparation boundaries this API must
  not bypass.
- `IMPLEMENTATION_PLAN.md` — current milestone traceability and required close
  synchronization for the implemented C.1 read slice.

---

## Resolution

<!-- Filled by the agent on close. DO NOT edit manually. -->

### Implementation

- Added `ADMIN_API_TOKEN` validation at FastAPI lifespan startup, constant-time
  Bearer authentication, generic unauthorized responses, sanitized auth logs,
  and the separate `/admin/acessorias` router.
- Added the read-only PostgreSQL projection boundary in
  `src/core/identity_admin.py` with bounded joins, deterministic ordering,
  state derivation, active-company filtering, and HMAC-signed scope-bound
  cursors. No migration or durable write was required.
- Added explicit Pydantic response projections and custom OpenAPI coverage for
  all three routes, the bearer scheme, tags, parameters, schemas, and errors.

### Tests and validation

- Focused admin/OpenAPI tests: **13 passed, 1 skipped**.
- `python -m compileall -q src tests alembic scripts`: passed.
- `npx --yes pyright`: **0 errors, 0 warnings, 0 informations**.
- `APP_TIMEZONE=UTC PYTHONPATH=/app python scripts/verify.py`: compileall,
  Pyright, **236 passed, 70 skipped** offline, Alembic head
  `0020_cycle_contact_provenance`, and **70 passed, 236 deselected** in
  disposable PostgreSQL 16 all passed.
- The unqualified runner was also executed; its only failure was the
  pre-existing timezone assertion in `tests/test_department_mapping.py`,
  caused by unrelated worktree configuration changes. No production or
  persistent database was used.

### Documentation and key decisions

- Synchronized SPEC-0012, `specs/README.md`, `IMPLEMENTATION_PLAN.md`,
  `README.md`, `PRD.md`, and `ARCHITECTURE.md`; documented that issues 0039 and
  0040 own mutation/discovery commands and that deploy still requires a
  protected secret/network.
- Updated the Graphify code metadata with `graphify update .`.
