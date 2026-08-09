---
id: 0002
title: "Establish the disposable PostgreSQL verification runner"
type: feature
status: closed
priority: critical
phase: 0
created_at: 2026-08-09
updated_at: 2026-08-09
closed_at: 2026-08-09
related_issues:
  - "0001"
blocked_by: []
affects:
  - docker-compose.test.yml
  - tests/
  - alembic/
  - README.md
---

## Description

Deliver the pending Phase 0 runner from `IMPLEMENTATION_PLAN.md` item 2: one
versioned, local command that proves the canonical test process can reach an
isolated PostgreSQL 16 target, migrates it to Alembic head, and runs the full
verification matrix without using a developer or production database.

**Verified gap:** `docker-compose.test.yml` supplies a disposable PostgreSQL
16 service but hard-codes host port `5433`. The documented manual sequence
exports `CAI_TEST_DATABASE_URL` for that port, while the database fixture reads
the value during collection, applies Alembic head, and truncates its target
before each database test. There is no versioned runner or CI configuration,
and the local offline result remains **120 passed, 28 skipped** because no
reachable disposable URL was supplied. A syntactically valid/healthy Compose
container is insufficient: the test process itself must connect successfully.

Expected outcome: a clean checkout can execute all canonical static, offline,
and PostgreSQL integration stages against a demonstrably disposable target,
with clear distinction between passed stages, skipped tests, and unavailable
prerequisites. `tests/test_webhook_local.py` remains opt-in.

## Scope

### In scope

- Add a versioned local verification runner and the minimum test-Compose
  changes needed to create, wait for, connect to, and tear down PostgreSQL 16
  safely.
- Remove the fixed-host-port assumption while preserving an explicit,
  documented host-process connection form and the Docker-network connection
  form when applicable.
- Ensure the runner supplies `CAI_TEST_DATABASE_URL` before pytest collection,
  verifies the target is disposable, and proves Alembic head plus the database
  families from the same process that executes pytest.
- Run and report compileall, strict Pyright, offline tests, and PostgreSQL
  tests as distinct canonical stages; update the versioned verification
  documentation and plan status only after actual successful evidence.

### Out of scope

- Adding a hosted CI provider, production deployment automation, or a
  production database migration.
- Changing application behavior, durable schema contracts, business rules, or
  the live webhook test's opt-in status.
- Investigating a database-family test failure as an application defect before
  runner connectivity and the disposable target are confirmed.
- The approved legacy-finalization removal refactor and raw-payload diagnostic
  policy decision.

## Implementation Plan

1. Inventory the current Compose test service, Alembic environment, fixture
   lifecycle, and all PostgreSQL-marked/module-selected tests. Preserve the
   fixture invariant that `CAI_TEST_DATABASE_URL` is read before collection and
   its `TRUNCATE ... RESTART IDENTITY CASCADE` target must be disposable.
2. Introduce one repository-owned runner that creates an isolated Compose
   project/service, waits for PostgreSQL readiness, obtains or selects a
   collision-free host endpoint, and exports a PostgreSQL URL only for the
   runner's test process. It must always attempt scoped teardown, including on
   a failed canonical stage, without touching unrelated Compose projects,
   volumes, or databases.
3. Before the integration suite, prove host-to-container connectivity using
   the same URL supplied to pytest and apply/verify Alembic `head`. Do not
   accept container-only health as success. Keep Alembic driven by the test
   URL rather than a developer `.env` or `DATABASE_URL` target.
4. Execute canonical stages in order: compile `src`, `tests`, and `alembic`;
   strict Pyright; offline pytest with persistent finalization explicitly set
   and the live webhook test excluded; then all PostgreSQL families with
   `CAI_TEST_DATABASE_URL`. Each failed stage fails the runner. Preserve
   machine-readable or plainly separated reporting for pass, skip, and missing
   prerequisite outcomes so a skip cannot be mistaken for schema/runtime
   verification.
5. Add focused runner coverage where practical for endpoint selection,
   cleanup-on-failure, required environment propagation, and refusal to use an
   unsafe external database URL. Execute the full runner from a clean relevant
   environment, record only observed counts/results, update README and
   `IMPLEMENTATION_PLAN.md`, run `graphify update .`, and close with one focused
   commit.

## Data, compatibility, security, observability, and rollout

- **Data/migrations:** Alembic head may run only against the runner-created
  ephemeral PostgreSQL 16 target. No active deployment, backfill, or production
  database is in scope. The runner must not present fixture truncation as safe
  until target isolation is demonstrated.
- **Compatibility:** retain the existing offline command semantics and explicit
  persistent-finalization selection. The live local webhook test stays outside
  canonical automation.
- **Security/configuration:** do not read, print, or persist developer or
  production database URLs/secrets. Scope Compose naming and cleanup to the
  runner-owned test service; credentials may remain test-only.
- **Observability:** surface the resolved safe connection mode without leaking
  secrets; report readiness, migration, static, offline, and PostgreSQL-stage
  outcomes separately.
- **Rollout:** local runner is the approved canonical mechanism; hosted CI is
  a later optional enhancement, not a prerequisite for this slice.

## Tests

- **Runner integration:** invoke the versioned runner from the host test
  process and verify it reaches PostgreSQL 16, applies Alembic head, and runs
  every database-backed family rather than producing prerequisite skips.
- **Static:** `python -m compileall -q src tests alembic` and `npx --yes pyright`.
- **Offline:** run the documented canonical suite with
  `DIGISAC_HISTORY_FINALIZATION_ENABLED=true` and
  `--ignore=tests/test_webhook_local.py`; retain its result separately from the
  PostgreSQL stage.
- **Safety/negative:** verify unavailable Docker/PostgreSQL, an occupied
  preferred host port, a failed stage, and an unsafe/non-runner database target
  yield a clear failure with scoped cleanup and do not claim integration
  success.

## Acceptance Criteria

- [x] A versioned local runner starts an isolated disposable PostgreSQL 16
  service without relying on fixed host port `5433` or any developer/production
  database endpoint.
- [x] The runner makes `CAI_TEST_DATABASE_URL` available before pytest
  collection, and the host-side test process successfully connects using that
  exact URL; container health alone is not accepted as verification. The
  containerized runner uses the documented Docker-network form when the host
  loopback is not reachable.
- [x] Alembic reaches and verifies `head` on the disposable target before the
  PostgreSQL suite, and the fixture's truncate lifecycle operates only on that
  demonstrated target.
- [x] Compileall, zero-diagnostic Pyright, offline pytest with explicit
  persistent mode, and all PostgreSQL test families are canonical, separately
  reported stages; failure of any stage fails the runner.
- [x] PostgreSQL tests no longer report `CAI_TEST_DATABASE_URL` prerequisite
  skips in a successful runner execution, while pass, skip, and unavailable
  prerequisite outcomes remain unambiguous.
- [x] `tests/test_webhook_local.py` remains excluded unless a local API is
  intentionally started.
- [x] Occupied ports, unavailable prerequisites, runner-stage failures, and
  unsafe target selection fail safely, clean up only runner-owned resources,
  and never claim migration/runtime success.
- [x] README and `IMPLEMENTATION_PLAN.md` document the actual runner command,
  safe connection forms, expected outcomes, and any verified evidence; no
  documentation claims unexecuted runtime verification.
- [x] `graphify update .` completes after the implementation changes, the plan
  is synchronized on closure, and the work closes in one focused commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — Phase 0, item 2 (selected); Phase 1 item 4
  is a follow-up that depends on this runner.
- Primary spec: `specs/0004-reproducible-verification-baseline.md` v1.2,
  requirements 4–6 and acceptance criteria.
- Related contracts: `specs/0001-shared-data-and-analysis-contract.md` —
  Tests e aceitação; `specs/0002-digisac-webhook-and-query-api.md` — Segurança,
  observabilidade, testes e aceitação; and
  `specs/0003-durable-finalization-and-media.md` — Remoção do legado,
  observabilidade e verificação.
- Dependency evidence: issue `0001` is closed and explicitly defers this
  disposable runner; no open or in-progress issue covers the same outcome.
- Current evidence: `docker-compose.test.yml`, `tests/conftest.py`,
  `alembic/env.py`, `src/core/db.py`, `README.md` — Testes e validação, and the
  PostgreSQL-marked test families.

---

## Resolution
Implemented the disposable PostgreSQL verification slice without changing
application behavior or durable schema:

- `scripts/verify.py` now owns the ordered compileall, Pyright, offline,
  connectivity, Alembic, and PostgreSQL stages. It creates a unique Compose
  project, validates PostgreSQL 16 and the test database identity from the
  process that runs the tests, injects the exact target URL before collection,
  rejects unsafe URLs, and always performs scoped cleanup.
- `docker-compose.test.yml` publishes PostgreSQL with host port `0` instead of
  fixed `5433`. The runner uses the dynamic loopback endpoint on a normal host
  and the explicit `postgres-test:5432` network form when the runner is inside
  a container whose host loopback is unavailable.
- `tests/test_verification_runner.py` covers endpoint parsing, target safety,
  environment propagation, and both connection forms. `test_webhook_local.py`
  remains excluded.

Validation executed:

- `PYTHONPATH=/app pytest -q tests/test_verification_runner.py` — **8 passed**.
- `python -m compileall -q src tests alembic scripts` — passed.
- `npx --yes pyright` — **0 errors, 0 warnings, 0 informations**.
- `docker compose -f docker-compose.test.yml config -q` — passed.
- `python scripts/verify.py` — compileall and Pyright passed; offline stage
  **128 passed, 28 skipped**; Docker-network PostgreSQL 16 connectivity,
  Alembic **0014_retry_scheduling**, and PostgreSQL stage **28 passed, 128
  deselected**. No PostgreSQL prerequisite skips were reported.
- `docker compose -p cai ps` after verification — the active `cai` project
  remained running and was not part of runner cleanup.

No migration was added or run against active data. Hosted CI remains out of
scope; Phase 1 item 4 owns broader operational verification.
