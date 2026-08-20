---
id: 0031
title: "Isolate durable media persistence from the database facade"
type: refactor
status: closed
priority: medium
phase: 5
created_at: 2026-08-20
updated_at: 2026-08-20
closed_at: 2026-08-20
related_issues:
  - "0004"
  - "0027"
  - "0029"
blocked_by: []
affects:
  - src/core/db.py
  - src/core/durable_media_repository.py
  - src/api/routes.py
  - src/workers/audio_worker.py
  - src/workers/image_worker.py
  - src/workers/ia_worker.py
  - tests/test_audio_worker.py
  - tests/test_image_extraction.py
  - tests/test_ia_history_db.py
  - tests/test_operational_recovery_db.py
  - tests/test_postgres_concurrency.py
  - tests/test_durable_media_repository.py
  - README.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
---

## Description

`src/core/db.py` owns the process-wide PostgreSQL lifecycle and schema
verification, but it also embeds the complete durable-media repository for two
parallel tables: `message_transcriptions` and `message_image_extractions`.
The shared section from `_reserve_content_sync()` through
`get_pending_content_extractions()` owns reservation/replay, guarded status
transitions, completed-content reads, due/stale recovery claims, publication
marker release, and pending-state projection.  It parameterizes SQL by the
two fixed table names, then exposes separate audio and image wrappers to the
API, both media workers, the IA worker, and PostgreSQL tests.  Unrelated
assignment, directory, contact, classification, and conversation-cycle
persistence remains in the same facade.

This is a cohesive durable boundary, not merely duplicated helper code.  Its
state and publication protocol is governed by SPEC-0003: PostgreSQL reservation
precedes Redis publication; state, attempt, lease and expected-update guards
prevent stale completion; due recovery uses `SKIP LOCKED`; and failed
publication clears only the matching marker.  Audio and image workflows have
different provider and terminal-cycle consequences, but they intentionally
share this persistence protocol.  Keeping it inside the generic facade makes
media-only changes span unrelated repositories and obscures the exact boundary
that must remain behaviorally identical.

Extract only the shared durable-media persistence behind one internal module,
using the existing initialized pool and shared serialization primitives. Keep
`src.core.db` as lifecycle/schema owner and preserve its current media-facing
async imports as a compatibility facade unless every in-repository consumer and
its monkeypatch seam is migrated atomically with unchanged observable behavior.
This issue does not authorize changes to media processing, queues, retry policy,
cycle transitions, schema, or external contracts.

## Scope

### In scope

- Move the cohesive persistence implementation for transcription and image
  extraction reservation, state transition, read, recovery-claim, publication
  release, and pending-content projection into one internal durable-media
  boundary.
- Keep the single process-local PostgreSQL pool, initialization, Alembic schema
  verification, and only the timestamp/row/SQL primitives genuinely shared
  with other domains in `src/core/db.py`.
- Preserve the existing audio- and image-specific async callable signatures,
  returned boolean/timestamp/row/map/set shapes, direct import paths, and test
  monkeypatch seams for API and worker consumers.
- Add or adjust focused tests only as needed to prove the extracted boundary
  preserves the current durable-media contract.
- Synchronize implementation-era source-map/architecture, plan status, and
  Graphify metadata after implementation without rewriting completed historical
  specs or issues.

### Out of scope

- Any Alembic migration, schema/index/constraint change, data rewrite,
  backfill, retention change, runtime schema creation, or second persistence
  authority.
- Changing DigiSac/Groq calls, models, audio conversion, image validation,
  webhook parsing, public routes/responses, Redis queue names/payloads, worker
  or CLI interfaces, configuration, or dead-letter ownership.
- Changing `pending`/`processing`/`completed`/`failed` semantics, retry or
  backoff timing, `Retry-After` treatment, idempotency, transaction scope,
  expected-status/updated-at guards, leases, `SKIP LOCKED`, failure handling,
  terminal audio warnings, image `media_blocked` behavior, or selective cycle
  wake-up.
- Extracting contact persistence (issue 0028), conversation-cycle persistence
  (issue 0029), ticket-assignment persistence (issue 0030), classification,
  directory, identity, mapping, or Acessórias Request repositories.

## Implementation Plan

1. Inventory every media-facing export, API/worker/IA import, direct
   PostgreSQL test, and monkeypatch seam. Treat existing signatures, return
   shapes, fixed-table allowlist, and audio/image wrapper names as compatibility
   contracts before moving implementation.
2. Introduce one internal durable-media persistence boundary using only the
   existing initialized pool and required shared helpers. Move the fixed-table
   allowlist and generic reservation, transition, read, completion, recovery,
   publication-release, and pending-projection operations together; do not
   duplicate SQL or create a pool.
3. Keep `db.py` as the lifecycle and schema-capability owner without circular
   imports. Retain its current media exports, or make one source-confirmed,
   atomic consumer migration that preserves import behavior and all existing
   worker/API monkeypatch seams.
4. Preserve exact durable predicates and transaction boundaries: reservation
   reopens only the current eligible terminal row; state writes honor expected
   status and optional update timestamp; recovery is due-only and uses row
   locking with `SKIP LOCKED`; a publication failure clears only the matching
   pending marker; completed-content and pending-state reads keep their current
   de-duplication and non-empty-text rules. Do not move cycle wake-up or change
   its coupling to successful image recovery.
5. Run focused audio, image, API/IA, concurrency, and disposable-PostgreSQL
   recovery coverage, then the repository static and canonical verification.
   Update implementation-era documentation and Graphify only after validation,
   then close the issue with one focused commit.

## Tests

- **Worker and API seams:** `PYTHONPATH=/app python -m pytest -q tests/test_audio_worker.py tests/test_image_extraction.py tests/test_history_finalization_webhook.py`
- **Media persistence and IA consumer:** `PYTHONPATH=/app python -m pytest -q tests/test_ia_history_db.py tests/test_operational_recovery_db.py`
- **Concurrency:** `PYTHONPATH=/app python -m pytest -q tests/test_postgres_concurrency.py`
- **Static:** `python -m compileall -q src tests alembic scripts` and `npx --yes pyright`
- **Canonical disposable verification:** `PYTHONPATH=/app python scripts/verify.py`
- **Graph:** `graphify update .` after implementation changes.

## Acceptance Criteria

- [x] Durable transcription and image-extraction persistence is isolated behind
  one cohesive internal boundary instead of being embedded with unrelated
  domains in `src/core/db.py`.
- [x] Current media-facing functions remain import- and call-compatible for
  API, audio/image/IA workers, utilities, and tests, or an atomic migration
  preserves their signatures, returned shapes, and monkeypatch seams.
- [x] Exactly one initialized process-local pool and the current Alembic schema
  verification remain in use; no migration, runtime schema creation, provider
  call, new lifecycle, or persistence authority is introduced.
- [x] Audio and image reservation stays idempotent by message ID; only the same
  terminal-row conditions may reopen work, and reservation still precedes Redis
  publication without changing queue or payload behavior.
- [x] State transitions retain current expected-status/updated-at guards,
  attempt counts, completed-at handling, sanitized errors, and `next_attempt_at`
  semantics so stale or concurrent workers cannot overwrite a valid result.
- [x] Due/stale recovery keeps its lease, ordering, transaction, and `FOR UPDATE
  SKIP LOCKED` behavior; release after failed publication affects only the
  matching pending media row and does not duplicate jobs.
- [x] Completed-content and pending-state projections retain their current
  deduplication, non-empty-text, and audio/image table-selection behavior for
  IA finalization.
- [x] Terminal audio warning, terminal-image `media_blocked`, selective image
  recovery wake-up, retry/backoff, idempotency, concurrency, failure,
  authorization/security/privacy, public HTTP, Redis, worker/CLI, provider,
  persistence, and compatibility semantics remain unchanged.
- [x] Focused tests, compileall, strict Pyright, and the canonical disposable
  runner pass, with local/disposable evidence reported separately from external
  runtime or production evidence.
- [x] README/architecture/plan synchronization where affected, Graphify
  metadata, and source-map references are updated after implementation; the
  issue is closed only after validation and one focused commit.

## References

- Primary contract: `specs/0003-durable-finalization-and-media.md` v1.5,
  especially media reservation, concurrent recovery, terminal outcomes, and
  verification requirements.
- Verification contract: `specs/0004-reproducible-verification-baseline.md`
  v1.6.
- Product/architecture: `PRD.md` §§5.3, 5.4, and 8; `ARCHITECTURE.md` §§2,
  4, 6, 7, 9, 12, and 14.
- Plan: `IMPLEMENTATION_PLAN.md` — completed durable analysis/recovery and
  audio-retry parity; this is structural maintenance, not a new milestone.
- Related issues: `0004` validates durable media recovery, `0027` established
  audio retry parity, and `0029` is the separate cycle-persistence extraction
  that continues to own cycle transitions and image-dependent wake-up.
- Current evidence: `src/core/durable_media_repository.py`; compatibility
  exports in `src/core/db.py`; direct media imports in
  `src/api/routes.py`, `src/workers/audio_worker.py`,
  `src/workers/image_worker.py`, and `src/workers/ia_worker.py`; plus
  `tests/test_ia_history_db.py`, `tests/test_operational_recovery_db.py`, and
  `tests/test_postgres_concurrency.py`.
- Non-duplicate rationale: issues `0028`, `0029`, and `0030` isolate contact,
  cycle, and assignment repositories respectively. Issue `0027` changed audio
  retry behavior; none isolates the shared audio/image persistence repository
  while preserving its current facade and durable semantics.

---

## Resolution

Implemented issue 0031 as a behavior-preserving durable-media persistence
boundary extraction.

### Implementation

- Moved the shared transcription/image reservation, guarded status transitions,
  completed-content reads, due/stale recovery claims, publication release, and
  pending-content projection into `src/core/durable_media_repository.py`.
- Kept `src/core/db.py` as the single PostgreSQL pool/lifecycle,
  schema-capability, shared-row-helper, and compatibility-facade owner. All
  existing audio/image async imports and signatures remain aliases to the
  focused repository; no second pool, migration, runtime schema creation, or
  persistence authority was added.
- Added `tests/test_durable_media_repository.py` to verify ownership and every
  compatibility export. Existing API, worker, IA, recovery, and concurrency
  consumers remain unchanged.

### Tests and validation

- Baseline focused suite: **23 passed, 13 skipped** before implementation.
- Focused final suite including ownership coverage:
  **24 passed, 13 skipped**.
- `python -m compileall -q src tests alembic scripts`: passed.
- `npx --yes pyright`: **0 errors, 0 warnings, 0 informations**.
- `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`:
  **216 passed, 69 skipped**.
- `PYTHONPATH=/app python scripts/verify.py`: compileall, Pyright, offline
  pytest (**216 passed, 69 skipped**), disposable PostgreSQL 16 and Alembic
  head `0020_cycle_contact_provenance`, and PostgreSQL pytest (**69 passed,
  216 deselected**); all stages passed and the temporary Compose project was
  removed.
- `git -c safe.directory=/app diff --check`: passed.
- `graphify update .`: completed after the implementation and documentation
  updates.

### Migrations

N/A. The existing Alembic-owned schema was reused unchanged.

### Documentation and key decisions

- Updated SPEC-0003, SPEC-0004 verification notes, `specs/README.md`,
  `README.md`, `ARCHITECTURE.md`, and `IMPLEMENTATION_PLAN.md` with the
  durable-media boundary and current local/disposable evidence.
- Preserved the fixed table allowlist, message-ID idempotency, state and
  timestamp guards, transaction boundaries, `SKIP LOCKED` recovery, pending
  marker release, non-empty completed-text projection, queue/publication order,
  selective image wake-up coupling, and privacy/provider contracts.
