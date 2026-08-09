---
id: 0005
title: "Remove the legacy Redis-buffer finalization path"
type: refactor
status: closed
priority: high
phase: 2
created_at: 2026-08-09
updated_at: 2026-08-09
closed_at: 2026-08-09
related_issues:
  - "0001"
  - "0002"
  - "0004"
blocked_by: []
affects:
  - src/api/
  - src/core/config.py
  - src/core/db.py
  - src/workers/ia_worker.py
  - tests/
  - .env.example
  - README.md
  - PRD.md
  - ARCHITECTURE.md
  - specs/0003-durable-finalization-and-media.md
  - specs/README.md
  - IMPLEMENTATION_PLAN.md
---

## Description

Deliver the implementation follow-up behind `IMPLEMENTATION_PLAN.md` Phase 2,
item 8: leave persistent DigiSac-history finalization as the only finalization
mode and remove the approved Redis-buffer compatibility path.

**Verified gap:** the approved decision is recorded as complete, but the current
checkout still exposes `DIGISAC_HISTORY_FINALIZATION_ENABLED` with a `false`
default, retains buffer/debounce settings and Redis key handling, branches
webhook and status/result behavior on the flag, and dispatches the IA worker to
legacy buffer processing when it is false. The canonical test runner and
fixtures force the persistent path, so the legacy code remains implemented but
is no longer required to support the verified baseline. No open or in-progress
issue covers this removal outcome.

Expected outcome: all supported webhook, query, worker, recovery, and media
flows use the existing persistent-cycle contract without a compatibility flag
or Redis-buffer/debounce state. Durable PostgreSQL cycles remain the source of
truth; Redis remains only transport and transient coordination for the
persistent flow. This issue does not decide or expose raw-payload diagnostics.

## Scope

### In scope

- Remove the legacy finalization setting, environment example, buffer/debounce
  settings, Lua/Redis buffer operations, legacy cleanup, and conditional route
  branches that select or report the compatibility path.
- Make the API always create/recover persistent cycles on ticket lifecycle
  events, persist a close before cycle publication, and serve status/result
  from durable cycles without a legacy fallback.
- Remove IA-worker buffer processing, `ia_processing` handling that belongs
  only to it, and legacy key/claim/debounce cleanup while retaining the
  persistent queue, cycle reconciliation, media scheduling, and safe
  status/result publication contracts.
- Remove or replace legacy-specific tests and fixtures with persistent-cycle
  coverage for the same supported webhook and media behavior; retain the
  canonical runner's disposable PostgreSQL and opt-in live-webhook boundaries.
- Synchronize implementation-derived documentation, SPEC-0003's legacy section,
  the specification index, `IMPLEMENTATION_PLAN.md`, and Graphify metadata when
  closing, then close in one focused commit.

### Out of scope

- Changing cycle schema, migrations, backfills, durable retry values, provider
  behavior, IA output contract, media recovery semantics, or production data.
- Adding a public API version, access-control layer, rate limit, hosted CI, or
  Redis deployment change.
- The raw-payload diagnostic policy and any mounting/exposure of
  `src/api/debug_routes.py`; that remains blocked by Phase 1 item 5's
  product/security decision.
- Deleting historical classifications, cycles, or Redis data outside normal TTL
  expiry; removal must not require a destructive production cleanup.

## Implementation Plan

1. Trace every finalization-mode branch and legacy Redis key from configuration
   through webhook handling, IA-worker dispatch, health/queue observability,
   status/result responses, tests, and documented environment variables.
   Preserve the existing persistent cycle state machine, durable publication
   marker, claim/lease, `next_attempt_at`, and media invariants as the sole
   contract before deleting compatibility code.
2. Remove selection of `DIGISAC_HISTORY_FINALIZATION_ENABLED` and the settings
   that exist only for the buffer/debounce path. Update startup/schema checks so
   the durable-cycle schema is an unconditional runtime prerequisite, without
   introducing startup schema creation or mutation.
3. Simplify webhook ticket and message handling to the persistent path. Opening
   or reopening must idempotently create/recover the appropriate persistent
   cycle; valid closure must persist the cycle before publishing its job; late
   messages must not recreate a Redis buffer or schedule debounce work.
4. Remove the legacy worker loop, Lua buffer claim, `ia_processing` queue use,
   and buffer/debounce/claim keys. Retain only cycle-queue processing and its
   recovery behavior: concurrent workers claim once, failed publication remains
   recoverable, retry scheduling remains due-only, and no terminal
   classification is duplicated.
5. Update API operational views to use durable cycle data consistently and keep
   their existing safe missing-result behavior. Keep supported media behavior:
   reserve before Redis publication, terminal image failures block dependent
   cycles, terminal audio failures remain warnings, and targeted recovery does
   not remove unrelated dead letters.
6. Replace tests that assert compatibility selection or buffer processing with
   tests proving the unconditional persistent behavior and the absence of
   legacy publication/keys. Run the existing offline and disposable PostgreSQL
   families, preserving their explicit database boundary and excluding the
   opt-in live webhook test.
7. After passing validation, remove obsolete legacy wording from the canonical
   documentation/spec references, sync `IMPLEMENTATION_PLAN.md`, run
   `graphify update .`, and make one focused commit containing this issue's
   implementation and completion updates.

## Data, compatibility, security, observability, and rollout

- **Data/migrations:** no schema or historical-data deletion is required. The
  existing Alembic-owned persistent-cycle schema remains authoritative; startup
  may verify it but must not create or mutate it.
- **Compatibility:** this is the approved incompatible removal of the
  feature-flagged legacy mode. Deployments must be configured and rolled out as
  persistent-cycle-only; no fallback to buffered in-flight work may be added.
- **Security:** preserve HMAC-before-normalization and the prohibition on raw
  bodies, tokens, signed URLs, and media binaries in logs, buffers, snapshots,
  or durable records. Do not broaden the diagnostic surface.
- **Observability:** queue/cycle views must continue to expose only safe IDs,
  states, and sanitized reasons. Remove only legacy queue/key counters; retain
  persistent IA queue, dead-letter, cycle, and media observability.
- **Rollout:** use the documented coordinated deployment for the persistent
  schema/services. Do not run a production migration, delete Redis keys, or
  claim production verification as part of this issue.

## Tests

- **Webhook/API:** persistent ticket create, reopen, close, late-message, and
  status/result paths; HMAC and ignored-event negatives remain covered without
  buffer/debounce side effects.
- **Worker/recovery:** only the durable cycle queue is dispatched; concurrent
  claims, publication failure/retry, future scheduling, media-blocked image,
  audio warnings, and targeted recovery retain their established behavior.
- **Configuration/regression:** no supported configuration selects a legacy
  finalization path, and no legacy buffer/debounce keys or `ia_processing`
  work are published by the supported flow.
- **Canonical verification:** `python -m compileall -q src tests alembic scripts`,
  `npx --yes pyright`, and `PYTHONPATH=/app python scripts/verify.py`.
- **Graph:** `graphify update .` after implementation changes.

## Acceptance Criteria

- [x] The obsolete finalization setting and environment value,
  route/worker branches, Redis buffer/debounce operations, and legacy
  `ia_processing` handling are removed; no supported runtime path can select
  Redis-buffer finalization.
- [x] Ticket creation/reopening, valid closure, late messages, and status/result
  queries use the persistent-cycle contract unconditionally, remain idempotent,
  and do not create buffer/debounce keys or duplicate cycle publication.
- [x] The IA worker processes only persistent cycle jobs while preserving durable
  claim/lease, publication-failure release, due-only retry, and one-terminal-
  classification invariants under retry or concurrency.
- [x] Audio/image reservation and recovery retain reserve-before-publication,
  no duplicate queue publication, safe terminal-audio warnings, and the rule
  that a terminal image failure blocks only dependent cycles until recovery.
- [x] The persistent-cycle schema is verified as required at runtime without
  schema creation/mutation, migration, backfill, or deletion of historical
  PostgreSQL/Redis data.
- [x] Tests cover unconditional persistent behavior plus the absence of legacy
  buffer/debounce side effects; existing HMAC, ignored-event, durable recovery,
  and live-webhook opt-in boundaries remain intact.
- [x] Compileall, Pyright, focused tests, and `PYTHONPATH=/app python
  scripts/verify.py` pass with results recorded accurately; no active or
  production database, provider, or live webhook is used for verification.
- [x] README, PRD, architecture, SPEC-0003, `specs/README.md`,
  `IMPLEMENTATION_PLAN.md`, and Graphify metadata are synchronized on closure,
  and the work is closed in one focused commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — Phase 2, item 8 (selected). Its completed
  status and discrepancy section now record the implemented persistent-only
  refactor.
- Primary specification: `specs/0003-durable-finalization-and-media.md` v1.3
  — approved complete legacy removal and persistent-cycle/media invariants.
- Related specifications: `specs/0001-shared-data-and-analysis-contract.md`
  v1.1 — PostgreSQL durable authority, privacy, and idempotency; and
  `specs/0002-digisac-webhook-and-query-api.md` v1.3 — webhook lifecycle,
  HMAC, idempotency, and query contracts.
- Completed dependencies: issues `0001`, `0002`, and `0004` provide the
  persistent test baseline, disposable PostgreSQL runner, and durable recovery
  evidence. No open or in-progress issue covers legacy-path removal.
- Current evidence: `src/core/config.py`, `src/api/routes.py`,
  `src/core/db.py`, `src/workers/ia_worker.py`, `.env.example`, the persistent
  cycle/media tests, and `scripts/verify.py`.

---

## Resolution

Implemented issue 0005 as a persistent-cycle-only finalization refactor:

- removed the obsolete finalization setting, buffer/debounce configuration,
  Lua scripts, Redis buffer helpers, legacy API branches, status/result fallback,
  and `ia_processing` worker loop;
- made ticket creation, reopening, closure, message idempotency, status, and
  result paths use durable PostgreSQL cycles unconditionally;
- removed the buffer-only `MessageBuffer` model and tests while retaining the
  persistent normalization, media, recovery, HMAC, and live-webhook boundaries;
- made startup verify the persistent-cycle schema unconditionally and updated
  the runner, configuration example, README, PRD, architecture, and active
  specifications to the implementation-derived persistent-only contract.

Validation executed:

- focused regression suite — **64 passed**;
- `python -m compileall -q src tests alembic scripts` — passed;
- `npx --yes pyright` — **0 errors, 0 warnings, 0 informations**;
- `PYTHONPATH=/app pytest -q --ignore=tests/test_webhook_local.py` — **118
  passed, 33 skipped**; skips require `CAI_TEST_DATABASE_URL`;
- `PYTHONPATH=/app python scripts/verify.py` — compileall, Pyright, offline
  **118 passed / 33 skipped**, runner-owned PostgreSQL 16 connectivity,
  Alembic `0014_retry_scheduling`, and PostgreSQL **33 passed / 118
  deselected**; scoped Compose resources were removed; and
- `graphify update .` — passed after the implementation and documentation
  changes.

No migration, production database, provider, credentials, Redis deployment, or
live webhook was used.
