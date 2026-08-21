---
id: 0029
title: "Isolate conversation-cycle persistence from the database facade"
type: refactor
status: closed
priority: medium
phase: 5
created_at: 2026-08-20
updated_at: 2026-08-20
closed_at: 2026-08-20
related_issues:
  - "0004"
  - "0005"
  - "0020"
  - "0028"
blocked_by: []
affects:
  - src/core/db.py
  - src/api/routes.py
  - src/workers/ia_worker.py
  - tests/test_conversation_cycles_db.py
  - tests/test_operational_recovery_db.py
  - tests/test_ticket_closure.py
  - tests/test_history_finalization_webhook.py
  - tests/test_conversation_cycle_repository.py
  - README.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
---

## Description

`src/core/db.py` owns the process-wide PostgreSQL lifecycle and schema-capability
check, but it also embeds the cohesive repository for
`conversation_processing_cycles` and `conversation_cycle_messages`. The cycle
section starts with the cycle status sets and `_require_cycle_schema`, then owns
open/close idempotency and sequencing, reads, guarded transitions and leases,
reconciliation-publication markers, selective image-unblock wake-up, message
membership, content-state reads, metrics, and cycle-result projection. The
same facade separately owns unrelated assignment, directory, contact,
classification, and media persistence.

This is a distinct durable boundary with direct consumers in the webhook routes
and IA worker, plus PostgreSQL and route-level monkeypatch tests. Its behavior
is already governed by SPEC-0003: a persisted cycle precedes queue
publication; expected-state transitions, advisory locks, `SKIP LOCKED`, leases,
and `next_attempt_at` prevent duplicate or premature work; message membership
is exclusive; and a recovered image wakes only affected blocked cycles. Keeping
that repository inside the generic facade makes a cycle-only change span
unrelated persistence domains and makes the durable contract harder to isolate.

Extract only this cycle repository behind one focused internal module using the
existing initialized pool and schema-capability information. `src.core.db`
remains the lifecycle owner and preserves its current cycle-facing async
imports as a compatibility facade, unless every in-repository consumer and its
monkeypatch seam is migrated atomically without changing its observable
contract. This is not authorization to alter cycle, media, Redis, API, schema,
or Request behavior.

## Scope

### In scope

- Move the cohesive persistence implementation for conversation cycles, cycle
  messages, cycle result/metrics, cycle-to-media state reads, and selective
  unblocking into one internal cycle-persistence boundary.
- Keep the single initialized process-local PostgreSQL pool, schema verification,
  shared timestamp/row serialization helpers where they are already common,
  and unrelated repositories in `src/core/db.py`.
- Preserve the current async callable signatures, row/count/list shapes, direct
  import behavior, and route/worker monkeypatch seams for existing consumers.
- Add or adjust focused tests proving the extracted boundary preserves current
  durable cycle and publication-recovery invariants.
- Synchronize implementation-era source-map/architecture, plan status, and
  Graphify metadata after implementation without rewriting completed historical
  specs or issues.

### Out of scope

- Any Alembic migration, schema/index/constraint change, data rewrite, backfill,
  retention change, or runtime schema creation.
- Changing webhook parsing, HTTP routes or response bodies, Redis queue names or
  payloads, worker/CLI interfaces, finalization/context/IA policy, or the
  DigiSac/Groq/Acessórias provider contracts.
- Changing cycle statuses, start/close inference, retry/backoff,
  `next_attempt_at`, idempotency, advisory locking, lease, transaction,
  `SKIP LOCKED`, failure, image-blocking, audio-warning, or recovery semantics.
- Extracting contact persistence (issue 0028), assignment, directory,
  classification, media reservation, identity, department mapping, or
  Acessórias Request persistence.

## Implementation Plan

1. Inventory the cycle-facing exports, route/worker imports, direct tests, and
   monkeypatch seams. Treat existing async signatures and returned row/list/
   count shapes as compatibility contracts before moving implementation.
2. Introduce one internal cycle-persistence boundary with only the existing
   pool, schema-capability, settings, UUID/timestamp, row-serialization, and
   SQL primitives it actually needs. Move cycle status definitions together
   with the cycle and membership operations; do not create a second pool or
   duplicate SQL.
3. Keep `db.py` responsible for initialization, shutdown, readiness, and schema
   verification, without circular imports. Retain import-compatible cycle
   exports there, or make one source-confirmed atomic consumer migration that
   preserves current route and worker test seams.
4. Preserve exact durable boundaries: per-conversation advisory locking and
   open/close event idempotency; sequence allocation; expected-status updates;
   due-time, lease, and publication-marker predicates; `FOR UPDATE SKIP LOCKED`
   reconciliation; transactional membership replacement with cross-cycle
   exclusivity; and selective image recovery. Do not change SQL predicates,
   state values, timestamps, transaction scope, or queue-publication ordering.
5. Run focused route, worker, and disposable-PostgreSQL cycle/recovery coverage,
   then the repository static and canonical verification commands. Update only
   implementation-era documentation and Graphify after passing validation, then
   close with one focused commit.

## Tests

- **Route and compatibility seams:** `PYTHONPATH=/app python -m pytest -q tests/test_ticket_closure.py tests/test_history_finalization_webhook.py`
- **Cycle persistence:** `PYTHONPATH=/app python -m pytest -q tests/test_conversation_cycles_db.py tests/test_operational_recovery_db.py`
- **Worker behavior:** `PYTHONPATH=/app python -m pytest -q tests/test_ia_worker_retry.py tests/test_ia_history_db.py`
- **Static:** `python -m compileall -q src tests alembic scripts` and `npx --yes pyright`
- **Canonical disposable verification:** `PYTHONPATH=/app python scripts/verify.py`
- **Graph:** `graphify update .` after implementation changes.

## Acceptance Criteria

- [x] Conversation-cycle persistence is isolated behind one cohesive internal
  boundary instead of being embedded with unrelated domains in `src/core/db.py`.
- [x] Existing cycle persistence functions remain import- and call-compatible
  for routes, workers, utilities, and tests, or one atomic migration preserves
  those contracts and monkeypatch seams.
- [x] Exactly one initialized process-local pool and the existing Alembic schema
  verification remain in use; no new lifecycle, provider call, runtime schema
  creation, migration, or persistence authority is added.
- [x] Open, reopen, and close preserve event-key idempotency, one open cycle per
  conversation, sequence allocation, canonical contact provenance, and their
  existing returned data shapes.
- [x] Transition, claim, and reconciliation behavior preserves expected-status,
  due-time, lease, `enqueued_at`, advisory-lock, transaction, and `SKIP LOCKED`
  predicates so concurrent workers neither duplicate work nor publish early.
- [x] Cycle-message membership remains transactional and exclusive across cycles;
  snapshot/result/metrics and content-state projections preserve their current
  fields and privacy boundaries.
- [x] Terminal image failure still blocks only dependent cycles, later image
  recovery wakes only eligible dependent cycles, and terminal-audio warning and
  finalization behavior remain unchanged.
- [x] Public HTTP, Redis queues/payloads, worker and CLI interfaces, persistence
  semantics, authorization/security, retry/idempotency/concurrency/failure
  semantics, provider contracts, and compatibility remain unchanged; no secret,
  raw payload, or sensitive content is added to logs, fixtures, or durable
  metadata.
- [x] Focused tests, compileall, strict Pyright, and the canonical disposable
  runner pass, with local/disposable evidence reported separately from external
  runtime or production evidence.
- [x] README/architecture/plan synchronization where affected, Graphify metadata,
  and source-map references are updated after implementation; the issue is
  closed only after validation and one focused commit.

## References

- Primary contract: `specs/0003-durable-finalization-and-media.md` v1.5,
  especially persistent-cycle, concurrency, media-recovery, and verification
  requirements.
- Verification contract: `specs/0004-reproducible-verification-baseline.md`
  v1.6.
- Product/architecture: `PRD.md` §§5.1–5.4 and 8; `ARCHITECTURE.md` §§2, 5–7,
  9, 12, and 14.
- Plan: `IMPLEMENTATION_PLAN.md` — completed durable-finalization and
  operational-verification baseline; this is structural maintenance, not a new
  product milestone.
- Related issues: `0004` (durable operational recovery), `0005` (persistent
  finalization only), `0020` (cycle bounds consumed by mapping), and `0028`
  (separate DigiSac-contact extraction from the same facade).
- Current evidence: `src/core/conversation_cycle_repository.py` cycle section from
  `RECOVERABLE_CYCLE_STATUSES` through `get_cycle_result`; compatibility exports
  in `src/core/db.py`; direct imports in
  `src/api/routes.py` and `src/workers/ia_worker.py`; and
  `tests/test_conversation_cycles_db.py`, `tests/test_operational_recovery_db.py`,
  `tests/test_ticket_closure.py`, and `tests/test_history_finalization_webhook.py`.
- Non-duplicate rationale: issue `0028` extracts only DigiSac-contact and
  hydration persistence. Existing issues `0004`, `0005`, and `0020` validate
  or change cycle behavior, but none isolates the cycle repository while
  preserving its current facade and durable semantics.

## Resolution

Implemented issue 0029 as a behavior-preserving persistence-boundary
extraction.

### Implementation

- Moved cycle status definitions and all cycle persistence SQL into
  `src/core/conversation_cycle_repository.py`, including open/close, reads,
  transitions, claims, publication markers, message membership, content-state
  projections, metrics, result projection, and selective media unblocking.
- Kept `src/core/db.py` as the single PostgreSQL pool/lifecycle and live schema
  capability owner, with explicit compatibility assignments for the existing
  cycle imports. No second pool, migration, runtime schema creation, or
  persistence authority was added.
- Added `tests/test_conversation_cycle_repository.py` to verify repository
  ownership and identity of every compatibility export. Existing route, worker,
  cycle, and operational-recovery seams remain unchanged.

### Tests and validation

- `PYTHONPATH=/app python -m pytest -q tests/test_ticket_closure.py tests/test_history_finalization_webhook.py`: 13 passed baseline; final focused coverage passed.
- `PYTHONPATH=/app python -m pytest -q`: 214 passed, 69 skipped without the test database.
- `python -m compileall -q src tests alembic scripts`: passed.
- `npx --yes pyright`: 0 errors, 0 warnings, 0 informations.
- `PYTHONPATH=/app python scripts/verify.py`: compileall, Pyright, offline
  214 passed/69 skipped, disposable PostgreSQL 16 and Alembic head
  `0020_cycle_contact_provenance`, PostgreSQL 69 passed/214 deselected; all
  stages passed and the temporary Compose project was removed.
- `graphify update .`: completed; graph metadata was refreshed after the code
  extraction.

### Migrations

N/A. The existing Alembic-owned schema was reused unchanged.

### Documentation and key decisions

- Updated SPEC-0003, SPEC-0004 verification notes, `specs/README.md`,
  `README.md`, `ARCHITECTURE.md`, and `IMPLEMENTATION_PLAN.md` with the
  implementation boundary and final local/disposable evidence.
- Preserved the existing async call signatures, returned shapes, SQL
  predicates, transaction scope, locking, scheduling, idempotency, queue
  publication, recovery, and privacy behavior. The repository reads the live
  schema-capability state from `db.py` so initialization remains authoritative.
