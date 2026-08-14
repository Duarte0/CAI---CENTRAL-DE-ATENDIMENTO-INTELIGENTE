---
id: 0014
title: "Implement the DigiSac Contacts full backfill"
type: feature
status: closed
priority: critical
phase: 4
created_at: 2026-08-14
updated_at: 2026-08-14
closed_at: 2026-08-14
related_issues:
  - "0013"
blocked_by:
  - "0013"
affects:
  - alembic/versions/
  - src/core/
  - src/utils/
  - tests/
  - scripts/verify.py
  - .env.example
  - IMPLEMENTATION_PLAN.md
---

## Description

Implement the remaining P0 Milestone B slice: a controlled full backfill of
the DigiSac Contacts directory into the existing PostgreSQL-authoritative
contact identity store. The backfill must acquire and validate a complete
Contacts listing before publishing it through the same typed normalization and
`contact.id`-keyed upsert boundary used by ticket snapshots and individual
hydration.

**Plan/spec references:** P0 **Milestone B — DigiSac Contact Identity
Foundation**, `IMPLEMENTATION_PLAN.md` under **Approved Acessórias
milestones**, item 2, governed by `SPEC-0008` v1.3 and the cross-cutting
contracts in `SPEC-0001` v1.2 and `SPEC-0004` v1.5.

**Dependencies:** closed issue `0013`; the existing Alembic head
`0016_digisac_contact_identity`; SPEC-0001, SPEC-0002, SPEC-0004, SPEC-0007,
and SPEC-0008; the configured DigiSac Bearer boundary and existing bounded
retry policy. No provider credential, production synchronization target, or
new product decision is required for this local implementation slice.

**Verified gap:** the current `DigisacClient` implements the typed individual
`GET /contacts/{contactId}` operation and the repository has durable contact
upsert/hydration state, but there is no Contacts list operation, page-envelope
validation for full backfill, global cross-page deduplication, explicit
backfill execution path, or corresponding tests. Issue `0013` intentionally
left this slice out while page advancement was unverified. Authorized evidence
now establishes high `perPage`, `page=N` advancement, and
`currentPage`/`lastPage` termination, so the gap is implementation-ready.

Expected outcome: an explicit internal backfill execution acquires a valid
single-page or multi-page Contacts snapshot, rejects invalid or non-advancing
pagination without declaring success, globally deduplicates by opaque
`contact.id`, and atomically/conservatively converges valid contacts without
deleting or inactivating records merely because they are absent from a failed,
partial, or later listing.

## Scope

### In scope

- Extend the existing typed DigiSac client boundary with the authorized
  Contacts list operation. Request a high but safe `perPage` value through
  configuration or a documented technical constant; treat the observed
  `5000` as tenant evidence, not a universal provider guarantee.
- Define a typed page/envelope boundary that validates `data`, `total`,
  `limit`, `currentPage`, and `lastPage` before any page contributes to the
  snapshot. Require positive, internally consistent page metadata and reject
  malformed contact objects or nonblank-identity violations through the
  existing sanitized error boundary.
- Implement single-page termination when `lastPage == 1`. For multi-page
  responses, request the next page with the validated `page=N` parameter and
  require `currentPage` to advance to the requested page until
  `currentPage == lastPage`. A repeated page, non-advancing page, empty page
  before `lastPage`, impossible metadata, or provider failure must fail the
  execution rather than being treated as normal completion.
- Deduplicate the complete acquired snapshot globally by opaque
  `contact.id`, including duplicates across adjacent pages, and route each
  retained record through the existing contact normalizer and timestamp-aware
  upsert semantics. Do not use phone, name, `idFromService`, `jidId`, `lidId`,
  group status, or any derived number as identity or matching evidence.
- Add one explicit internal execution path, such as the repository's existing
  utility/CLI pattern, that uses the same adapter and persistence boundary for
  an operational backfill. It must be invokable without adding a public HTTP
  route, admin surface, or Redis directory state.
- Acquire and validate all pages before publication. Persist only a validated
  complete snapshot through a transaction or equivalent failure-safe database
  boundary so an acquisition, validation, cancellation, or commit failure
  cannot be reported as success or erase the last valid local contact data.
  Absence from a valid listing is not permission to delete or inactivate a
  contact; provider `deletedAt` remains metadata on the contact row.
- Add deterministic client, page-validation, deduplication, execution,
  persistence, and disposable-PostgreSQL coverage for the complete contract,
  including positive and negative pagination, replay, failure preservation,
  and concurrent execution behavior.
- On completion, synchronize only the implementation-derived documentation and
  `IMPLEMENTATION_PLAN.md` status/evidence required by the plan, and update
  Graphify metadata through the repository workflow. Keep provider, Redis,
  deployment, credential, and production-readiness claims explicitly out of
  the evidence.

### Out of scope

- Acessórias company matching, candidate/confirmed/ambiguous/rejected links,
  Brazilian mobile-variant matching, department mapping, Request creation or
  lifecycle, or any automatic/fuzzy identity decision.
- New HTTP routes, public backfill controls, webhook behavior changes, IA or
  finalization changes, query authorization, Users synchronization, or use of
  Redis as a directory or backfill authority.
- Invented cursor, offset, header, delta, or termination semantics; the only
  approved multi-page mechanism is `page=N` with the validated
  `currentPage`/`lastPage` contract in SPEC-0008.
- Physical deletion or inactivation from list absence, partial input, an
  invalid page, or a failed execution; provider writes and production
  synchronization; real credentials, deployment changes, hosted CI, and
  unrelated cleanup.
- Changes to SPEC-0008 or to the already completed issue-0013 contract.

## Implementation Plan

1. Reconfirm the current client `_get_json` authentication/retry boundary,
   `DigisacContact` normalization, PostgreSQL contact upsert transaction,
   existing utility/CLI conventions, settings validation, and disposable
   runner. Define typed page metadata and a complete-snapshot result so raw
   provider JSON stays outside persistence and execution reporting.
2. Add the list operation using only the configured DigiSac base URL and
   Bearer credential. Pass the safe high `perPage` setting and `page` only as
   specified. Reuse bounded timeout, connection, transient HTTP,
   `Retry-After`, authentication, missing-credential, invalid-response, and
   sanitized-error handling; do not log Authorization, raw payloads, full
   numbers, names, or secret-bearing URLs.
3. Validate every page before advancing. Check the list envelope and typed
   contact records, require the requested page and returned `currentPage` to
   agree, reject `lastPage < currentPage`, reject nonpositive or contradictory
   totals/limits, reject empty intermediate pages, and guard against repeated
   page numbers or any non-advancing response. Stop only at the validated last
   page, including the one-page optimization.
4. Maintain a global `contact.id` set/map across the entire acquisition. Allow
   a repeated contact identity to be idempotently represented once, then apply
   the existing conservative timestamp precedence when upserting. Do not
   convert missing list entries into deletion or overwrite newer observations
   with older or unordered data.
5. Publish the validated snapshot through an internal explicit backfill path.
   Keep network acquisition outside the database transaction, then persist the
   complete normalized set through a failure-safe transaction/bulk boundary;
   report success only after commit. A failed acquisition or commit must leave
   the prior rows and any operational state recoverable and must never present
   a partial snapshot as complete. Concurrent runs must serialize or converge
   safely without duplicate identities or contradictory completion state.
6. Add deterministic tests for one-page execution, multi-page `page=N`
   fallback, repeated contacts across pages, page mismatch/non-advancement,
   invalid envelope/contact, empty intermediate page, provider/auth/retry
   failure, missing credentials, replay, concurrent execution, commit
   rollback/preservation, timestamp precedence, absence preservation, and
   sanitized observability. Add disposable-PostgreSQL coverage at Alembic head
   for the actual bulk persistence and uniqueness invariants.
7. Run the focused tests, offline suite, disposable PostgreSQL verification,
   compileall, strict Pyright, `git diff --check`, and `graphify update .`.
   Record offline, PostgreSQL, and unavailable-prerequisite evidence
   separately. Close only after the Milestone B status/evidence and required
   implementation-derived documentation are synchronized in
   `IMPLEMENTATION_PLAN.md` and all implementation, tests, docs, and Graphify
   changes are included in one focused commit.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migrations:** reuse the additive `0016_digisac_contact_identity`
  schema and PostgreSQL contact authority unless a schema gap is demonstrated
  by the implementation contract. If execution state requires an additive
  migration, document its invariant and data-preserving downgrade explicitly;
  do not mutate schema at application startup. Persist normalized contacts
  only after complete acquisition/validation, preserve `deletedAt`, and never
  delete or inactivate from absence.
- **Compatibility:** preserve the existing HMAC-validated webhook, event
  filtering and response/status behavior, eight HTTP routes, IA output and
  finalization, individual hydration behavior, DigiSac department/User
  directory behavior, and Redis queue contracts. The backfill is an additive
  internal operation and exposes no contact HTTP response.
- **Retry/concurrency/idempotency:** retry only the existing bounded transient
  classes and honor usable `Retry-After`; do not retry invalid or permanent
  responses as if they were pagination completion. Replays must converge by
  `contact.id`, page validation must prevent infinite loops, and concurrent
  executions must not publish duplicate identities or claim a failed partial
  result as complete.
- **Security/privacy:** read credentials only from the existing secure
  settings. Keep tokens, Authorization headers, raw provider payloads, full
  phone numbers, names, message text, and secret-bearing URLs out of logs,
  metrics, exceptions, fixtures, and durable execution state. Contact data is
  not exposed through a new route.
- **Observability:** record only safe operation/contact identifiers, page and
  deduplication counts, attempt/duration data, completion state, and sanitized
  failure categories. Distinguish acquired/validated/published/failed outcomes;
  do not claim provider, Redis, deployment, or production evidence from
  deterministic/disposable tests.
- **Rollout:** local deterministic and disposable-PostgreSQL validation only.
  Scheduling, real credentials, operational ownership, production execution,
  and provider-volume acceptance require separate authorization.

## Tests

- **Focused client/page contract:** deterministic fake Contacts responses for
  a valid one-page result, valid multi-page `page=N` results, cross-page
  duplicate IDs, invalid envelopes and contact shapes, page mismatch,
  non-advancing/repeated pages, empty intermediate pages, and inconsistent
  metadata.
- **Provider boundary:** missing credentials, authentication/permanent errors,
  timeout/connection errors, transient statuses including `429` with
  `Retry-After`, bounded attempts, invalid JSON, and sanitized failure state.
- **Persistence/execution:** replay and concurrent backfill tests prove one
  durable row per opaque `contact.id`, timestamp-aware precedence, no list-
  absence deletion/inactivation, failed acquisition/commit preservation, and
  success only after durable publication.
- **PostgreSQL:** add `postgres`-marked coverage for the existing Alembic head,
  contact uniqueness/check constraints, complete snapshot publication,
  rollback/preservation, and concurrent execution/transaction behavior.
- **Repository validation:**
  `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`,
  `PYTHONPATH=/app python scripts/verify.py` when disposable
  PostgreSQL/Docker prerequisites are available,
  `python -m compileall -q src tests alembic scripts`,
  `npx --yes pyright`, `git diff --check`, and `graphify update .`.

## Acceptance Criteria

- [x] The internal backfill uses only the configured DigiSac Contacts list
  endpoint, Bearer configuration, and the safe high `perPage`/`page=N`
  parameters authorized by SPEC-0008; it adds no public HTTP route or Redis
  directory authority.
- [x] A valid `lastPage == 1` response completes through the one-page path, and
  a valid `lastPage > 1` response requests each next page with `page=N` until
  the validated last page.
- [x] Every page validates `data`, `total`, `limit`, `currentPage`, and
  `lastPage`; malformed metadata, invalid contacts, page mismatch, repeated or
  non-advancing pages, empty intermediate pages, and provider failures fail
  the execution without declaring a successful backfill.
- [x] The complete acquired snapshot is deduplicated globally by opaque
  `contact.id`; replay and cross-page duplicates produce one durable identity,
  while phone, names, provider service IDs, group status, and derived numbers
  never become identity or matching keys.
- [x] Valid records are normalized and upserted through the existing contact
  boundary with SPEC-0008 timestamp precedence; older or unordered observations
  cannot erase newer known metadata, and `deletedAt` remains metadata.
- [x] Acquisition and validation complete before publication; a provider,
  validation, cancellation, transaction, or commit failure preserves the last
  valid contact state and cannot report a partial snapshot as complete.
- [x] Absence from a valid or failed list never deletes or inactivates a local
  contact, and failed execution state is bounded, recoverable where applicable,
  and sanitized.
- [x] Concurrent executions serialize or converge safely, do not duplicate
  contacts, do not loop indefinitely, and only report success after durable
  commit.
- [x] Retry handling covers the existing bounded timeout, connection,
  transient-status, and usable `Retry-After` policy; authentication, invalid
  response, and permanent failures are not misclassified as completion.
- [x] Tokens, Authorization headers, raw provider payloads, full phone numbers,
  names, message text, and secret-bearing URLs are absent from logs, metrics,
  exceptions, fixtures, and durable operational state.
- [x] Deterministic unit/client/execution tests and disposable-PostgreSQL tests
  cover expected and negative pagination, deduplication, idempotency,
  concurrency, rollback/preservation, security sanitization, and migration
  invariants.
- [x] The focused tests, applicable offline and PostgreSQL suites, compileall,
  strict Pyright, `git diff --check`, and `graphify update .` pass; unavailable
  prerequisites are recorded separately from skips and passes.
- [x] `IMPLEMENTATION_PLAN.md` records only the completed Milestone B status
  and exact local evidence, required implementation-derived documentation and
  Graphify metadata are synchronized, and this issue is closed through one
  focused commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — **Approved Acessórias milestones**, item 2,
  **P0 | completed locally | implemented** Milestone B; see
  **Specification boundary and next gate** and **Recommended next pass**.
- Primary specification: `specs/0008-digisac-contact-identity-foundation.md`
  v1.3 — Contacts list evidence, page contract, global deduplication,
  timestamp precedence, failure preservation, privacy, and verification.
- Cross-cutting contracts:
  `specs/0001-shared-data-and-analysis-contract.md` v1.2,
  `specs/0002-digisac-webhook-and-query-api.md` v1.5,
  `specs/0004-reproducible-verification-baseline.md` v1.5, and
  `specs/0007-acessorias-external-directory-foundation.md` v1.1.
- Prerequisites: issue `0013`, Alembic revision
  `0016_digisac_contact_identity`, and the existing DigiSac client/retry
  boundary.
- Current implementation boundaries: `src/core/digisac_client.py`,
  `src/core/db.py`, `src/core/digisac_contact_hydration.py`,
  `src/api/routes.py`, `src/core/config.py`, `tests/`, and `scripts/verify.py`.

---

## Resolution

Implemented the complete internal DigiSac Contacts backfill slice.

- **Implementation:** added the typed `GET /contacts` page boundary with
  configurable `DIGISAC_CONTACT_BACKFILL_PER_PAGE` (default 5000), strict
  metadata/contact validation, `page=N` progression, global opaque
  `contact.id` deduplication with timestamp-aware duplicate selection, and the
  existing bounded Bearer/retry boundary. Added acquisition/execution in
  `src/core/digisac_contact_backfill.py`, atomic PostgreSQL publication using
  the existing contact upsert semantics plus a transaction advisory lock, and
  the internal `src.utils.backfill_digisac_contacts` CLI. No public route,
  Redis directory state, or provider write was added.
- **Tests:** added deterministic page, retry, deduplication, failure-before-
  publication, rollback, replay, concurrency, and identity-boundary tests in
  `tests/test_digisac_contact_backfill.py`; existing contact tests remained
  green.
- **Migrations:** none required; Alembic revision
  `0016_digisac_contact_identity` already provides the unique durable contact
  store and was verified at head. Invalid publication input rolls back the
  entire transaction; list absence never deletes local rows.
- **Documentation:** synchronized `SPEC-0008` status notes, `specs/README.md`,
  `IMPLEMENTATION_PLAN.md`, `PRD.md`, `ARCHITECTURE.md`, and `README.md` with
  the implemented full-backfill boundary and local-only evidence. Graphify
  metadata was refreshed with `graphify update .`.
- **Key decisions:** use the configured high page size with validated
  `page=N` fallback; acquire and validate all pages before database work; use
  PostgreSQL as the only durable authority; serialize publication with a
  transaction advisory lock; preserve data on provider/validation/commit
  failure; and make no production/provider-readiness claim.

### Validation

- `PYTHONPATH=/app python -m pytest -q tests/test_digisac_contact_backfill.py tests/test_digisac_contact_identity.py` — **18 passed, 6 skipped**.
- `PYTHONPATH=/app python scripts/verify.py` — compileall **PASS**; Pyright **PASS** (0 errors, 0 warnings, 0 informations); offline pytest **169 passed, 42 skipped**; Alembic head **0016_digisac_contact_identity PASS**; PostgreSQL 16 pytest **42 passed, 169 deselected**; scoped Compose cleanup **PASS**.
- `git diff --check` — **PASS**.
- `graphify update .` — **PASS**; Graphify reported existing zero-node `pyrightconfig.json` and missing `tree_sitter_sql` warnings, while rebuilding the graph successfully.
