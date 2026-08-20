---
id: 0012
title: "Implement the Acessórias external directory foundation"
type: feature
status: closed
priority: critical
phase: 4
created_at: 2026-08-14
updated_at: 2026-08-14
closed_at: 2026-08-14
related_issues:
  - "0001"
  - "0002"
  - "0004"
blocked_by: []
affects:
  - alembic/versions/
  - src/core/
  - tests/
  - scripts/verify.py
  - .env.example
  - IMPLEMENTATION_PLAN.md
---

## Description

Implement the first approved Acessórias milestone from `IMPLEMENTATION_PLAN.md`:
an Alembic-owned, PostgreSQL-authoritative local directory that can be fully
reconciled from the provider and safely consumed by later identity, department,
and Request work. The implementation must be a complete vertical slice with a
dedicated provider boundary, durable persistence, controlled refresh, and
disposable-PostgreSQL evidence.

**Plan/spec references:** P0 Milestone A — **Acessórias Directory Foundation**
(`IMPLEMENTATION_PLAN.md`, Approved Acessórias milestones, item 1), governed by
`SPEC-0007` v1.1 and the cross-cutting persistence and verification contracts in
`SPEC-0001` and `SPEC-0004`.

**Verified gap:** SPEC-0007 records that this checkout has no Acessórias client,
configuration, migration, table, or test. The existing
`src/core/digisac_directory.py` synchronizes DigiSac departments/users and is
only a local pattern; it is not an Acessórias API contract and must not supply
the provider paths, fields, pagination, credentials, or authority model for
this work.

Expected outcome: a valid complete provider snapshot creates or converges the
four durable directory resource groups (companies, company contacts,
departments, and current company-department relationships), while invalid or
partial input, provider failure, concurrent refresh, missing credentials, and
pre-commit failure preserve the last successful view and expose only sanitized
execution state.

## Scope

### In scope

- Add an additive Alembic revision after the current head for separate durable
  company, company-contact, department, company-department relationship, and
  synchronization-execution records. Apply the required external-identity
  uniqueness, parent references, relationship uniqueness, nonblank/check
  constraints, presence/activity state, raw provider status, timestamps, safe
  counts, and sanitized failure state from SPEC-0007.
- Implement a dedicated typed Acessórias adapter that centralizes the Bearer
  header and reads its token only from secure configuration. Use the observed
  base URL and read contracts from SPEC-0007: departments `ListAll`, paginated
  companies `ListAll` with contacts/departments, and the optional company detail
  enrichment path. Do not add Users or Request behavior.
- Implement complete snapshot acquisition and validation: start company
  pagination at `Pagina=1`, terminate only on a valid empty page, detect repeated
  pages/content and a configurable safety limit, reject invalid pages,
  identifiers, contacts, or parent references, and compose active/inactive
  coverage without assuming undocumented status values or delta cursors.
- Normalize and persist contact `Celular` and `E-mail` alongside their raw
  values exactly as specified, while treating them only as technical evidence.
  Preserve inactive and historically absent resources; mark absence only after
  a validated complete snapshot, and reactivate/reconfirm the same external
  records when they return.
- Reconcile the validated snapshot in one PostgreSQL transaction with a durable
  or process-safe single-refresh guard. Replays and recovery after interruption
  must be idempotent, must not duplicate contacts/relationships/success
  executions, and must preserve the last successful snapshot if acquisition,
  validation, cancellation, or commit fails.
- Provide one explicit internal refresh mechanism (job or CLI) using the same
  adapter and reconciliation path for initial, periodic, and operational
  refreshes. Do not expose a public HTTP refresh endpoint or make Redis the
  directory authority.
- Implement bounded handling for timeout, connection failure, HTTP 408/425/429/
  500/502/503/504, including `Retry-After` when present and conservative
  throttling at no more than 100 requests per minute. Non-transient responses,
  authentication failures, invalid payloads, and integrity errors must fail
  sanitized and must not be interpreted as an empty directory.
- Add deterministic adapter doubles and disposable-PostgreSQL coverage for the
  complete contract, then update only the implementation-plan status/evidence
  and Graphify metadata when the build issue is completed.

### Out of scope

- DigiSac contact persistence, Acessórias identity matching or confirmation,
  department mapping, Request creation/lifecycle, Users synchronization, or
  any automatic/fuzzy matching.
- New HTTP routes, administrative UI, public refresh controls, changes to the
  eight existing HTTP routes, IA taxonomy/output/persistence, webhook behavior,
  or the existing DigiSac directory semantics.
- Any provider write/effect, production synchronization, real credential,
  live-provider acceptance claim, Redis-backed directory state, hosted CI, or
  deployment/rollout change.
- Delta synchronization based on unverified provider dates, invented status
  values/fields/parameters, physical deletion of historical directory data,
  unrelated cleanup, or changes to SPEC-0007's contract.

## Implementation Plan

1. Reconfirm the current Alembic head, PostgreSQL helper/transaction patterns,
   configuration validation, logging conventions, `SPEC-0007` v1.1 field and
   endpoint evidence, and the `scripts/verify.py` disposable-runner boundary.
   Keep the existing DigiSac directory implementation as a non-authoritative
   reference only; do not copy its API assumptions. Define typed internal
   records at the adapter boundary so provider JSON cannot leak into persistence
   or application handlers.
2. Add one additive Alembic migration using the repository's current revision
   conventions. Model the four resources separately, retain opaque external
   identifiers and safe display/provider fields, enforce unique identities and
   valid parent/relationship references, and record sync attempts, complete
   success, failure, timestamps, counts, and sanitized error categories. The
   downgrade must refuse before data loss when directory or sync state exists;
   application startup must not create or alter these tables.
3. Add secure configuration for the Acessórias base URL, token, timeout,
   bounded retry/throttle controls, and pagination safety limit with validation
   that prevents unsafe values. Centralize the Bearer header in the dedicated
   adapter; never persist, log, metric, or include the token or complete
   authorization header in an exception.
4. Implement adapter acquisition in the contract order: departments, complete
   company pages beginning at `Pagina=1`, and only the permitted detail
   enrichment if needed. Accept an empty valid list as the end sentinel; reject
   non-list or structurally incomplete pages, missing required identifiers,
   repeated page/content loops, invalid parent references, and the safety-limit
   condition. Preserve provider status as raw data and keep company phone
   separate from contact mobile. Represent empty contact lists and empty contact
   values without dropping valid companies or contacts.
5. Stage and validate the complete snapshot before opening the publication
   transaction. Upsert by external identity, update safe attributes and
   presence/activity, normalize contacts, upsert current relationships, mark
   source-absent resources/links as not present without deleting rows, and
   update the execution as successful only in the same committed transaction.
   On any pre-commit failure, rollback all changes and leave the previous
   complete view and success marker unchanged. Use the refresh guard to prevent
   overlapping publications and make replay/recovery converge.
6. Apply bounded provider retry and rate handling around every request. Treat
   the SPEC-0007 transient statuses, timeout, and connection errors as
   retryable; honor numeric/date `Retry-After` when usable, use limited local
   backoff otherwise, and stop after the configured attempt count. Categorize
   non-transient/authentication/payload/integrity failures without masking them
   as an empty snapshot. Emit only run/resource/page/attempt/duration/count and
   sanitized failure metadata; never emit contact names, phones, emails,
   payloads, URLs containing secrets, tokens, or authorization headers.
7. Add focused deterministic adapter tests for pagination, valid empty end,
   repeated pages, safety-limit and invalid-payload failures, active/inactive
   composition, contact normalization, relationship validation, transient and
   non-transient failures, `429` with/without `Retry-After`, bounded attempts,
   missing credentials, and sanitized logs/state. Add PostgreSQL tests for head
   migration, constraints, idempotent replay, concurrent refresh exclusion,
   rollback/preservation of the last good snapshot, absence/inactivation, and
   reactivation. Use synthetic provider fixtures only; do not record
   exploration tokens or real PII.
8. Run focused and full applicable checks, record offline versus disposable
   PostgreSQL evidence separately, inspect migration downgrade behavior and the
   focused diff, run `graphify update .`, then synchronize only the Milestone A
   item and exact evidence in `IMPLEMENTATION_PLAN.md`. Close this issue only
   when implementation, tests, required documentation/Graphify updates, and
   that plan sync are included in one focused commit.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migrations:** PostgreSQL is the sole durable directory authority. Use
  an additive Alembic migration after the current head; do not use application
  initialization or legacy `migrations/` SQL to create the schema. Never
  physically delete companies, contacts, departments, relationships, or sync
  history as part of reconciliation. A downgrade that would lose populated
  directory state must refuse explicitly.
- **Compatibility:** preserve all existing DigiSac webhook/query routes,
  classification fields and persistence, Redis queue behavior, and the
  DigiSac-only directory contract. The new directory is an internal foundation
  for later SPEC-0008–0011 work and does not change their blocked status or
  introduce matching semantics.
- **Retry/concurrency/idempotency:** transient provider work is bounded and
  rate-limited; only one Acessórias snapshot may publish at a time. Repeated
  snapshots, duplicate pages, retries, and post-failure recovery must not
  duplicate rows or mark a partial snapshot complete. A failed run cannot mark
  absence or replace the last good snapshot.
- **Security/configuration:** obtain the Bearer token exclusively from secure
  configuration, keep it absent from repository fixtures/examples and persisted
  state, and sanitize all errors/logs/metrics. Do not add authentication
  exceptions, provider credentials, raw payloads, contact PII, or signed URLs.
- **Observability:** correlate a sanitized execution ID with resource/page,
  attempts, duration, counts, and failure category. Distinguish attempted,
  complete success, and failure; do not claim provider, Redis, replica,
  deployment, or production verification from deterministic doubles.
- **Rollout:** local/disposable PostgreSQL validation only. Any real provider
  credential, production target, schedule activation, operational ownership,
  or deployment acceptance requires separate authorization and is outside this
  issue.

## Tests

- **Focused adapter and normalization:** run the focused test module(s) added
  for the Acessórias adapter and use deterministic provider doubles; cover all
  pagination, payload, retry, normalization, credential, and sanitization
  cases in SPEC-0007.
- **PostgreSQL:** run the new `postgres`-marked directory tests against the
  disposable database and verify migration head, constraints, transaction
  rollback, idempotent replay, concurrent guard, absence/inactivation, and
  reactivation.
- **Offline suite:**
  `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`
- **Static/repository validation:**
  `python -m compileall -q src tests alembic scripts`, `npx --yes pyright`,
  `git diff --check`, and the focused migration/schema inspection.
- **Canonical runner:**
  `PYTHONPATH=/app python scripts/verify.py` when disposable PostgreSQL/Docker
  prerequisites are available; record compileall, Pyright, offline, migration,
  and PostgreSQL stages separately, including any unavailable prerequisite.
- **Graph/documentation:** run `graphify update .` after implementation and
  verify the plan, SPEC-0007 v1.1 references, and current-head/schema claims
  remain consistent.

## Acceptance Criteria

- [x] An additive Alembic migration creates separate durable company,
  company-contact, department, company-department relationship, and
  synchronization-execution records with the required unique identities,
  references, checks, timestamps, presence/activity state, and sanitized
  execution outcome fields.
- [x] A valid complete synthetic snapshot persists all four resource groups in
  PostgreSQL, preserves inactive records, and a repeated identical snapshot
  converges without duplicate resources, contacts, relationships, or successful
  executions.
- [x] A valid empty terminal company page ends pagination, while repeated
  page/content, invalid/incomplete page, missing identifier, invalid parent
  reference, and safety-limit cases fail the run and are never published as a
  complete empty directory.
- [x] Contact raw `Celular`/`E-mail` values and their SPEC-0007 normalizations
  are stored separately; missing/blank values produce no normalized identifier,
  and no identity matching or confirmation is triggered.
- [x] A validated later snapshot marks source-absent resources and
  relationships not present without physical deletion, and a later provider
  return reactivates/reconfirms the same external records.
- [x] Acquisition, validation, cancellation, or commit failure rolls back the
  attempted snapshot, leaves the last complete view and success marker
  queryable, and records a sanitized failed execution rather than success.
- [x] Concurrent refresh attempts are mutually excluded at publication, and
  replay/recovery is idempotent with no duplicate rows or partial-success
  state.
- [x] Timeout, connection, 408/425/429/500/502/503/504, and `Retry-After`
  behavior is covered with bounded attempts and no more than 100 requests per
  minute; non-transient and authentication failures remain visible as
  sanitized failures.
- [x] Missing credentials skip/fail safely without changing the existing
  directory, and no token, authorization header, payload, contact name, phone,
  email, or secret-bearing URL appears in logs, metrics, fixtures, or persisted
  sync state.
- [x] The refresh mechanism is explicitly invokable and reuses the same adapter
  and reconciliation guarantees for initial, periodic, and operational runs,
  without adding a public endpoint or Redis authority.
- [x] Deterministic adapter tests and disposable-PostgreSQL tests cover the
  positive and negative cases above, and the applicable offline suite,
  compileall, strict Pyright, canonical runner (when available), and
  `git diff --check` pass with exact results recorded and unavailable stages
  labeled.
- [x] Existing HTTP, classification, DigiSac directory, Redis, and migration
  behavior outside the new additive directory remains unchanged; no provider or
  production-readiness claim is added.
- [x] `graphify update .` passes, the focused implementation diff contains no
  unrelated cleanup, and the plan/spec/reference cross-check is consistent.
- [x] `IMPLEMENTATION_PLAN.md` marks only P0 Milestone A complete and records
  the observed local evidence without changing the blocked status of
  Milestones B–E or claiming real-provider/production verification.
- [x] The issue is closed only after implementation, required tests, Graphify
  and plan synchronization, and one focused commit are complete.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — **Approved Acessórias milestones**, item 1,
  **P0 | completed locally | implemented** Milestone A; see **Specification
  boundary and next gate**.
- Primary specification: `specs/0007-acessorias-external-directory-foundation.md`
  v1.1 — canonical resources, observed provider endpoints, complete snapshot,
  transactional reconciliation, bounded retry, sanitization, and acceptance
  criteria.
- Cross-cutting contracts:
  `specs/0001-shared-data-and-analysis-contract.md` and
  `specs/0004-reproducible-verification-baseline.md`.
- Current implementation boundaries: `alembic/versions/`, `src/core/config.py`,
  `src/core/db.py`, `src/core/provider_retry.py`, `scripts/verify.py`,
  `.env.example`, and the existing DigiSac-only
  `src/core/digisac_directory.py` pattern.
- Prerequisite evidence: issues `0001`, `0002`, and `0004`.

---

## Resolution

Implemented the Acessórias directory foundation as issue 0012.

- Added Alembic revision `0015_acessorias_directory` with durable companies,
  contacts, departments, current relationships, execution state, checks,
  foreign keys, unique identities, presence/activity state, and a
  data-preserving downgrade guard.
- Added `src/core/acessorias_directory.py` with typed provider records,
  centralized Bearer authentication, exact observed read paths, complete
  pagination, loop/safety-limit detection, normalization, bounded transient
  retry and rate limiting, sanitized failure categories, advisory-lock
  publication, transactional reconciliation, absence/inactivation,
  reactivation, and successful-snapshot deduplication. The module is the
  explicit internal CLI entry point; no HTTP endpoint or Redis authority was
  added.
- Added deterministic adapter and disposable-PostgreSQL tests covering valid
  pages, terminal empty pages, malformed/partial/looping input, all transient
  HTTP statuses, timeout/authentication failures, normalization, idempotency,
  rollback-preserving failure state, absence/reactivation, and PostgreSQL
  advisory-lock exclusion.
- Added secure configuration examples and updated the canonical runner/schema
  verification to head `0015_acessorias_directory`.
- Synchronized SPEC-0007, SPEC-0004, `specs/README.md`, PRD, architecture,
  and `IMPLEMENTATION_PLAN.md` with local-only evidence. Graphify update
  completed successfully; its existing SQL parser warning remains a graph
  coverage limitation, not an implementation failure.

Validation recorded:

- `PYTHONPATH=/app python scripts/verify.py`: compileall PASS; Pyright PASS;
  offline pytest **143 passed, 36 skipped**; disposable PostgreSQL 16 and
  Alembic head **0015_acessorias_directory** PASS; PostgreSQL pytest **36
  passed, 143 deselected**.
- `git diff --check` PASS and `graphify update .` PASS.

No real Acessórias credential, provider synchronization, Redis runtime,
deployment, or production-readiness claim was used. Milestones B–E remain
blocked and unchanged.
