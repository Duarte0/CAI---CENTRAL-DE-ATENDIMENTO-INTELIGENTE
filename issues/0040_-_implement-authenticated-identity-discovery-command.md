---
id: 0040
title: "Implement the authenticated identity-discovery command"
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
  - "0039"
blocked_by:
  - "0038"
  - "0039"
affects:
  - src/api/
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

SPEC-0012 v1.1 defines an optional authenticated administrative command to
rerun deterministic identity discovery for one canonical DigiSac contact. The
read-only triage and bearer-authentication foundation is owned by open issue
0038, and the confirmation/rejection commands plus the reusable durable command
idempotency boundary are owned by open issue 0039. This issue is the separate
discovery follow-up explicitly excluded from both of those outcomes.

The current implementation has the PostgreSQL-backed `discover_identity()`
domain operation and deterministic unit/PostgreSQL coverage, but no HTTP
endpoint under `/admin/acessorias`, no external-ID command wrapper, and no
idempotency-key contract for an administrative discovery request. The existing
operation is transactionally serialized by the contact lock and is replay-safe
for evidence/link upserts, but it returns local database identifiers and does
not reserve or replay an administrative command result. The current route and
OpenAPI surfaces also have no administrative discovery operation.

The approved plan/spec reference is **Milestone C.1 — administrative identity
operations**, represented by **SPEC-0012 v1.1**. The active specification index
marks SPEC-0012 ready for decomposition; its dependencies SPEC-0001,
SPEC-0006, SPEC-0007, SPEC-0008, and SPEC-0009 are implemented locally. The
current `IMPLEMENTATION_PLAN.md` does not yet enumerate C.1; synchronized plan
traceability is a completion requirement and is not part of this issue-creation
pass.

Expected outcome: an authenticated operator can request discovery for one
external DigiSac contact, receive a sanitized external-ID result, and safely
replay the same command. Discovery uses only the existing PostgreSQL directory
and identity facts, preserves conservative matching and confirmed-link
precedence, and never calls a provider, changes historical cycle resolution, or
creates an Acessórias Request.

## Scope

### In scope

- Extend the authenticated `/admin/acessorias` router delivered by issue 0038
  with `POST /admin/acessorias/contacts/{digisac_contact_external_id}/identity-discovery`.
- Add explicit request and response projections for SPEC-0012. Require the
  opaque `idempotency_key`, accept the canonical DigiSac external contact ID,
  and expose only the approved discovery state, stable external company/link
  references, bounded counts, and safe timestamps or metadata defined by the
  specification.
- Add an external-ID/domain boundary that resolves exactly one existing
  canonical contact and invokes the existing deterministic discovery rules over
  the local PostgreSQL directory. Preserve group exclusion, exact evidence and
  the approved Brazilian mobile variant, many-to-many candidates, confirmed
  precedence, and no automatic confirmation.
- Make the command idempotent through the PostgreSQL command ledger provided by
  issue 0039. A same-scope, same-key replay returns the stored result without
  duplicate evidence/link transitions or a second discovery execution; reuse of
  a key for a different command or target follows the SPEC-0012 `409` contract.
  Do not create a parallel idempotency store.
- Keep command reservation, contact serialization, discovery persistence, and
  result publication transactionally consistent. A failed transaction must not
  leave a command reservation, partial evidence, a candidate link, or a new
  administrative transition behind.
- Extend the generated OpenAPI contract with the protected discovery operation,
  request/response schemas, internal/admin tagging, bearer security inherited
  from issue 0038, and the SPEC-0012 success and conflict responses.

### Explicitly out of scope

- The router, `ADMIN_API_TOKEN` startup validation, bearer comparison, and
  read-only triage projections owned by issue 0038, except for consuming their
  authenticated dependency and route registration boundary.
- Confirmation or rejection commands, their request bodies, audit semantics,
  and mutation behavior owned by issue 0039, except for reusing its command
  idempotency mechanism without changing its contract.
- Any change to matching rules, ranking, name/CNPJ matching, fuzzy matching,
  group matching, automatic confirmation, directory synchronization, provider
  calls, contact hydration, full backfill, or Acessórias/DigiSac credentials.
- Re-evaluating or mutating `conversation_cycle_identity_resolutions`,
  department mappings, Request preparation/recovery, or any provider POST.
- A frontend, public API, user table, IdP, JWT, RBAC, session store, or
  secret-manager/network/VPN rollout.
- Returning phone numbers, email values, raw or normalized evidence, raw
  provider payloads, conversation content, authorization headers, tokens,
  idempotency keys, or PII in responses, logs, metrics, audit metadata,
  fixtures, or examples.
- Redis-backed command state, destructive migration/backfill, broad identity
  refactoring, or unrelated cleanup.

## Implementation Plan

1. Reconfirm the route/authentication contract from issue 0038 and the
   command-idempotency contract from issue 0039, then inspect the current
   Alembic head and identity tables. Trace the existing `discover_identity()`
   transaction, contact advisory lock, directory projection, evidence upsert,
   candidate-link upsert, and transition uniqueness before adding the
   external-ID command boundary.
2. Define the endpoint input/output models and sanitized error mapping. Validate
   the canonical external contact ID and nonblank opaque idempotency key at the
   HTTP boundary; use the existing admin bearer dependency and request ID; do
   not log, echo, or use the key as a matching input. Map an absent contact and
   invalid or incompatible command state only to the status/detail contract in
   SPEC-0012, without resource enumeration.
3. Reuse the shared PostgreSQL command ledger from issue 0039 with a distinct
   `identity_discovery` operation scope. Reserve the command under the contact
   serialization boundary, execute discovery and result capture in the same
   transaction, and make same-key concurrent calls converge to one stored
   result. Preserve prior evidence and transitions; never turn a confirmed link
   into a candidate or create a second transition on replay.
4. Adapt the current discovery result into the administrative projection using
   external IDs rather than local database IDs. Keep the existing rules and
   safe observability: groups remain unresolved, inactive/absent directory
   rows cannot become candidates, multiple valid companies remain ambiguous,
   and a confirmed link remains authoritative. If the local contact or
   directory facts are unavailable, fail closed according to the specification;
   do not call either provider or silently substitute a fallback company.
5. Mount the operation on the existing protected router and update the custom
   OpenAPI document without changing the webhook HMAC scheme, public query
   routes, issue-0038 read projections, or issue-0039 command contracts.
   Handlers should remain limited to HTTP validation, authentication, request-ID
   propagation, and sanitized exception translation; PostgreSQL transactions,
   locking, command idempotency, and identity persistence remain below the
   HTTP boundary.
6. Add focused offline HTTP and disposable-PostgreSQL coverage for valid,
   missing, replayed, incompatible, concurrent, rollback, group, unresolved,
   candidate, ambiguous, confirmed, rejected-history, and privacy cases. Prove
   that the command does not invoke Redis, providers, hydration, sync,
   backfill, cycle resolution, mapping, Request creation, or a second
   discovery execution on replay.
7. Run the focused tests, `PYTHONPATH=/app python scripts/verify.py`,
   `python -m compileall -q src tests alembic scripts`, strict Pyright, and
   `git diff --check`; report unavailable external-runtime prerequisites
   separately. On completion, synchronize the C.1 evidence in the README,
   ARCHITECTURE, specification index, OpenAPI/operational documentation, and
   `IMPLEMENTATION_PLAN.md`, run `graphify update .`, and close this issue only
   through that synchronized plan update and one focused commit.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migration:** PostgreSQL remains authoritative for command state,
  evidence, links, and transitions. Reuse issue 0039's additive command
  ledger; if its schema needs an operation-specific extension, make it one
  additive Alembic change with existing rows and `manual_db` history preserved.
  Never delete or rewrite evidence, links, transitions, or cycle resolutions;
  Redis is not a command ledger.
- **Compatibility:** preserve the public route set, webhook HMAC ordering,
  worker/preparation imports, SPEC-0009 matching semantics, current
  `discover_identity()` callers, and issue-0038/0039 administrative contracts.
  The new operation is unversioned and exact under `/admin/acessorias`.
- **Security/privacy:** use issue 0038's constant-time bearer authentication
  and server-derived administrative context. Log only sanitized operation,
  outcome, bounded counts, and request/correlation IDs; never log keys, tokens,
  SQL parameters, contact evidence, phone/email values, or provider payloads.
- **Observability:** distinguish new discovery, idempotent replay, invalid
  command, missing contact, and conflict/concurrency outcomes with bounded
  categories only. A replay must not be mistaken for a second discovery while
  its key and sensitive request fingerprint remain undisclosed.
- **Rollout:** deployment still requires the protected `ADMIN_API_TOKEN` and
  authorized internal network from issue 0038. This issue documents but does
  not provision secrets or perform deployment/provider verification.

## Tests

- **HTTP/authentication:** exercise the endpoint through the authenticated
  admin router with valid access and generic unauthorized behavior inherited
  from issue 0038; cover body/key validation, missing-contact handling, the
  SPEC-0012 `200` result/replay and `409` incompatible/concurrent outcomes,
  unchanged public routes, and OpenAPI security/schema projections.
- **Discovery contract:** cover exact phone/email and approved Brazilian
  mobile-variant evidence, group exclusion, candidate/ambiguous/unresolved
  states, confirmed-link precedence, rejected-history preservation, stable
  external-ID projections, bounded result fields, and no automatic confirmation.
- **Idempotency/concurrency:** cover same-key replay with no duplicate command,
  evidence, link, or transition rows; incompatible reuse with no state change;
  concurrent same-key requests converging to one result; and rollback leaving
  no command reservation or partial identity state.
- **Privacy/side effects:** assert that phone/email values, raw evidence,
  authorization headers, bearer tokens, idempotency keys, conversation content,
  and provider payloads are absent from responses, logs, metrics, audit
  metadata, fixtures, and examples; assert no Redis, provider, hydration,
  synchronization, backfill, mapping, Request, or cycle-resolution call.
- **Verification:** run the focused tests plus
  `PYTHONPATH=/app python scripts/verify.py`,
  `python -m compileall -q src tests alembic scripts`, strict Pyright, and
  `git diff --check`. Run `graphify update .` after implementation changes.

## Acceptance Criteria

- [x] `POST /admin/acessorias/contacts/{digisac_contact_external_id}/identity-discovery`
  is mounted under the authenticated router and preserves all existing route
  and security behavior.
- [x] The command accepts only a canonical existing DigiSac contact and a
  valid opaque `idempotency_key`; missing or invalid input follows the
  SPEC-0012 sanitized error contract without resource enumeration.
- [x] Discovery uses the existing conservative SPEC-0009 rules over local
  PostgreSQL facts: groups remain unresolved, only current eligible directory
  rows produce evidence, multiple companies remain ambiguous, and no automatic
  confirmation or fallback selection occurs.
- [x] A successful response exposes only the approved sanitized result and
  stable external IDs/metadata; local database IDs, phone, email, raw or
  normalized evidence, conversation content, provider payloads, tokens, and
  command keys are absent from responses and logs.
- [x] A same-scope same-key replay returns the stored result without a second
  discovery execution or duplicate command, evidence, link, or transition
  state; incompatible key reuse returns `409` and leaves identity state
  unchanged.
- [x] Concurrent requests for the same contact/key converge to one durable
  result, while a failed transaction leaves no command reservation, partial
  evidence, candidate link, or administrative transition.
- [x] Discovery does not call DigiSac/Acessórias providers, Redis, hydration,
  directory sync, backfill, cycle-resolution, department-mapping, or Request
  code, and does not mutate historical cycle resolutions.
- [x] PostgreSQL remains the authority and any schema change is additive,
  rollback-safe, preserves existing evidence/link/transition data, and keeps
  `manual_db` callers valid.
- [x] OpenAPI documents the protected discovery route, request/response
  schemas, internal/admin tag, bearer security, and SPEC-0012 response codes;
  the webhook HMAC and existing public query contract remain unchanged.
- [x] Focused offline and disposable-PostgreSQL tests cover expected,
  negative, privacy, idempotency, concurrency, rollback, and side-effect
  behavior; compileall, strict Pyright, `git diff --check`, and the applicable
  verification runner pass.
- [x] README/ARCHITECTURE, the specification index, and
  `IMPLEMENTATION_PLAN.md` record the implemented C.1 discovery slice, local
  evidence, and the remaining rollout boundary.
- [x] Graphify metadata is updated with `graphify update .`, and the issue is
  closed only after the synchronized plan update and one focused commit.

## References

- `specs/0012-administrative-contact-company-link-management.md` v1.1 —
  canonical administrative discovery endpoint, authentication, idempotency,
  privacy, and acceptance contract.
- `specs/0009-digisac-acessorias-identity-resolution.md` v1.2 — conservative
  matching, evidence provenance, link states, discovery precedence, and
  immutable cycle resolution.
- `specs/0001-shared-data-and-analysis-contract.md` v1.5 — PostgreSQL
  authority, privacy, transaction, and Redis boundaries.
- `specs/0006-api-documentation-and-openapi-contract.md` v1.1 — current HTTP
  and OpenAPI compatibility boundary.
- `issues/0015_-_implement-digisac-acessorias-identity-resolution.md` and
  `issues/0026_-_prepare-identity-and-department-mapping-before-acessorias-request.md`
  — implemented discovery/preparation domain boundaries this command must not
  bypass.
- `issues/0038_-_implement-authenticated-read-only-identity-link-triage-api.md`
  — prerequisite authenticated router and bearer boundary.
- `issues/0039_-_implement-authenticated-identity-link-confirmation-and-rejection.md`
  — prerequisite shared administrative command-idempotency boundary and sibling
  mutation contract.
- `IMPLEMENTATION_PLAN.md` — Milestone C.1 traceability gap to synchronize on
  completion; no plan rewrite is part of issue creation.

---

## Resolution

<!-- Filled by the agent on close. DO NOT edit manually. -->

### Implementation

- Added the authenticated `identity-discovery` command and explicit sanitized
  request/response projections under the existing `/admin/acessorias` router.
- Reused the PostgreSQL `identity_admin_commands` ledger with the distinct
  `identity_discovery` operation, transaction-local contact locking, external-ID
  result projection, and same-key replay/concurrency convergence.
- Added additive Alembic migration `0022_identity_discovery_command`, allowing a
  discovery command to have no company target while preserving confirmation and
  rejection rows and refusing a data-losing downgrade.
- Preserved the existing SPEC-0009 matcher and `discover_identity()` callers;
  the command uses only local PostgreSQL facts and never invokes providers,
  Redis, hydration, sync, backfill, mapping, cycle resolution, or Request code.

### Tests and validation

- Focused admin/OpenAPI/identity tests: **15 passed, 7 skipped** offline.
- `python -m compileall -q src tests alembic scripts`: passed.
- `npx --yes pyright`: **0 errors, 0 warnings, 0 informations**.
- `git diff --check`: passed.
- `APP_TIMEZONE=UTC PYTHONPATH=/app python scripts/verify.py`: passed compileall,
  strict Pyright, **238 passed, 76 skipped** offline, Alembic head
  `0022_identity_discovery_command`, and **76 passed, 238 deselected** in
  disposable PostgreSQL 16.
- The unqualified `PYTHONPATH=/app python scripts/verify.py` run reached the
  PostgreSQL suite but retained the pre-existing timezone assertion in
  `tests/test_department_mapping.py`; the issue-specific tests passed. No
  production or persistent database was used.

### Documentation and key decisions

- Synchronized SPEC-0012, `specs/README.md`, `IMPLEMENTATION_PLAN.md`,
  `README.md`, and `ARCHITECTURE.md` with the completed C.1 discovery slice,
  migration head, local validation evidence, and rollout boundary.
- Updated the custom OpenAPI contract and `src/core/db.py` schema support to
  document and accept migration `0022_identity_discovery_command`.
- Discovery has no `reason` field; its idempotency fingerprint is scoped to the
  operation and canonical contact, while the opaque key remains hashed and is
  never returned or logged.
