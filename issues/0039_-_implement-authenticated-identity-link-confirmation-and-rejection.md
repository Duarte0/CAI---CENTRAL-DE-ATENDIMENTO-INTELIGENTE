---
id: 0039
title: "Implement authenticated identity-link confirmation and rejection commands"
type: feature
status: closed
priority: high
phase: 4
created_at: 2026-08-21
updated_at: 2026-08-21
closed_at: 2026-08-21
related_issues:
  - "0015"
  - "0026"
  - "0038"
blocked_by:
  - "0038"
affects:
  - src/api/
  - src/core/config.py
  - src/core/identity_resolution.py
  - alembic/versions/
  - tests/
  - src/api/openapi.py
  - README.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
  - specs/README.md
---

## Description

SPEC-0012 v1.1 defines the authenticated administrative commands that turn a
reviewed DigiSac–Acessórias identity decision into a durable, auditable action:
explicit confirmation of one company and rejection of one existing link. The
read-only triage surface is separately tracked by open issue 0038; this issue
must extend that protected router only after its read and bearer-authentication
contract is available.

The current identity domain has PostgreSQL-backed `confirm_identity_link()` and
`reject_identity_link()` operations for the initial `manual_db` procedure, but
the external-ID wrapper has no command idempotency key, the transition key is
not the API command identity, and the existing rejection path does not expose
the `admin_api` actor/source contract. There is no HTTP command surface. These
are verified implementation gaps, not a request to change conservative
matching or historical cycle resolution.

The approved follow-on plan item is **Milestone C.1 — administrative identity
operations** from SPEC-0012 and the specification index. The current
`IMPLEMENTATION_PLAN.md` does not yet enumerate C.1 alongside Milestones C–E;
that traceability discrepancy is part of the required completion sync, not a
reason to alter the plan in this issue-creation pass.

Expected outcome: an authenticated operator can confirm or reject one explicit
contact/company pair by opaque external IDs, with durable PostgreSQL
idempotency, serialized contact-level concurrency, auditable transitions, and
sanitized responses. A command never performs discovery, changes a historical
cycle resolution, or creates/retries an Acessórias Request.

## Scope

### In scope

- Extend the authenticated `/admin/acessorias` router from issue 0038 with:
  - `POST /admin/acessorias/contacts/{digisac_contact_external_id}/identity-links/confirm`;
  - `POST /admin/acessorias/contacts/{digisac_contact_external_id}/identity-links/{acessorias_company_external_id}/reject`.
- Add explicit request/response schemas for the SPEC-0012 command bodies and
  projections. Require sanitized `reason` and opaque `idempotency_key`; derive
  actor `admin` and command source `admin_api` from authentication rather than
  request data.
- Evolve the existing identity domain boundary so handlers do not write
  identity tables directly. Confirm only the requested pair, allow promotion
  of an existing candidate or creation of a manual link when the canonical
  contact and current directory company are present/active, and preserve the
  single-confirmed-company invariant under the existing contact lock.
- Make rejection append an auditable transition while retaining all prior
  evidence and transition history. A confirmed link may be rejected only as
  the explicit administrative correction described by SPEC-0012; no other
  company may be promoted implicitly.
- Persist command idempotency in PostgreSQL. A replay with the same command
  scope, target, reason, and key returns the prior result without another
  transition; reuse of a key with a different command body/target returns
  `409`. Use an additive Alembic revision only if the current schema needs a
  durable command record or constraint; preserve all existing identity rows and
  `manual_db` history.
- Map the SPEC-0012 outcomes: `201` for a new confirmation/rejection,
  `200` for an idempotent replay, `400` for invalid body/key/reason,
  `404` for missing canonical references or a missing link on rejection,
  `409` for competing confirmation, incompatible key reuse, or unresolved
  concurrency, and `422` for an unavailable directory company.
- Extend the generated OpenAPI contract under the existing internal/admin tag
  and bearer security scheme without changing the webhook HMAC scheme or any
  existing public route.

### Explicitly out of scope

- The read-only triage/authentication foundation owned by issue 0038, except
  for consuming its router and auth dependency.
- `POST /admin/acessorias/contacts/{id}/identity-discovery`; SPEC-0012 marks
  discovery optional for the first frontend and it is a separate follow-up.
- Any matching-rule, ranking, name/CNPJ, phone/email, group-matching,
  automatic-confirmation, hydration, directory-sync, backfill, or provider
  behavior change.
- Re-evaluating or mutating `conversation_cycle_identity_resolutions`,
  department mapping, Request preparation/recovery, or any provider POST.
- A frontend, user table, IdP, JWT, RBAC, session store, public API, or
  secret-manager/network/VPN rollout.
- Returning phone numbers, email values, raw evidence, conversation content,
  provider payloads, authorization headers, token fingerprints, or PII in
  responses, logs, metrics, audit rows, fixtures, or examples.
- Redis-backed idempotency, broad identity-module refactoring, destructive
  migration/backfill, or unrelated cleanup.

## Implementation Plan

1. Reconfirm issue 0038's route/auth contract, the current Alembic head, and
   the identity tables before changing the domain. Trace the existing
   external-ID lookup, contact advisory lock, competing-confirmation check,
   transition insert, and `manual_db` callers so the new administrative source
   is additive and does not change worker/preparation behavior.
2. Define the command input/output models and error mapping at the HTTP
   boundary. Validate opaque IDs, bounded safe reason categories, and the
   required nonblank idempotency key without logging or persisting request
   secrets or free-text PII. Reuse issue 0038 authentication and pass a
   server-derived actor/request ID to the domain service.
3. Add a PostgreSQL-authoritative command-idempotency boundary keyed by
   operation, canonical contact, target company, and client key, with a
   sanitized request fingerprint/result reference sufficient to reject
   incompatible reuse. Make reservation, domain mutation, transition audit,
   and result publication one transaction; ensure concurrent same-key calls
   converge and a failed transaction leaves no command reservation or partial
   identity state.
4. Extend the confirmation operation to validate both external references and
   current company availability under the contact lock, then confirm only the
   requested pair. Preserve the existing confirmed-company conflict as `409`,
   never reject a competing link implicitly, keep `confirmed_at` server-side,
   and record `confirmed_by=admin` with an auditable `admin_api` source while
   retaining the schema's confirmed-state invariants.
5. Extend rejection to require an existing pair, preserve evidence and all
   historical transitions, append one sanitized administrative transition, and
   keep the link rejected without promoting another company. Replaying an
   already completed command returns its stored result; a new command must not
   create duplicate audit rows for the same idempotent operation.
6. Mount the two operations on the protected router and update the custom
   OpenAPI schemas, security annotations, response codes, and sanitized
   examples. Keep handlers limited to validation/authentication/request-ID
   propagation/error translation; PostgreSQL transactions, locks,
   idempotency, and audit writes remain in the identity domain boundary.
7. Add focused offline HTTP tests and disposable-PostgreSQL tests for valid and
   invalid commands, unavailable references, competing confirmations,
   confirmed-link correction, same-key replay, incompatible-key `409`,
   concurrent confirmations/replays, audit preservation, and absence of
   provider/Redis/discovery/cycle side effects. Run the focused tests,
   compile/type checks, `git diff --check`, and the canonical verification
   runner; report external-runtime prerequisites separately.
8. On completion, synchronize the C.1 traceability entry and local evidence in
   `IMPLEMENTATION_PLAN.md`, the specification index, README, ARCHITECTURE, and
   OpenAPI documentation; run `graphify update .`; close this issue only after
   that synchronized plan update and one focused commit.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migration:** PostgreSQL remains the sole authority for links,
  transitions, command idempotency, and audit history. If needed, use one
  additive Alembic revision with rollback-safe constraints/indexes; never
  delete or rewrite existing evidence, links, transitions, or cycle
  resolutions. Redis is not a command ledger.
- **Compatibility:** preserve `manual_db` callers and their current domain
  semantics, the one-confirmed-company invariant, existing identity states,
  worker/preparation imports, all current route paths, and webhook HMAC
  ordering. Administrative mutations are unversioned and exact under
  `/admin/acessorias`.
- **Security/privacy:** require the configured bearer token through issue
  0038's constant-time authentication. Derive `admin`/`admin_api` server-side;
  never trust actor fields from JSON. Log only sanitized operation/outcome
  categories and safe request IDs; do not log keys, tokens, SQL parameters,
  external PII, or evidence values.
- **Observability:** expose bounded outcome/error categories and stable IDs
  only. A replay must be distinguishable from a new mutation without exposing
  the idempotency key or sensitive request fingerprint.
- **Rollout:** deployment remains blocked until `ADMIN_API_TOKEN` is safely
  provisioned and the service is restricted to its authorized internal
  network; this issue documents but does not perform that rollout.

## Tests

- **HTTP/authentication:** exercise both routes with valid bearer access and
  generic `401` behavior inherited from issue 0038; cover body validation,
  `200`/`201`/`400`/`404`/`409`/`422`, unchanged public routes, and OpenAPI
  security/schema projections.
- **Confirmation:** cover candidate promotion, explicit creation of a
  requested link, absent contact/company, inactive or not-present company,
  competing confirmed company, stable server timestamp/actor, and no
  implicit rejection or cycle update.
- **Rejection:** cover missing pair, candidate rejection, correction of a
  confirmed link, preserved evidence and prior confirmation transition, no
  alternate-company promotion, and safe replay.
- **Idempotency/concurrency:** cover same-key same-command replay with no
  duplicate transition, same-key changed target/reason `409`, concurrent
  same-key calls converging to one result, competing confirmations remaining
  conflict-safe, and rollback without partial command/identity state.
- **Privacy/side effects:** assert that tokens, idempotency keys, raw or
  normalized phone/email, evidence values, conversation content, and provider
  payloads are absent from responses/logs/fixtures; assert no Redis, provider,
  discovery, hydration, sync, Request, mapping, or cycle-resolution call.
- **Verification:** run the focused tests plus
  `PYTHONPATH=/app python scripts/verify.py`,
  `python -m compileall -q src tests alembic scripts`, strict Pyright, and
  `git diff --check`. Run `graphify update .` after implementation changes.

## Acceptance Criteria

- [x] Both SPEC-0012 mutation routes are mounted under the authenticated
  `/admin/acessorias` router and preserve all existing route/security behavior.
- [x] Confirmation requires an existing canonical contact and present/active
  company, confirms only the requested pair, permits candidate promotion or
  explicit manual creation, and returns the specified `404`/`409`/`422`
  outcomes without implicit rejection or fallback selection.
- [x] Rejection requires an existing pair, appends an `admin_api` audit
  transition, preserves all evidence and prior transition history, allows only
  the specified confirmed-link correction, and never promotes another company.
- [x] Every new command requires a sanitized reason and opaque idempotency key;
  same-key same-command replay returns the stored result without duplicate
  audit/state changes, while incompatible reuse returns `409`.
- [x] PostgreSQL transactions and the contact lock prevent two competing
  confirmed links and prevent partial command reservations or identity updates
  after rollback; concurrent same-key replays converge.
- [x] The actor and source are server-derived (`admin` and `admin_api`),
  timestamps are server-generated, and no token, key, PII, raw evidence, or
  provider payload appears in responses, logs, metrics, audit metadata,
  fixtures, or examples.
- [x] The commands do not call providers, Redis, discovery, hydration, sync,
  cycle-resolution, department-mapping, or Request code, and do not mutate
  historical cycle resolutions.
- [x] Any schema change is additive Alembic migration work with existing
  identity/evidence/transition data preserved and the current `manual_db`
  callers still valid.
- [x] OpenAPI documents both protected commands, their schemas, security, and
  response codes without changing the webhook HMAC or public query contract.
- [x] Focused offline and disposable-PostgreSQL tests cover expected,
  negative, privacy, idempotency, concurrency, rollback, and side-effect
  behavior; compileall, strict Pyright, `git diff --check`, and the applicable
  verification runner pass.
- [x] README/ARCHITECTURE, the specification index, and
  `IMPLEMENTATION_PLAN.md` record the implemented C.1 command slice, local
  evidence, and the separate optional discovery follow-up.
- [x] Graphify metadata is updated with `graphify update .`, and the issue is
  closed only after the synchronized plan update and one focused commit.

## References

- `specs/0012-administrative-contact-company-link-management.md` v1.1 —
  canonical authenticated command contract and acceptance requirements.
- `specs/0009-digisac-acessorias-identity-resolution.md` v1.2 — conservative
  matching, link states, transition audit, and immutable cycle resolution.
- `specs/0001-shared-data-and-analysis-contract.md` v1.5 — PostgreSQL
  authority, privacy, and Redis boundary.
- `specs/0006-api-documentation-and-openapi-contract.md` v1.1 — current HTTP
  and OpenAPI compatibility boundary.
- `issues/0015_-_implement-digisac-acessorias-identity-resolution.md` and
  `issues/0026_-_prepare-identity-and-department-mapping-before-acessorias-request.md`
  — implemented identity/preparation boundaries this command must not bypass.
- `issues/0038_-_implement-authenticated-read-only-identity-link-triage-api.md`
  — prerequisite authenticated read/router slice.
- `IMPLEMENTATION_PLAN.md` — Milestone C.1 traceability gap to synchronize on
  completion; no plan rewrite is part of issue creation.

---

## Resolution

<!-- Filled by the agent on close. DO NOT edit manually. -->

### Implementation

- Added additive Alembic migration `0021_identity_admin_commands` and updated
  schema verification to accept the new head. The ledger stores only a hashed
  opaque command key, request fingerprint, and sanitized result JSON; failed
  transactions roll back the reservation.
- Added contact-locked, PostgreSQL-authoritative administrative confirmation and
  rejection domain commands. Confirmation supports candidate promotion or
  explicit manual creation, preserves competing-confirmation conflicts, and
  records `confirmed_by=admin`/`admin_api`; rejection preserves prior evidence
  and transitions, including confirmed-link correction.
- Mounted the two authenticated `/admin/acessorias` POST routes with explicit
  request/response schemas, sanitized error mapping, generic 401 behavior, and
  custom OpenAPI security, schemas, and status responses. No provider, Redis,
  discovery, cycle, mapping, or Request path was added.

### Tests and validation

- Focused offline admin/OpenAPI tests: **14 passed, 1 deselected**.
- `python -m compileall -q src tests alembic scripts`: passed.
- `npx --yes pyright`: **0 errors, 0 warnings, 0 informations**.
- `APP_TIMEZONE=UTC PYTHONPATH=/app python scripts/verify.py`: compileall,
  Pyright, **237 passed, 74 skipped** offline, Alembic head
  `0021_identity_admin_commands`, and **74 passed, 237 deselected** in
  disposable PostgreSQL 16 all passed.
- No production database, provider credential, Redis runtime, deployment, or
  secret-manager/network rollout was used.

### Documentation and key decisions

- Synchronized SPEC-0012, `specs/README.md`, `IMPLEMENTATION_PLAN.md`,
  `README.md`, `ARCHITECTURE.md`, and the generated OpenAPI composition. The
  optional discovery follow-up remains issue 0040.
- Updated Graphify metadata with `graphify update .`.
