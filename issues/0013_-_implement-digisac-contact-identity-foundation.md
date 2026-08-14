---
id: 0013
title: "Implement the DigiSac contact identity foundation"
type: feature
status: closed
priority: critical
phase: 4
created_at: 2026-08-14
updated_at: 2026-08-14
closed_at: 2026-08-14
related_issues:
  - "0012"
blocked_by:
  - "0012"
affects:
  - alembic/versions/
  - src/api/routes.py
  - src/api/webhook_adapter.py
  - src/core/config.py
  - src/core/db.py
  - src/core/digisac_client.py
  - src/core/digisac_directory.py
  - tests/
  - scripts/verify.py
  - IMPLEMENTATION_PLAN.md
---

## Description

Implement the bounded P0 Milestone B slice from `IMPLEMENTATION_PLAN.md`: a
durable, PostgreSQL-authoritative local representation of DigiSac contacts,
incremental ticket-snapshot ingestion, and need-based individual contact
hydration. The external identity is the opaque provider `contact.id`; no phone,
name, `idFromService`, `jidId`, or `lidId` may become an identity or matching
key.

**Plan/spec references:** P0 **Milestone B — DigiSac Contact Identity
Foundation**, `IMPLEMENTATION_PLAN.md` under **Approved Acessórias milestones**,
item 2, governed by `SPEC-0008` v1.1 and the cross-cutting contracts in
`SPEC-0001` v1.2, `SPEC-0002` v1.5, `SPEC-0004` v1.5, and `SPEC-0007` v1.1.

**Dependencies:** issue `0012` (completed Acessórias directory foundation),
the specifications listed above, the current Alembic head
`0015_acessorias_directory`, and the existing DigiSac Bearer configuration and
retry boundary.

**Verified gap:** the checkout has no DigiSac contact table, contact-specific
client operation, contact persistence, or contact hydration path. The existing
`DigisacClient` handles tickets/messages, `src/core/digisac_directory.py`
covers only departments and Users, `DigisacWebhookAdapter` carries
`data.contactId` as message metadata, and the webhook has no `data.contact`
upsert or deferred hydration side effect.

Expected outcome: a valid ticket snapshot creates or converges one durable
contact identity; repeated message references create at most one recoverable
hydration need without a Contacts request in the webhook critical path; an
individual `GET /contacts/{contactId}` hydration updates the same identity
under the specified precedence; provider or credential failures preserve the
existing webhook contract and the last valid local state with sanitized,
recoverable execution state.

## Scope

### In scope

- Add one additive Alembic revision after the current head for the minimal
  contact representation and any durable hydration/synchronization state
  required to deduplicate, claim, retry, and recover individual hydration.
  Enforce one nonblank external `contact.id`, safe references, provider/local
  timestamps, and the constraints required by SPEC-0008.
- Persist the approved identity metadata: provider names when present, raw
  number, technical numeric normalization, group flag, provider account/service
  identifiers, provider timestamps including `deletedAt`, local observation
  timestamps, and source/sync state. Keep raw and normalized values separate.
- Extend or add the typed DigiSac Contacts adapter at the existing client
  boundary for `GET /contacts/{contactId}` using configured
  `DIGISAC_API_BASE_URL` and `DIGISAC_API_KEY`, the existing Bearer policy,
  bounded timeout/retry behavior, `429`/`Retry-After` handling, and sanitized
  errors.
- Integrate valid `data.contact` snapshots from `ticket.created` and
  `ticket.updated` with idempotent local upsert. Integrate message events that
  provide only `data.contactId` with a durable, deduplicated hydration need
  when the local representation is absent or insufficient, without making a
  Contacts call from the webhook handler.
- Provide the smallest internal deferred execution path needed to claim and
  process individual hydration needs through the typed adapter, preserving
  the current webhook response/status behavior when credentials or DigiSac are
  unavailable.
- Prove timestamp-aware precedence, conservative behavior for unordered
  observations, replay/concurrency idempotency, group handling, and absence
  preservation with deterministic doubles and disposable PostgreSQL tests.
- Update implementation-derived documentation, `IMPLEMENTATION_PLAN.md`, and
  Graphify metadata only when the implementation is complete, with exact local
  evidence and no provider or production-readiness claim.

### Out of scope

- Full paginated Contacts backfill or reconciliation. Do not invent `page`,
  `offset`, cursor, header, or termination semantics; this remains blocked
  until the provider's page-advance behavior is separately validated.
- Acessórias company matching, candidate/confirmed/ambiguous/rejected links,
  Brazilian mobile-variant matching, department mapping, Request creation or
  lifecycle, Users synchronization, or any fuzzy/automatic identity decision.
- New HTTP routes, an administrative interface, public hydration controls,
  changes to the eight existing HTTP routes, IA classification behavior,
  finalization, query authorization, or Redis-backed directory/hydration state.
- Physical deletion or inactivation caused by absence from a list, provider
  writes, production synchronization, real credentials, deployment changes,
  hosted CI, unrelated cleanup, or changes to SPEC-0008's contract.

## Implementation Plan

1. Reconfirm Alembic head `0015_acessorias_directory`, PostgreSQL transaction
   helpers, current webhook event branches, `DigisacClient` request/retry
   behavior, configuration validation, logging conventions, and the
   `scripts/verify.py` disposable-runner boundary. Define typed contact input
   and local-record boundaries so provider JSON does not leak into persistence
   or handlers.
2. Add one additive Alembic migration using the repository's current revision
   conventions. Model one durable row per nonblank opaque external contact ID,
   the approved safe metadata and source/observation state, plus durable
   hydration execution state if needed for deduplication and recovery. Enforce
   uniqueness, nonblank/check constraints, and a downgrade guard that refuses
   before populated data could be lost; application startup must not create or
   mutate the schema.
3. Implement the contact normalizer and persistence operations. Convert every
   Unicode decimal digit in the raw number to its ASCII digit representation,
   retain only digits, and produce no normalized value for missing/blank input.
   Treat group numbers as provider metadata only. Upsert by `contact.id`, keep
   `deletedAt` as metadata, never turn list absence into deletion, and use
   comparable `updatedAt` values to prevent older webhook or hydration data
   from overwriting a newer observation. When timestamps are not comparable,
   converge conservatively without destructive clearing.
4. Reuse the existing DigiSac configuration and request policy while adding the
   individual Contacts operation. Keep timeout, connection, transient HTTP,
   `Retry-After`, invalid-response, authentication, and missing-credential
   outcomes bounded and typed. Do not log tokens, Authorization headers, raw
   payloads, full phone numbers, names, message text, or secret-bearing URLs.
5. Wire ticket snapshot handling into the existing `ticket.created` and
   `ticket.updated` paths without changing their current response contract.
   Wire `message.created`/`message.updated` contact references to record one
   durable hydration need per contact/state rather than doing inline network
   work. Claim hydration outside the request path, fetch only the authorized
   individual endpoint, upsert through the same precedence rules, and leave a
   bounded recoverable state on failure. Repeated events and concurrent claims
   must not create duplicate work or duplicate contact identities.
6. Add deterministic adapter, normalization, webhook, hydration, and
   PostgreSQL tests for both positive and negative behavior. Include migration
   head/constraints, replay, concurrent upsert/claim, rollback/preservation,
   group and timestamp edge cases, credential/provider failures, retry limits,
   response sanitization, and proof that no Contacts request occurs inline for
   repeated message references.
7. Run focused checks, the offline suite, disposable PostgreSQL verification,
   compileall, strict Pyright, `git diff --check`, and `graphify update .`.
   Record offline, PostgreSQL, and unavailable-prerequisite evidence
   separately. Close only after the exact Milestone B status/evidence is synced
   in `IMPLEMENTATION_PLAN.md` and all implementation, tests, documentation,
   and Graphify changes are included in one focused commit.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migrations:** PostgreSQL is the durable contact and hydration
  authority; Redis may only transport or coordinate transient work and cannot
  be the contact directory or hydration state. Use an additive Alembic
  migration after `0015_acessorias_directory`; do not use startup initialization
  or legacy SQL migrations. Preserve provider-deleted metadata and historical
  rows; absence is not deletion.
- **Compatibility:** preserve the current HMAC-validated webhook, event
  filtering, response/status behavior, eight HTTP routes, IA output and
  persistence, finalization, existing DigiSac directory semantics, and Redis
  queue contracts. Contact ingestion is an additive side effect and does not
  expose contact data through HTTP.
- **Retry/concurrency/idempotency:** retry only bounded transient provider
  failures, honor usable `Retry-After`, deduplicate hydration needs by contact
  identity/state, prevent concurrent duplicate claims, and make replay converge
  without losing the newest comparable observation. A failed hydration must
  not erase the last valid contact record or mark it successfully hydrated.
- **Security/privacy:** obtain credentials only from existing secure settings.
  Keep tokens, headers, raw provider payloads, full numbers, names, and message
  text out of logs, metrics, fixtures, exceptions, and durable operational
  state. Group and phone fields are evidence only and never matching keys.
- **Observability:** record only contact/execution identifiers, operation or
  logical endpoint, attempt/count/duration, and sanitized failure category/state
  needed for recovery. Distinguish attempted, pending, succeeded, and failed
  hydration; do not claim provider, Redis, deployment, or production evidence.
- **Rollout:** local deterministic and disposable-PostgreSQL validation only.
  Activation of real credentials, a full backfill, schedules, operational
  ownership, or production synchronization requires separate authorization.

## Tests

- **Focused normalization/persistence:** deterministic tests for Unicode
  decimal-to-ASCII digit normalization, blank/missing numbers, groups,
  provider timestamps, `deletedAt`, opaque IDs, and conservative precedence.
- **Focused client/adapter:** fake `GET /contacts/{contactId}` responses for
  successful hydration, invalid JSON/shape, authentication/permanent failures,
  timeout/connection failures, transient statuses, `Retry-After`, bounded
  attempts, missing credentials, and sanitized logs/state.
- **Webhook/hydration:** test ticket snapshot upsert, message `contactId`
  handling, no inline Contacts request, deduplicated repeated references,
  recoverable provider failure, current webhook response compatibility, and
  concurrent claim behavior.
- **PostgreSQL:** add `postgres`-marked coverage for the additive migration at
  head, uniqueness/check constraints, idempotent replay, concurrent upsert and
  hydration claim, rollback/preservation of the last valid row, and no
  deletion from absent-list or partial input.
- **Repository validation:**
  `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`,
  `PYTHONPATH=/app python scripts/verify.py` when disposable PostgreSQL/Docker
  prerequisites are available,
  `python -m compileall -q src tests alembic scripts`,
  `npx --yes pyright`, `git diff --check`, and `graphify update .`.

## Acceptance Criteria

- [x] An additive Alembic migration after `0015_acessorias_directory` creates
  the durable contact identity and required hydration/sync state, enforces one
  nonblank external `contact.id`, preserves data on downgrade, and does not
  require startup schema mutation.
- [x] A valid ticket `data.contact` snapshot inserts or updates exactly one
  contact identity; replay and concurrent delivery do not duplicate rows or
  erase a newer comparable observation.
- [x] The stored representation preserves the approved metadata, raw number,
  numeric-only normalized number, group flag, provider timestamps, `deletedAt`,
  local observation/source state, and no normalized number is produced from
  missing or blank input.
- [x] Phone, name, `idFromService`, `jidId`, `lidId`, and group status are never
  used as identity, automatic matching, company resolution, or confirmation
  inputs; no Acessórias link, candidate, Request, or public contact response is
  created.
- [x] A message carrying only `contactId` records at most one recoverable
  hydration need for the relevant local state, performs no Contacts network
  call inline, and repeated/concurrent messages do not create duplicate work.
- [x] The deferred hydration path uses only the configured individual Contacts
  endpoint, updates the same `contact.id` under SPEC-0008 precedence, and
  leaves the last valid data plus recoverable sanitized failure state after
  missing credentials, invalid responses, permanent failures, or exhausted
  transient retries.
- [x] Retry behavior covers timeout, connection, `429`/`Retry-After`, and the
  repository's bounded transient policy; authentication headers, tokens, raw
  payloads, full phone numbers, names, and message text are absent from logs,
  metrics, fixtures, exceptions, and durable operational state.
- [x] The existing webhook authentication, event filtering, response/status
  behavior, eight HTTP routes, IA/finalization behavior, DigiSac department and
  Users directory, and Redis authority boundary remain unchanged.
- [x] Deterministic unit/client/webhook tests and disposable-PostgreSQL tests
  cover expected and negative cases, migration head, constraints, idempotency,
  concurrency, rollback/preservation, and no-inline-hydration behavior.
- [x] The focused tests, applicable offline and PostgreSQL suites, compileall,
  strict Pyright, `git diff --check`, and `graphify update .` pass; the
  canonical runner records unavailable prerequisites separately from skips and
  passes.
- [x] Full Contacts pagination/backfill remains unimplemented and is not
  represented as provider-verified behavior; the plan/spec/reference cross-check
  records this boundary.
- [x] `IMPLEMENTATION_PLAN.md` marks only P0 Milestone B complete with exact
  local evidence, required implementation-derived documentation and Graphify
  metadata are synchronized, and closure occurs in one focused commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — **Approved Acessórias milestones**, item 2,
  **P0 | completed locally | implemented** Milestone B; see
  **Specification boundary and next gate**.
- Primary specification: `specs/0008-digisac-contact-identity-foundation.md`
  v1.2 — canonical contact identity, metadata, ticket snapshot upsert,
  individual hydration, precedence, privacy, retry, and verification contract.
- Cross-cutting contracts:
  `specs/0001-shared-data-and-analysis-contract.md` v1.2,
  `specs/0002-digisac-webhook-and-query-api.md` v1.5,
  `specs/0004-reproducible-verification-baseline.md` v1.5, and
  `specs/0007-acessorias-external-directory-foundation.md` v1.1.
- Prerequisite: issue `0012` and Alembic revision
  `0015_acessorias_directory`; the completed slice advances the head to
  `0016_digisac_contact_identity`.
- Current implementation boundaries: `src/core/digisac_client.py`,
  `src/core/digisac_directory.py`, `src/api/webhook_adapter.py`,
  `src/api/routes.py`, `src/core/db.py`, `src/core/config.py`, and
  `scripts/verify.py`.

---

## Resolution

Implemented and closed issue 0013 as the bounded SPEC-0008 Milestone B slice.

- Added Alembic revision `0016_digisac_contact_identity` with durable
  `digisac_contacts` and `digisac_contact_hydrations` tables, uniqueness and
  nonblank/digit/status checks, indexes, and data-preserving downgrade guard.
- Extended the typed DigiSac client with individual `GET /contacts/{contactId}`
  hydration, bounded retry/error categories, safe path quoting, and contact
  normalization. Raw/normalized numbers, group metadata, provider timestamps,
  and approved names/account/service metadata remain separate from identity;
  `idFromService`, `jidId`, and `lidId` are not persisted or matched.
- Added timestamp-aware PostgreSQL upsert/claim/lease/retry operations and an
  API-owned deferred hydration loop. Ticket snapshots upsert additively, while
  message-only `contactId` references create one durable need without an inline
  Contacts request. Stale claims cannot complete over a newer lease.
- Preserved existing HMAC, event filtering, webhook responses, HTTP routes,
  IA/finalization behavior, and Redis authority boundaries. Full Contacts
  pagination/backfill remains intentionally outside this issue pending provider
  page-advance evidence.
- Added deterministic normalization/client/webhook tests and disposable
  PostgreSQL coverage for migration head, constraints, concurrent upsert/claim,
  timestamp precedence, idempotency, no-inline hydration, recovery, and
  preservation after sanitized failure.
- Synchronized SPEC-0008, SPEC-0001/SPEC-0004 cross-references, the spec index,
  README, PRD, architecture, implementation plan, runner schema expectation,
  configuration example, and Graphify metadata.

Validation recorded:

- `PYTHONPATH=/app python scripts/verify.py`: compileall PASS; strict Pyright
  PASS; offline pytest **160 passed, 40 skipped**; disposable PostgreSQL 16,
  Alembic head `0016_digisac_contact_identity`, and PostgreSQL pytest **40
  passed, 160 deselected** PASS.
- Focused contact/client/webhook tests PASS; `git diff --check` PASS; Graphify
  update PASS.

No real DigiSac credential, provider synchronization, Redis runtime,
deployment, or production-readiness claim was used.
