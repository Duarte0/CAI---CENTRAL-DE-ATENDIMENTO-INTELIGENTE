---
id: 0021
title: "Keep Acessórias payload-load failures retryable before POST"
type: bug
status: closed
priority: high
phase: 4
created_at: 2026-08-17
updated_at: 2026-08-17
closed_at: 2026-08-17
related_issues:
  - "0017"
  - "0018"
blocked_by: []
affects:
  - src/core/acessorias_requests.py
  - tests/test_acessorias_requests.py
  - specs/0011-durable-acessorias-request-creation.md
  - README.md
  - PRD.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
---

## Description

The durable Acessórias Request orchestrator records that the provider POST has
started before it loads the persisted payload. If that database read fails, no
provider call has occurred, but the operation remains `attempting` with a
non-null `post_started_at`. Lease recovery then classifies the operation as
post-send uncertainty and moves it to `reconciliation_required`, blocking a
safe retry and requiring manual intervention for an operation that never
reached Acessórias.

**Plan/spec references:** `IMPLEMENTATION_PLAN.md`, Milestone E — Durable
Acessórias Request Creation; SPEC-0011 v1.2 §§5.3–5.7, especially the rule that
a crash before POST is safe to retry and that only a possibly processed POST
requires reconciliation; PRD §§5.5 and 8; ARCHITECTURE §§2.1 and 12.

**Dependencies:** the durable operation and claim/lease state from issue 0017
and the existing PostgreSQL cycle/classification facts. No new product,
provider, migration, or authorization decision is required.

**Root cause:** `create_request_for_cycle()` calls
`_mark_post_started_sync()` before `_load_payload_sync()`
(`src/core/acessorias_requests.py:724-726`). `_mark_post_started_sync()` sets
`post_started_at`, increments the attempt, and clears the previous failure.
`_load_payload_sync()` then performs another PostgreSQL read and can raise
before `AcessoriasRequestAdapter.create_request()` is reached. The exception
escapes the orchestration without `_finish_operation_sync()`. When the claim
expires, `_claim_operation_sync()` treats any non-null `post_started_at` as
`claim_expired_after_post_start` and persists `reconciliation_required`
(`src/core/acessorias_requests.py:537-572`).

**Reproduction:**

1. Prepare an eligible cycle with a durable Request operation in
   `not_started` state.
2. Claim the operation and make the persisted payload read fail, such as by a
   transient PostgreSQL failure between the claim and provider invocation.
3. Observe the operation after the orchestration exception: `post_started_at`
   is populated, the state is still `attempting`, and the provider was not
   called.
4. Allow the claim lease to expire and run the existing claim/recovery path.
5. Observe `reconciliation_required` rather than a safe retryable state.

The current deterministic control-flow probe produced the sequence
`post_started`, `payload_load` and no provider call when `_load_payload_sync()`
raised. The focused/full offline suite passes (189 passed, 60 skipped) because
it covers provider outcomes and claim races but does not inject a payload-load
failure between the post-start marker and the adapter call.

**Actual behaviour:** a failure before the payload is available is durably
indistinguishable from a failure after request bytes may have been sent. Lease
recovery therefore blocks an operation that has no possible remote Request.

**Expected behaviour:** the durable post-start marker must represent the point
at which the provider attempt can actually begin. A payload-load or other
failure proven to occur before provider invocation must leave the operation
safe to retry, with a sanitized failure category and no `post_started_at`.
The existing conservative reconciliation behavior must remain for failures
after the provider attempt has started, including ambiguous transport errors
covered by issue 0018.

## Scope

### In scope

- Correct the ordering and failure transition at the durable orchestration
  boundary so pre-provider failures remain safe-to-retry.
- Preserve the existing one-operation-per-cycle constraint, claim/lease
  recovery, payload fingerprint/metadata, provider fields, and all
  post-start uncertainty semantics.
- Add deterministic unit and PostgreSQL regression coverage for payload-load
  failure, lease recovery, no provider call, and the unchanged post-start
  reconciliation path.

### Out of scope

- Provider idempotency headers or parameters, automatic retries after an
  ambiguous POST, or changes to issue 0018's transport classification.
- Shared rate-limit coordination from issue 0019, department selection from
  issue 0020, Request lifecycle operations, or changes to identity/mapping
  policy.
- Schema redesign or changing the approved multipart payload, priority, type,
  credential boundary, or classification contract.

## Implementation Plan

1. Reconfirm the SPEC-0011 crash-before-POST and post-start outcome matrix and
   identify the exact durable transition boundary for a provider attempt.
2. Ensure the persisted payload is loaded and validated before recording
   `post_started_at`; if a failure occurs before provider invocation, release
   the claim using the existing safe retryable state and a sanitized category.
   Keep the marker and lease recovery conservative once the provider attempt
   can have started.
3. Preserve operation uniqueness, attempt accounting, payload fingerprint and
   metadata, `SolID` commit rules, and manual reconciliation/release guards.
   Do not add a provider idempotency mechanism or persist raw payload/error
   content.
4. Add regression coverage that injects a payload-read failure and asserts no
   provider call, no false `post_started_at`, a recoverable durable state, and
   successful later replay. Add the complementary case proving that an
   already-marked post-start claim still becomes
   `reconciliation_required` after lease expiry, and retain existing success,
   retry, ambiguity, concurrency, and sanitization coverage.
5. Run the focused and canonical verification applicable to this repository,
   then synchronize only the implementation-derived Request documentation and
   Graphify metadata required by the corrected state boundary.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migration:** no migration is expected. Existing `attempting`,
  `post_started_at`, `retryable_failure`, and `reconciliation_required` rows
  remain readable; do not clear evidence from operations that may already have
  reached the provider.
- **Compatibility:** preserve the six approved multipart fields,
  `prioridade=2`, `tipo=E`, provider URL, Bearer boundary, one operation per
  cycle, and completed replay as a no-op.
- **Integrity/concurrency:** a pre-provider failure must not consume the
  post-send uncertainty path; a concurrent worker must still be unable to
  claim the same active operation, and a post-start lease expiry must never
  trigger an automatic second POST.
- **Security/privacy:** failure categories and durable messages remain safe
  operational identifiers only. No token, header, title, description, raw
  provider body, payload, PII, or database connection detail may be persisted
  or logged.
- **Rollout:** local focused tests, the offline suite, compileall, strict
  Pyright, `git diff --check`, and disposable PostgreSQL verification are the
  acceptance boundary; these do not prove provider or production behavior.

## Tests

- **Unit:** `tests/test_acessorias_requests.py` — payload-load failure before
  provider invocation, safe state cleanup, sanitized category, and preserved
  post-start uncertainty.
- **PostgreSQL:** Request-operation tests in
  `tests/test_acessorias_requests.py` — durable lease expiry, retry replay,
  concurrency, and no duplicate provider calls.

Required validation commands:

- `PYTHONPATH=/app python -m pytest -q tests/test_acessorias_requests.py`
- `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`
- `python -m compileall -q src tests alembic scripts`
- `npx --yes pyright`
- `PYTHONPATH=/app python scripts/verify.py` when disposable PostgreSQL and
  Docker prerequisites are available; report unavailable external
  prerequisites separately.
- `git diff --check`

## Acceptance Criteria

- [x] A failure loading or validating the persisted Request payload before
  provider invocation leaves a safe retryable durable state, records a
  sanitized category, and does not retain a false `post_started_at`.
- [x] The payload-load failure path makes zero provider POST calls and a later
  eligible replay can attempt the provider exactly once under the existing
  claim/lease rules.
- [x] A claim that has a genuine non-null post-start marker still becomes
  `reconciliation_required` after lease expiry and cannot be automatically
  retried or posted a second time.
- [x] Existing successful, definitive, safe-retry, ambiguous-transport,
  `5xx`, missing-`id`, manual reconciliation, and proof-gated release behavior
  remains unchanged.
- [x] One durable operation per source cycle, payload fingerprint/metadata,
  `SolID` integrity, classification immutability, and concurrent claim safety
  remain enforced.
- [x] No token, header, raw payload/provider response, classification content,
  PII, or unsanitized database error appears in logs or durable state.
- [x] Focused tests, the applicable offline/PostgreSQL verification,
  compileall, strict Pyright, and `git diff --check` pass, with unavailable
  prerequisites reported separately from skips and passes.
- [x] SPEC-0011, its index, `README.md`, `PRD.md`, `ARCHITECTURE.md`, and
  `IMPLEMENTATION_PLAN.md` remain consistent with the corrected
  crash-before-POST state boundary; Graphify metadata is updated according to
  repository workflow.
- [x] The issue is closed only after validation and one focused commit.

## References

- **Primary contract:** `specs/0011-durable-acessorias-request-creation.md`
  v1.2, §§5.3–5.7, especially §5.7's safe crash-before-POST rule.
- **Product/architecture:** `PRD.md` §§5.5 and 8; `ARCHITECTURE.md` §§2.1 and
  12; `IMPLEMENTATION_PLAN.md`, Milestone E.
- **Related implementation:**
  `issues/0017_-_implement-durable-acessorias-request-creation.md`.
- **Non-duplicates:** issue 0018 covers ambiguous transport after a provider
  POST may have started; issue 0019 covers shared Request rate-limit state;
  issue 0020 covers cycle-scoped department assignment. This issue covers a
  false post-start state caused by a local payload-read failure before any
  provider call.
- **Source evidence:** `src/core/acessorias_requests.py`,
  `create_request_for_cycle()`, `_mark_post_started_sync()`,
  `_load_payload_sync()`, and `_claim_operation_sync()`.

---

## Resolution

- **Implementation:** moved persisted payload loading/validation ahead of
  `post_started_at`; a pre-provider payload failure now finishes the claimed
  operation as sanitized `retryable_failure/payload_load_failed` without
  incrementing provider-attempt evidence or calling Acessórias. The genuine
  post-start lease-expiry branch remains `reconciliation_required`.
- **Tests:** added deterministic unit coverage for ordering, zero provider
  calls, sanitized state, and added PostgreSQL coverage for durable replay and
  conservative marked-claim recovery.
- **Migrations:** none; the existing operation schema and state model are
  sufficient.
- **Docs:** synchronized SPEC-0011 v1.2, `specs/README.md`, `README.md`,
  `PRD.md`, `ARCHITECTURE.md`, and `IMPLEMENTATION_PLAN.md`; refreshed
  Graphify metadata with `graphify update .`.
- **Key decisions:** only the fixed operational category is persisted; raw
  load/validation exceptions are never recorded, and no provider idempotency
  or blind retry behavior was added.

### Validation

- `PYTHONPATH=/app python -m pytest -q tests/test_acessorias_requests.py` — 13 passed, 6 skipped.
- `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py` — 198 passed, 65 skipped.
- `python -m compileall -q src tests alembic scripts` — passed.
- `npx --yes pyright` — 0 errors, 0 warnings, 0 informations.
- `PYTHONPATH=/app python scripts/verify.py` — all stages passed; PostgreSQL 65 passed, 198 deselected; Alembic head `0019_acessorias_request_creation`.
- `git diff --check` — passed.

<!-- Filled by the agent on close. DO NOT edit manually. -->
