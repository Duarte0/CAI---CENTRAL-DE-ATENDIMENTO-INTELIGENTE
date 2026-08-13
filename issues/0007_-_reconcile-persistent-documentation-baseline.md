---
id: 0007
title: "Reconcile the persistent implementation documentation baseline"
type: spec
status: closed
priority: high
phase: 1
created_at: 2026-08-13
updated_at: 2026-08-13
closed_at: 2026-08-13
related_issues:
  - "0006"
blocked_by: []
affects:
  - README.md
  - PRD.md
  - ARCHITECTURE.md
  - specs/0002-digisac-webhook-and-query-api.md
  - specs/0004-reproducible-verification-baseline.md
  - specs/README.md
  - IMPLEMENTATION_PLAN.md
---

## Description

Deliver `IMPLEMENTATION_PLAN.md` Phase 1, item 1: reconcile the active
implementation-derived documentation with the persistent-only runtime and the
latest recorded disposable verification evidence.

**Verified gap:** the source and current specifications already establish that
persistent history finalization is the only supported path and that mounted
conversation queries are unversioned, but `README.md` still describes the
conversation status as possibly belonging to a legacy flow and records that
the runner explicitly enables persistence. The README, PRD, and architecture
also retain the earlier **118 passed, 33 skipped** and **33 passed, 118
deselected** counts, while issue 0006 records the later **122 passed, 33
skipped** and **33 passed, 122 deselected** evidence. SPEC-0002's contract is
already corrected, but its active documentation and index references must be
checked together for consistency.

Expected outcome: operators and maintainers can identify the actual
persistent-only flow, unversioned query routes, canonical runner boundary, and
local-versus-external verification limits from the versioned documents without
inferring a removed flag, a mounted `/v1/` or `/v2/` route, or production
readiness.

## Scope

### In scope

- Reconcile `README.md`, `PRD.md`, `ARCHITECTURE.md`, SPEC-0002,
  SPEC-0004, the specification index, and the implementation-plan evidence
  with SPEC-0005 and the current source, runner, tests, and issue-0006 result.
- State that persistent DigiSac-history finalization is the sole supported
  behavior and that the runner's offline stage does not select a finalization
  flag or inherit a developer database credential.
- State that mounted conversation/cycle query routes are currently
  unversioned; `/v1/` and `/v2/` may remain only as future compatibility
  policy, never as implemented aliases or mounted routes.
- Update active verification counts and distinguish offline skips, disposable
  PostgreSQL evidence, and the unverified Redis, DigiSac, Groq, replica, and
  production boundaries.
- Validate links, specification versions/IDs, route statements, commands, and
  evidence references; run `graphify update .` and close through the plan sync
  and one focused documentation commit.

### Out of scope

- Application, test, migration, configuration, Compose, runner, route,
  database, Redis, provider, deployment, or production-data changes.
- New API versioning, compatibility aliases, authentication, rate limiting,
  retention policy, hosted CI, production acceptance, or external-runtime
  verification.
- Redefining SPEC-0005 or duplicating the webhook, finalization, media, or
  verification contracts; reference the canonical specifications instead.

## Implementation Plan

1. Re-scan the affected documents and verify each stale statement against
   `src/api/routes.py`, the persistent finalization and configuration code,
   `scripts/verify.py`, SPEC-0002 through SPEC-0005, tracked tests, and the
   resolution of issue 0006. Preserve the repository source-of-truth order;
   record historical counts only when dated and explicitly marked historical.
2. Update the README API, finalization, runner, and validation sections;
   reconcile the corresponding PRD and architecture status/evidence sections;
   and align SPEC-0002, SPEC-0004, and `specs/README.md`. Preserve the
   invariant that documentation describes only the routes and settings the
   checkout actually supports: persistent history is unconditional, query
   routes have no version prefix, and the offline runner does not enable a
   removed flag.
3. Keep verification claims separated by boundary: static checks and offline
   pytest are local evidence, PostgreSQL results come only from the disposable
   runner target, and no local result implies Redis/provider/replica/
   production readiness. Keep `tests/test_webhook_local.py` opt-in.
4. Run targeted searches for legacy-flow claims, finalization-flag runner
   instructions, stale test counts, and mounted versioned routes; validate
   changed links and references. Run the documentation-appropriate checks and
   record only results actually observed. Then run `graphify update .`, inspect
   the final diff, synchronize `IMPLEMENTATION_PLAN.md`, and close with one
   focused commit.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migrations:** none. Do not run migrations, backfills, or data changes
  against an active target.
- **Compatibility:** document current unversioned routes and persistent-only
  behavior; do not add aliases or imply support for the removed finalization
  setting.
- **Security:** do not add credentials or operational exceptions. Documentation
  must preserve the distinction between sanitized local evidence and external
  or production verification, without exposing secrets or raw payloads.
- **Observability:** retain the separate offline, disposable-PostgreSQL, and
  external-runtime evidence boundaries and the safe operational-surface
  wording.
- **Rollout:** documentation-only; no production deployment, provider call,
  Redis operation, or live webhook execution is authorized or required.

## Tests

- **Documentation consistency:** targeted `rg` searches over versioned
  documentation for legacy-flow wording, finalization-flag instructions,
  stale counts, and versioned route claims; verify links, IDs, versions, and
  commands in changed sections.
- **Canonical evidence:** run
  `PYTHONPATH=/app python scripts/verify.py` when Docker is available; if it is
  unavailable, run the safe static/offline subset and report the PostgreSQL
  stage as unavailable rather than claiming it passed.
- **Static:** `python -m compileall -q src tests alembic scripts` and
  `npx --yes pyright` when refreshed evidence is collected.
- **Graph:** `graphify update .` after documentation changes.

## Acceptance Criteria

- [x] README no longer describes conversation status as a legacy flow or says
  that the runner activates persistent finalization through a flag.
- [x] README, PRD, ARCHITECTURE, SPEC-0004, and the specification index use
  the issue-0006 evidence (**122 passed, 33 skipped** offline and **33 passed,
  122 deselected** PostgreSQL), or clearly date and label older counts as
  historical.
- [x] SPEC-0002 and all active API documentation describe mounted query routes
  without a version prefix; `/v1/` and `/v2/` appear only as future policy and
  are not described as aliases or implemented routes.
- [x] Documentation states that persistent DigiSac-history finalization is the
  only supported path and that the offline runner isolates database credentials
  without selecting a removed finalization setting.
- [x] Local offline and disposable-PostgreSQL evidence is clearly separated
  from unverified Redis, DigiSac, Groq, replica, deployment, and production
  behavior; the live webhook test remains opt-in.
- [x] No application code, tests, migrations, configuration, routes, data, or
  external environment is changed by this issue.
- [x] Targeted searches and link/reference checks pass, and every changed
  statement is traceable to current source, SPEC-0005, or observed issue-0006
  evidence.
- [x] `graphify update .` succeeds, the final diff contains only the intended
  documentation/plan-sync changes, and no stale active claim remains.
- [x] `IMPLEMENTATION_PLAN.md` is synchronized with the observed completion
  evidence and the issue is closed only after one focused documentation commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — Phase 1, item 1 (selected); Phase 2,
  item 2 remains blocked on a separately authorized production decision.
- Primary specification: `specs/0005-documentation-baseline-reconciliation.md`
  v1.1 — documentation contract and implementation notes.
- Related specifications: SPEC-0002 v1.5 for unversioned query routes and
  webhook surface; SPEC-0003 v1.3 for persistent-only finalization and media;
  SPEC-0004 v1.4 for the verification matrix and evidence boundaries.
- Completed dependency: issue `0006` supplies the latest recorded
  **122/33** offline and **33/122** PostgreSQL results. Issue `0003` is closed
  and covers the superseded pre-persistent-only documentation baseline; no
  open or in-progress issue duplicates this outcome.
- Current evidence: `src/api/routes.py`, persistent finalization/configuration
  modules, `scripts/verify.py`, tracked verification tests, and the affected
  versioned documents.

---

## Resolution

Implemented the documentation-only reconciliation for the persistent runtime.

- Updated README, PRD, ARCHITECTURE, SPEC-0002, SPEC-0004, the specification
  index, SPEC-0005, and `IMPLEMENTATION_PLAN.md` to state persistent-only
  DigiSac-history finalization, unversioned mounted query routes, future-only
  `/v1/` and `/v2/` policy, and the separated local verification boundaries.
- Replaced stale `118/33` and `33/118` active evidence with `122/33` offline
  and `33/122` disposable PostgreSQL evidence, preserving the issue-0006
  provenance and the limitation that external services and production remain
  unverified.
- Marked SPEC-0005 v1.1 and this issue complete. No application code, tests,
  migrations, configuration, routes, data, credentials, or external services
  were changed.

Validation performed:

- `python -m compileall -q src tests alembic scripts` — passed.
- `npx --yes pyright` — 0 errors, 0 warnings, 0 informations.
- `PYTHONPATH=/app pytest -q --ignore=tests/test_webhook_local.py` — 122
  passed, 33 skipped.
- `PYTHONPATH=/app python scripts/verify.py` — all stages passed; Alembic head
  `0014_retry_scheduling`; PostgreSQL 16 tests 33 passed, 122 deselected.
- Both Compose configurations, targeted stale-claim searches, relative-link
  checks, `git diff --check`, and `graphify update .` — passed.

The remaining production/external-runtime acceptance decision stays blocked as
specified in the plan; it is outside this issue.
