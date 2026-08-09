---
id: 0004
title: "Verify durable operational recovery on the disposable runner"
type: refactor
status: closed
priority: high
phase: 1
created_at: 2026-08-09
updated_at: 2026-08-09
closed_at: 2026-08-09
related_issues:
  - "0002"
blocked_by: []
affects:
  - tests/
  - scripts/verify.py
  - README.md
  - IMPLEMENTATION_PLAN.md
---

## Description

Complete `IMPLEMENTATION_PLAN.md` Phase 1 item 4 by extending the existing
runner-owned PostgreSQL verification from persistence-unit coverage to
operational recovery paths that cross durable state and queue publication.

**Verified gap:** `scripts/verify.py` already creates a reachable disposable
PostgreSQL 16 target, applies Alembic `0014_retry_scheduling`, and runs the
28 current `postgres` tests. Those tests prove individual cycle claims,
durable media scheduling, and an image-blocked wake-up; the current
publication-recovery and queue-deduplication checks use isolated transport
doubles and do not prove the worker reconciliation contract against the
runner-owned durable state. The pending plan item explicitly requires broader
checks for cycle claim/lease, publication recovery, due-media wake-up,
blocked-image behavior, and idempotent queue publication.

Expected outcome: one reproducible PostgreSQL-marked operational test slice
runs through the canonical disposable runner and proves that recovery remains
durable, due-only, and non-duplicating when queue publication succeeds, is
already present, or fails. This issue does not change production behavior,
schema, retry policy, or the approved legacy-removal refactor.

## Scope

### In scope

- Add PostgreSQL-backed operational tests to the existing disposable database
  family, using a deterministic Redis-compatible test transport only for queue
  observation/failure simulation.
- Exercise reconciliation of persisted cycles and media in conjunction with
  their real claim, lease, `enqueued_at`, `next_attempt_at`, and release
  operations.
- Ensure the canonical runner selects the new tests as part of its existing
  PostgreSQL stage, and document only the verification evidence actually run.
- Synchronize `IMPLEMENTATION_PLAN.md` and Graphify metadata when closing, and
  close in one focused commit.

### Out of scope

- Any application, API, worker-production, Redis deployment, Compose-service,
  migration, backfill, or production-data change.
- Removing the feature flag or Redis-buffer legacy finalization path; that is
  Phase 2 item 8's separately scoped refactor.
- Provider calls, broad dead-letter recovery, altered retry/backoff values, or
  the raw-payload diagnostic policy blocked by Phase 1 item 5.

## Implementation Plan

1. Start from the current PostgreSQL-marked cycle and media tests and the
   reconciliation methods used by the IA, audio, and image workers. Keep the
   runner's isolated target and fixture truncation boundary unchanged; do not
   substitute a developer or active database.
2. Build a minimal deterministic queue transport in the test suite that can
   inspect enqueued jobs and simulate a publish failure without calling Redis.
   It must model only the operations exercised by reconciliation and must not
   become a second implementation of worker logic.
3. Add an operational scenario that persists a due cycle, reconciles it via
   the real cycle-recovery query, and verifies one eligible publication under
   concurrent recovery attempts. A prior `enqueued_at`, a future
   `next_attempt_at`, or an active lease must prevent a second publication.
4. Add recovery-failure coverage: when queue publication raises, the matching
   persistent publication marker is released so a later eligible reconciliation
   can publish exactly once; the cycle is neither deleted nor terminally
   classified by the recovery path.
5. Cover the media side with real durable reservations: only due stale media
   is recovered, an existing queue entry is not duplicated, and a successful
   image recovery wakes only its dependent `media_blocked` cycle. Preserve the
   invariant that a terminally failed image does not allow that dependent cycle
   to classify.
6. Confirm the new tests carry the existing `postgres` selection contract so
   `PYTHONPATH=/app python scripts/verify.py` runs them after Alembic head.
   Update the verification/documentation evidence and plan status only after
   the required commands pass; run `graphify update .` after the implementation
   changes.

## Data, compatibility, security, observability, and rollout

- **Data/migrations:** no schema change, backfill, or active-target migration.
  Tests may mutate only the runner-owned disposable database and its fixture
  lifecycle must continue to truncate only that proved target.
- **Compatibility:** retain the current persistent queue payload and state
  contracts. Do not make new public API or Redis compatibility promises.
- **Security:** queue fixtures, test failures, assertions, and documentation
  must use safe IDs and sanitized error text; never place raw webhook bodies,
  secrets, tokens, signed URLs, or media binaries in durable test records.
- **Observability:** assertions should make cycle/media identifiers and status
  transitions sufficient to diagnose a failed recovery without asserting on
  sensitive history content.
- **Rollout:** this is local disposable verification only. Do not claim Redis
  production, provider, multi-replica, or production-database verification.

## Tests

- **Focused PostgreSQL operational tests:** run the new PostgreSQL-marked
  recovery scenarios with `CAI_TEST_DATABASE_URL` supplied by the disposable
  runner; include concurrent claim/reconcile, publish failure then retry,
  due/future scheduling, existing queue entry, and image-blocked wake-up
  negatives.
- **Canonical runner:** `PYTHONPATH=/app python scripts/verify.py`.
- **Static:** `python -m compileall -q src tests alembic scripts` and
  `npx --yes pyright`.
- **Graph:** `graphify update .` after implementation changes.

## Acceptance Criteria

- [x] The runner-owned PostgreSQL stage executes a focused operational
  recovery test slice after Alembic head, without a developer, active, or
  production database target.
- [x] Concurrent eligible cycle recovery claims result in one queue job and
  one durable publication marker; active lease, existing marker, and future
  `next_attempt_at` each prevent duplicate or premature publication.
- [x] A queue publication failure releases only the matching cycle's durable
  publication claim, preserves the cycle for a later due reconciliation, and
  a subsequent successful reconciliation produces one job without a duplicate
  terminal classification.
- [x] Due audio/image recovery uses the persisted reservation and schedule;
  an already queued matching media job is not republished, and a future job is
  not claimed early.
- [x] A terminally failed image keeps dependent cycles `media_blocked` and
  unclassified; when that image becomes recoverable/successful, only dependent
  blocked cycles become eligible again.
- [x] The tests use safe synthetic identifiers and sanitized errors, preserve
  PostgreSQL as durable authority and Redis as transient transport, and make
  no production behavior, migration, retry-policy, or diagnostic-surface
  change.
- [x] Focused PostgreSQL tests, compileall, Pyright, and
  `PYTHONPATH=/app python scripts/verify.py` pass with results recorded
  accurately; `tests/test_webhook_local.py` remains opt-in.
- [x] README evidence (if affected), `IMPLEMENTATION_PLAN.md`, and Graphify
  metadata are synchronized on closure, and the work is closed in one focused
  commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — Phase 1, item 4 (selected).
- Primary specification: `specs/0003-durable-finalization-and-media.md` v1.2
  — persistent-cycle concurrency, media recovery, and verification.
- Related specifications: `specs/0001-shared-data-and-analysis-contract.md`
  v1.1 — durable authority and idempotency; and
  `specs/0002-digisac-webhook-and-query-api.md` v1.3 — reserve-before-publish
  and publication-failure release contract.
- Completed dependency: issue `0002` provides the runner-owned PostgreSQL 16
  target, process-level connectivity, Alembic-head check, and canonical
  PostgreSQL stage. No open or in-progress issue covers this operational
  recovery outcome.
- Current evidence: `scripts/verify.py`; `tests/test_conversation_cycles_db.py`;
  `tests/test_postgres_evolution.py`; `tests/test_media_scheduling.py`;
  `src/core/db.py`; `src/workers/ia_worker.py`; the audio/image workers; and
  `tests/test_operational_recovery_db.py`.

---

## Resolution

Implemented the durable operational recovery verification slice without
changing production code, migrations, retry policy, Compose services, or active
data:

- added five PostgreSQL-marked scenarios using a minimal deterministic queue
  transport for concurrent cycle reconciliation, publication failure/retry,
  due-only audio/image recovery, queue deduplication, and selective image
  blocked-cycle wake-up;
- confirmed the existing canonical runner selects the new module after Alembic
  head and uses only its disposable PostgreSQL 16 target; and
- synchronized README, PRD, architecture, SPEC-0004 v1.3, `specs/README.md`,
  and `IMPLEMENTATION_PLAN.md` with the observed evidence.

Validation:

- `PYTHONPATH=/app pytest -q tests/test_operational_recovery_db.py` — **5
  skipped** without `CAI_TEST_DATABASE_URL` (expected prerequisite behavior);
- `PYTHONPATH=/app DIGISAC_HISTORY_FINALIZATION_ENABLED=true pytest -q
  --ignore=tests/test_webhook_local.py` — **128 passed, 33 skipped**;
- `PYTHONPATH=/app python scripts/verify.py` — compileall passed, Pyright
  reported **0 errors, 0 warnings, 0 informations**, offline pytest passed
  (**128 passed, 33 skipped**), runner-owned PostgreSQL 16 connectivity and
  Alembic `0014_retry_scheduling` passed, PostgreSQL pytest passed (**33
  passed, 128 deselected**), and scoped Compose resources were removed; and
- `graphify update .` — passed after the implementation and documentation
  changes.

No production database, credentials, provider, Redis deployment, or live
webhook test was used. `tests/test_webhook_local.py` remains opt-in.
