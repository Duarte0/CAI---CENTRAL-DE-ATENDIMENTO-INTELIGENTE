---
id: 0003
title: "Reconcile the versioned documentation baseline"
type: spec
status: closed
priority: critical
phase: 0
created_at: 2026-08-09
updated_at: 2026-08-09
closed_at: 2026-08-09
related_issues:
  - "0001"
  - "0002"
blocked_by: []
affects:
  - README.md
  - PRD.md
  - ARCHITECTURE.md
  - specs/
  - IMPLEMENTATION_PLAN.md
---

## Description

Complete `IMPLEMENTATION_PLAN.md` Phase 0, item 3 by making the versioned
README, PRD, architecture, and active specification index describe one
implementation-derived baseline. This is a documentation/specification slice;
it must report verified behavior and observed verification evidence without
claiming an unexecuted production rollout or changing application behavior.

**Verified gap:** Phase 0 items 1 and 2 are closed and `scripts/verify.py`
now runs the offline suite, a runner-owned PostgreSQL 16 target, Alembic head,
and PostgreSQL-marked tests. However, `PRD.md` §9 and `ARCHITECTURE.md` §13
still describe unversioned/legacy test limitations that the closed issues and
SPEC-0004 v1.2 superseded. README and architecture wording also mix the
currently feature-flagged legacy implementation with the approved future
removal, while the mounted `POST /webhook/debug` behavior must be described as
an internal raw-payload diagnostic exception without broadening its exposure.

Expected outcome: operators and implementers can identify the real runtime
contracts, the canonical disposable verification command and its evidence, the
approved-but-not-yet-implemented legacy removal, and the remaining blocked
diagnostic-policy decision from the versioned documents alone.

## Scope

### In scope

- Reconcile `README.md`, `PRD.md`, `ARCHITECTURE.md`, `specs/README.md`, and
  active SPEC-0001 through SPEC-0004 where their implementation status,
  verification evidence, routing, configuration, or compatibility wording is
  stale or contradictory.
- Derive route, configuration, finalization, storage, media, and verification
  statements from current source, migrations, tracked tests, and the observed
  runner evidence; preserve the source-of-truth precedence in
  `OPERATING_PRINCIPLES.md`.
- State the mounted `/webhook/debug` contract accurately as an internal,
  HMAC-validated, no-write diagnostic response that includes `raw_payload`,
  while retaining the explicit Phase 1 item 5 security/policy limitation.
- Synchronize `IMPLEMENTATION_PLAN.md` and required Graphify metadata after
  the documentation work is verified, then close this issue in one focused
  commit.

### Out of scope

- Removing the feature flag, legacy Redis-buffer keys, debounce behavior, or
  legacy IA-worker path; that approved refactor remains a separate follow-up.
- Mounting, exposing, sanitizing, logging, retaining, or otherwise changing
  either raw-payload diagnostic surface before the product/security decision in
  Phase 1 item 5.
- Application, test, migration, Compose, runner, production-data, CI, or
  deployment changes; document only behavior actually supported by the current
  checkout and observed local verification.

## Implementation Plan

1. Re-inventory the versioned documents against `src/api/routes.py`,
   `src/core/config.py`, `src/core/db.py`, finalization/media modules, Alembic
   revisions, `scripts/verify.py`, Compose files, and the tracked test matrix.
   Record every material disagreement and resolve it according to the
   repository source-of-truth order; distinguish implemented behavior, approved
   future work, and unverified runtime/production claims.
2. Update README operational guidance and API/configuration tables to name the
   canonical runner, its disposable-target safety boundary, separate offline
   versus PostgreSQL evidence, opt-in live webhook test, durable PostgreSQL
   authority, and the feature-flagged legacy path without presenting removal as
   already implemented. Keep commands and defaults tied to code rather than a
   personal `.env` or developer database.
3. Reconcile PRD and architecture status/known-gap sections with the closed
   Phase 0 work. Preserve the current `/webhook/debug` response truthfully,
   prohibit copying raw payloads into normal logs/snapshots/queues, and point
   to the unresolved least-privilege, redaction, retention, and audience
   decision instead of inventing a public security contract.
4. Align the specification index and SPEC-0001–0004 traceability, status,
   compatibility language, and acceptance/evidence references with the same
   baseline. Do not turn the approved legacy-removal decision into a claim that
   the code path no longer exists; retain an explicit implementation follow-up.
5. Run documentation-link/reference checks and the documented canonical
   verification appropriate to documentation-only changes. Record only results
   actually executed, update `IMPLEMENTATION_PLAN.md`, run `graphify update .`,
   and close through one focused commit.

## Data, compatibility, security, observability, and rollout

- **Data/migrations:** none. Do not run migrations, backfills, or recovery
  commands against an active target for this issue.
- **Compatibility:** document the actual feature-flagged legacy behavior and
  approved removal separately; preserve existing API paths and `/v1/` consumer
  compatibility statements.
- **Security:** neither docs nor examples may reveal secrets, signed URLs,
  media binaries, or raw bodies outside the explicitly internal diagnostic
  response. The issue must not resolve the blocked access/redaction/retention
  decision by assumption.
- **Observability:** retain the distinction between sanitized operational
  logging and the mounted internal diagnostic response, and between offline,
  disposable-PostgreSQL, and production/runtime evidence.
- **Rollout:** documentation must not claim production verification. Hosted CI
  remains optional and outside this slice.

## Tests

- **Documentation consistency:** search all versioned documentation for stale
  Phase 0 counts/statuses, legacy-removal claims, route paths, command names,
  and raw-payload exposure wording; validate every changed statement against
  its cited source or observed evidence.
- **Canonical verification:** `PYTHONPATH=/app python scripts/verify.py` when
  Docker is available; otherwise run the safe static/offline subset and report
  the unavailable PostgreSQL stage separately rather than claiming it passed.
- **Static:** `python -m compileall -q src tests alembic scripts` and
  `npx --yes pyright` if documentation changes require refreshed evidence.
- **Graph:** `graphify update .` after the documentation/specification changes.

## Acceptance Criteria

- [x] README, PRD, architecture, specs, and implementation plan have one
  internally consistent implementation-derived baseline for Phase 0 status,
  canonical verification, and observed evidence.
- [x] Documentation distinguishes the offline suite from the runner-owned
  disposable PostgreSQL stage and does not present skips, container health, or
  unexecuted production work as schema/runtime verification.
- [x] Route, API version, HMAC, storage-authority, configuration, media, and
  finalization statements changed by this issue match current code, migrations,
  and tracked tests.
- [x] The mounted `/webhook/debug` response is accurately limited to its
  internal, HMAC-validated, no-write diagnostic contract; normal logs,
  snapshots, queues, and public-facing documentation do not gain raw-payload
  exposure, and Phase 1 item 5 remains explicitly blocked.
- [x] Documentation distinguishes the still-implemented feature-flagged legacy
  path from its approved removal; it neither claims the removal is complete nor
  adds compatibility promises beyond current behavior.
- [x] No secrets, signed URLs, binary media, production database targets, or
  fabricated verification results appear in versioned documentation.
- [x] Changed commands, links, identifiers, versions, references, and statuses
  are verified; required static/canonical results are recorded honestly.
- [x] `IMPLEMENTATION_PLAN.md` is synchronized, `graphify update .` completes,
  and the issue closes only after one focused documentation commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — Phase 0, item 3 (selected); Phase 1 item 5
  remains a blocked security/product dependency for any diagnostic-surface
  expansion.
- Primary specifications: `specs/0001-shared-data-and-analysis-contract.md`
  v1.1; `specs/0002-digisac-webhook-and-query-api.md` v1.3;
  `specs/0003-durable-finalization-and-media.md` v1.2; and
  `specs/0004-reproducible-verification-baseline.md` v1.2.
- Completed dependencies: issues `0001` and `0002`; no open or in-progress
  issue covers the documentation-baseline outcome.
- Current evidence: `README.md` — Modos de finalização, API, and Testes e
  validação; `PRD.md` §§5, 7–10; `ARCHITECTURE.md` §§3–5 and 10–13;
  `src/api/routes.py`; `src/core/config.py`; `scripts/verify.py`; and
  `tests/conftest.py` / `tests/test_verification_runner.py`.

---

## Resolution

Implemented the documentation-baseline reconciliation without changing source,
tests, migrations, Compose, or runtime behavior:

- reconciled README, PRD, architecture, `specs/README.md`, SPEC-0002,
  SPEC-0003, and `IMPLEMENTATION_PLAN.md` with the current route/API version,
  storage authority, runner evidence, and configuration behavior;
- documented the mounted `/webhook/debug` response as an internal,
  HMAC-validated-when-configured, no-write diagnostic exception that currently
  returns `raw_payload`, while keeping the unmounted handler outside the
  contract and Phase 1 item 5 open;
- distinguished the still-implemented feature-flagged legacy finalization path
  from its approved future removal; and
- corrected the prior `/v1/` documentation claim: query routes are currently
  mounted without a version prefix in `src/api/routes.py`; future versioned
  compatibility remains a policy target outside this issue; and
- recorded the observed local disposable verification evidence without claiming
  production availability.

Validation executed:

- `PYTHONPATH=/app pytest -q tests/test_verification_runner.py` — **8 passed**;
- `python -m compileall -q src tests alembic scripts` — passed;
- `npx --yes pyright` — **0 errors, 0 warnings, 0 informations**;
- `PYTHONPATH=/app python scripts/verify.py` — compileall, Pyright, offline
  **128 passed, 28 skipped**, Docker-network PostgreSQL 16 connectivity,
  Alembic **0014_retry_scheduling**, and PostgreSQL **28 passed, 128
  deselected**; scoped runner resources were removed; and
- documentation link/reference checks and `graphify update .` — passed.

No application behavior, migration, production data, or active deployment was
changed. Broader durable-operation verification remains Phase 1 item 4, and
the diagnostic security/product decision remains Phase 1 item 5.
