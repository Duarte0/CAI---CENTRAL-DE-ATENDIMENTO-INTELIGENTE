---
id: 0001
title: "Isolate the persistent canonical test suite"
type: refactor
status: closed
priority: critical
phase: 0
created_at: 2026-08-09
updated_at: 2026-08-09
closed_at: 2026-08-09
related_issues: []
blocked_by: []
affects:
  - tests/
  - src/core/config.py
  - src/api/routes.py
  - src/workers/ia_worker.py
  - README.md
---

## Description

Implement the remaining test-isolation portion of `IMPLEMENTATION_PLAN.md` item
1 so the canonical offline suite exercises the approved persistent
finalization path without inheriting a developer's `.env`.

**Verified gap:** all 27 current `tests/` files are already tracked and the
current `.gitignore` no longer ignores `tests/*`; that part of SPEC-0004 is
implemented.  The shared fixture reads `CAI_TEST_DATABASE_URL` at import time
but does not explicitly select `DIGISAC_HISTORY_FINALIZATION_ENABLED`, while
`tests/test_ticket_closure.py` still asserts Redis-buffer/debounce keys,
`task_token` jobs, and the legacy branch of `IAWorker`.  Those assumptions are
incompatible with the approved removal of legacy finalization and with an
offline persistent-mode baseline.

Expected outcome: a clean checkout's offline command has one explicit,
persistent finalization mode and tracked tests cover the equivalent durable
cycle behavior.  The disposable PostgreSQL runner, host-port resolution, and
execution of PostgreSQL families are deliberately deferred to plan item 2.

## Scope

### In scope

- Make test configuration select the persistent finalization mode explicitly
  and restore any mutated process/settings state between tests.
- Remove or replace legacy Redis-buffer ticket-closure coverage with focused,
  tracked persistent-cycle coverage for the same relevant webhook and worker
  invariants.
- Keep the canonical offline selection independent of a personal `.env` and
  retain `test_webhook_local.py` as a deliberately opt-in live test.
- Update the versioned test documentation and plan status when closing, based
  on the verified commands and their results.

### Out of scope

- Removing the legacy production implementation, flag, Redis keys, or worker
  branch; that approved refactor needs a separately scoped follow-up.
- Creating CI, changing `docker-compose.test.yml`, resolving its host-port
  collision, or running PostgreSQL integration families (plan item 2).
- Application behavior changes, Alembic migrations, production data, and
  debug-surface/security policy work.

## Implementation Plan

1. Inventory the existing offline and PostgreSQL-marked tests before editing.
   Preserve the current tracked-suite policy; do not reintroduce a blanket
   ignore rule or make `test_webhook_local.py` canonical.
2. Establish one test-owned configuration boundary that explicitly selects
   `DIGISAC_HISTORY_FINALIZATION_ENABLED=true` before code using settings is
   exercised, and restores environment/settings state after each affected test.
   The offline result must be invariant whether a developer `.env` sets the
   flag to true or false.
3. Replace the legacy closure scenarios with persistent-cycle scenarios using
   the actual webhook contract: valid close/reopen events create or deduplicate
   durable cycles, preserve the required protocol/identity behavior, publish
   only the cycle-shaped work that the persistent worker accepts, and avoid
   asserting `buffer:*`, debounce, or `task_token` semantics.  Keep negative
   validation and bot-message behavior only where it remains valid for the
   persistent route.
4. Retain concurrency/idempotency coverage at the durable boundary.  A
   duplicate event must not create a second terminal cycle or classification;
   failures/retries must retain durable recoverability rather than fall back to
   Redis-buffer state.  Do not fake the PostgreSQL boundary in a way that
   claims integration coverage without `CAI_TEST_DATABASE_URL`.
5. Run the required offline checks with both conflicting external flag values
   to demonstrate fixture isolation.  Record only actually executed results,
   update the relevant README verification instructions and
   `IMPLEMENTATION_PLAN.md`, run `graphify update .`, then close via one
   focused commit.

## Data, compatibility, security, observability, and rollout

- **Data/migrations:** none; this slice must not alter durable schema or run
  migrations against an active deployment.
- **Compatibility:** persistent finalization is the test contract.  Do not add
  new support for the deprecated legacy mode while replacing its tests.
- **Security/configuration:** test setup must not read or disclose personal
  `.env` values; the opt-in live webhook test remains excluded unless its API
  is deliberately started.
- **Observability:** preserve assertions for durable cycle identity and
  idempotent queue publication where the persistent flow exposes them.
- **Rollout:** no production rollout.  The follow-up PostgreSQL runner must
  prove that any database URL subject to fixture `TRUNCATE` is disposable.

## Tests

- **Unit/offline:** `PYTHONPATH=/app DIGISAC_HISTORY_FINALIZATION_ENABLED=true pytest -q --ignore=tests/test_webhook_local.py`
- **Isolation regression:** repeat the same command with
  `DIGISAC_HISTORY_FINALIZATION_ENABLED=false`; the test-owned persistent mode
  must yield the same offline outcome.
- **Static:** `python -m compileall -q src tests alembic` and
  `npx --yes pyright`.
- **Deferred integration:** PostgreSQL-marked families remain the responsibility
  of the isolated runner in plan item 2; skipped results must not be presented
  as migration or worker-runtime verification.

## Acceptance Criteria

- [x] The canonical offline suite explicitly selects persistent finalization
  and does not inherit `DIGISAC_HISTORY_FINALIZATION_ENABLED` from `.env`.
- [x] Conflicting externally supplied values for that flag produce the same
  canonical offline test result, and test configuration is restored so it does
  not leak to other tests.
- [x] No canonical tracked test asserts Redis-buffer/debounce/`task_token`
  behavior as the expected finalization path.
- [x] Replacement coverage verifies persistent close/reopen and duplicate-event
  behavior, including durable cycle identity and idempotency at the applicable
  boundary.
- [x] Negative webhook behavior and bot filtering remain covered only according
  to the persistent route's current contract.
- [x] `test_webhook_local.py` remains excluded from canonical automation unless
  a local API is intentionally running.
- [x] `python -m compileall -q src tests alembic` completes successfully.
- [x] `npx --yes pyright` completes with zero diagnostics.
- [x] The documented offline command passes without personal `.env` input;
  PostgreSQL skips, if the disposable runner is unavailable, are reported
  separately and not treated as integration success.
- [x] README verification guidance and `IMPLEMENTATION_PLAN.md` accurately
  record the canonical command and evidence; `graphify update .` is run after
  the code/test changes.
- [x] The issue is closed only after the plan sync and one focused commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — Phase 0, item 1; follow-up dependency is
  item 2 (executable PostgreSQL verification runner).
- Primary spec: `specs/0004-reproducible-verification-baseline.md` v1.1,
  requirements 1–3 and acceptance criteria.
- Related contracts: `specs/0001-shared-data-and-analysis-contract.md` §Tests
  e aceitação; `specs/0002-digisac-webhook-and-query-api.md` §Segurança,
  observabilidade, testes e aceitação; and
  `specs/0003-durable-finalization-and-media.md` §Remoção do legado,
  observabilidade e verificação.
- Current evidence: `tests/conftest.py`, `tests/test_ticket_closure.py`,
  `tests/test_history_finalization_webhook.py`,
  `tests/test_conversation_cycles_db.py`, `.gitignore`, and
  `docker-compose.test.yml`.

---

## Resolution

Implemented the test-isolation slice of SPEC-0004 without changing application
behavior or schema:

- `tests/conftest.py` now owns the persistent finalization mode for every test,
  explicitly overrides external `.env`/process values, and restores the mode and
  `DATABASE_URL` state after each test.
- `tests/test_ticket_closure.py` replaces Redis-buffer/debounce/task-token
  expectations with persistent webhook coverage for close, reopen, duplicate
  cycle identity/publication, bot and negative validation, and recovery after a
  queue-publication failure. The live webhook test remains opt-in.
- README, SPEC-0004, `specs/README.md`, and `IMPLEMENTATION_PLAN.md` now record
  the canonical commands and the separate PostgreSQL prerequisite status.

Validation executed:

- `PYTHONPATH=/app DIGISAC_HISTORY_FINALIZATION_ENABLED=true pytest -q --ignore=tests/test_webhook_local.py` — **120 passed, 28 skipped**.
- Same command with `DIGISAC_HISTORY_FINALIZATION_ENABLED=false` — **120 passed, 28 skipped**.
- `python -m compileall -q src tests alembic` — passed.
- `npx --yes pyright` — 0 diagnostics.
- `graphify update .` — completed after the changes.

No migration was added or run against active data. PostgreSQL-marked tests were
skipped because `CAI_TEST_DATABASE_URL` was unavailable; their disposable runner
and runtime verification remain the next plan item.
