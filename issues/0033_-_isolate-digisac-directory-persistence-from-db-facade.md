---
id: 0033
title: "Isolate DigiSac directory persistence from the database facade"
type: refactor
status: closed
priority: medium
phase: 5
created_at: 2026-08-20
updated_at: 2026-08-20
closed_at: 2026-08-20
related_issues:
  - "0016"
  - "0020"
  - "0030"
blocked_by: []
affects:
  - src/core/db.py
  - src/core/digisac_directory.py
  - src/workers/ia_worker.py
  - tests/test_digisac_directory.py
  - tests/test_ticket_assignments.py
  - tests/test_department_mapping.py
  - README.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
---

## Description

`src/core/db.py` owns the process-wide PostgreSQL lifecycle, but it also owns
the DigiSac directory cache persistence in one section and the directory user
lookup in a distant section. `_upsert_digisac_directory_sync()`,
`mark_directory_sync_attempt()`, and `directory_refresh_is_due()` at lines
350–461 write `digisac_departments`, `digisac_users`, and
`digisac_directory_sync_state`; `_resolve_user_names_sync()` and
`resolve_user_names()` at lines 2461–2482 read `digisac_users` for message
sender-name projection. The provider-facing `digisac_directory` orchestrator
imports the first three functions, while `IAWorker` imports both the
orchestrator and the user lookup. This leaves one directory responsibility
split across an otherwise generic facade and unrelated cycle persistence.

The boundary is supported by the implemented architecture: DigiSac directory
sync is a component with PostgreSQL cache and sync state, PostgreSQL is the
authoritative directory store, and synchronized names are the only source for
assignment/name projection. The Alembic-owned schema defines the three cache
tables in `0001_initial`; no schema or policy decision is needed to move their
existing access behind an internal repository. Existing domain modules such as
identity resolution, department mapping, and Acessórias Requests already use
the shared pool accessor rather than owning the process lifecycle.

Issue 0030 extracts ticket-assignment event/history persistence and its
assignment-plus-directory projection but explicitly excludes the DigiSac
directory repository. This issue therefore covers only the remaining
directory-cache persistence and lookup boundary; it must not move assignment
history or change how mapping reads the persisted directory.

## Scope

### In scope

- Extract DigiSac directory cache persistence and lookup into one focused
  internal boundary using the existing initialized PostgreSQL pool:
  resource upsert, sync-attempt state, refresh-due evaluation, and user-name
  lookup.
- Keep `src.core.db` responsible for database URL/pool lifecycle, Alembic-head
  verification, and genuinely shared timestamp/row primitives, while retaining
  import- and call-compatible facade exports for the current consumers unless
  every confirmed consumer and test seam moves atomically with the same
  observable contract.
- Keep `src.core.digisac_directory` responsible for provider authentication,
  pagination, transient retry, the process-local sync lock, and the periodic
  loop; it should call the extracted persistence boundary without acquiring a
  second connection lifecycle.
- Preserve the existing directory consumers in the IA worker, assignment
  projection, and cycle-scoped department mapping, adding or adjusting focused
  PostgreSQL coverage only to prove the extraction is behaviorally equivalent.
- Synchronize implementation-era architecture/source-map, plan status, and
  Graphify metadata after implementation, without changing completed specs or
  historical issues.

### Out of scope

- Any Alembic migration, schema/index/constraint change, data rewrite, backfill
  execution, retention-policy change, runtime schema creation, or second
  persistence authority.
- Changing DigiSac authentication, endpoint paths, pagination, response
  validation, transient status set, retry/backoff, timeout, sync lock,
  refresh interval/cooldown, logging, or failure behavior that preserves the
  previous directory cache.
- Changing the resource allowlist, entry filtering, timestamp normalization,
  upsert conflict behavior, sync-state timestamps, user-name projection,
  unresolved-ID handling, or the existing transaction boundaries.
- Moving or changing ticket-assignment event keys/history, assignment-name
  projection, cycle-bounded mapping selection, identity resolution, Acessórias
  directory/Request persistence, contact persistence, classification, media,
  or conversation-cycle persistence (see issue 0030 and the other open
  extraction issues).
- Adding public routes, CLI commands, provider calls, authorization, matching,
  automatic identity confirmation, new retries, new idempotency semantics, or
  user-visible behavior.

## Implementation Plan

1. Inventory the four directory-facing persistence exports, their direct
   imports, IA-worker snapshot use, provider-sync orchestration, PostgreSQL
   tests, monkeypatch seams, and the assignment/mapping SQL that reads the
   same tables. Treat async signatures, return shapes, resource validation,
   and facade imports as compatibility contracts.
2. Introduce one internal DigiSac-directory persistence boundary using only the
   initialized pool and shared timestamp/serialization primitives that are
   already owned by the application. Move the fixed `departments`/`users`
   resource allowlist, normalized upsert, sync-state writes/due check, and
   de-duplicated user-name read together; do not duplicate SQL, create a pool,
   or make the provider orchestrator a second persistence authority.
3. Keep `db.py` as lifecycle and schema-capability owner without circular
   imports. Retain its current `upsert_digisac_directory()`,
   `mark_directory_sync_attempt()`, `directory_refresh_is_due()`, and
   `resolve_user_names()` facade behavior, or perform one source-confirmed
   atomic consumer migration preserving signatures, returned values, and
   monkeypatch seams.
4. Preserve exact durable semantics: invalid resources still fail with the
   current validation; entries without nonblank string IDs or names remain
   skipped; successful upsert and `last_success_at` publication remain one
   transaction; failed provider acquisition leaves the previous cache intact;
   attempt timestamps and the both-resources cooldown check retain their
   current meaning; and user lookup continues to ignore blank IDs/names and
   return the same ID-to-trimmed-name map. Keep assignment history and mapping
   reads pointed at the same tables and schema.
5. Run focused directory, assignment, mapping, static, and canonical disposable
   PostgreSQL verification. Only after validation, update implementation-era
   documentation/source-map references and Graphify, then close the issue with
   one focused commit.

## Tests

- **Directory and assignment boundary:** `PYTHONPATH=/app python -m pytest -q tests/test_digisac_directory.py tests/test_ticket_assignments.py`
- **Directory consumers:** `PYTHONPATH=/app python -m pytest -q tests/test_department_mapping.py tests/test_acessorias_preparation.py`
- **Static:** `python -m compileall -q src tests alembic scripts` and `npx --yes pyright`
- **Canonical disposable verification:** `PYTHONPATH=/app python scripts/verify.py`
- **Graph:** `graphify update .` after implementation changes.

## Acceptance Criteria

- [x] DigiSac directory cache persistence and user-name lookup are isolated
  behind one cohesive internal boundary instead of being split across
  unrelated sections of `src/core/db.py`.
- [x] `upsert_digisac_directory()`, `mark_directory_sync_attempt()`,
  `directory_refresh_is_due()`, and `resolve_user_names()` remain
  import- and call-compatible for the provider sync, IA worker, assignment
  tests, and other confirmed consumers, or an atomic migration preserves their
  signatures, returned shapes, and monkeypatch seams.
- [x] Exactly one initialized process-local pool and the current Alembic
  schema verification remain in use; no migration, runtime schema creation,
  provider call, new lifecycle, or second persistence authority is introduced.
- [x] Only the existing `departments` and `users` resources are accepted;
  malformed entries are filtered with the same nonblank ID/name rules, names
  and source timestamps are normalized identically, and unknown resources
  retain the current failure behavior.
- [x] Successful directory upsert keeps the same single-transaction table and
  sync-state updates, conflict replacement, row count, and
  `last_attempt_at`/`last_success_at` semantics; provider failure still leaves
  the previous cache available.
- [x] Refresh-due evaluation still requires the same two-resource state and
  cooldown comparison, while user lookup preserves input de-duplication,
  blank-value filtering, and the same ID-to-trimmed-name result.
- [x] The provider sync retains its current authentication, pagination,
  transient retry/backoff, timeout, process-local lock, periodic loop, and
  sanitized observability behavior; no credentials or raw provider payloads
  are added to logs or durable metadata.
- [x] Assignment history, ordered name projection, unresolved external IDs,
  cycle-bounded department mapping, IA message sender names, public HTTP,
  Redis, persistence, authorization/security/privacy, retry/idempotency,
  concurrency, failure, provider, and compatibility semantics remain
  unchanged.
- [x] Focused tests, compileall, strict Pyright, and the canonical disposable
  runner pass, with local/disposable evidence reported separately from
  external-runtime or production evidence.
- [x] README/architecture/plan synchronization where affected, Graphify
  metadata, and source-map references are updated after implementation; the
  issue is closed only after validation and one focused commit.

## References

- Primary cross-cutting contract: `specs/0001-shared-data-and-analysis-contract.md`
  v1.4, especially PostgreSQL authority, synchronized DigiSac directory name
  resolution, unresolved-ID preservation, privacy, and compatibility.
- Ingestion/assignment contract: `specs/0002-digisac-webhook-and-query-api.md`
  v1.6, especially idempotent assignment capture and the unchanged webhook
  boundary.
- Verification contract: `specs/0004-reproducible-verification-baseline.md`
  v1.6.
- Product/architecture: `PRD.md` §§5.2, 5.5, and 8;
  `ARCHITECTURE.md` §§2, 2.1, 9, 12, and 14.
- Schema/source evidence: `alembic/versions/0001_initial.py` lines 109–126;
  `src/core/digisac_directory_repository.py` lines 1–172;
  `src/core/db.py` compatibility exports; `src/core/digisac_directory.py` lines
  12–123; and
  `src/workers/ia_worker.py` lines 397–411 and 1267–1292.
- Plan: `IMPLEMENTATION_PLAN.md` — persistent analysis, assignment history,
  and approved mapping milestones are complete; this is structural maintenance,
  not a new product milestone.
- Related issues: `0030` owns assignment event/history and assignment-name
  projection but explicitly excludes the DigiSac directory repository;
  `0016`, `0020`, and `0026` establish the mapping consumers and cycle-bound
  prerequisites.
- Non-duplicate rationale: no existing issue isolates the DigiSac directory
  cache, sync-state, and user-name lookup. Issues `0028`–`0032` cover contact,
  cycle, assignment, media, and classification persistence, and issue `0030`
  explicitly leaves this directory boundary out of scope.

## Resolution

<!-- Filled by the agent on close. -->

Implemented `src/core/digisac_directory_repository.py` as the cohesive internal
boundary for DigiSac directory upsert, synchronization state, refresh-due
evaluation, and user-name lookup. `src/core/db.py` remains the single
process-local pool, Alembic schema-capability, shared timestamp primitive, and
compatibility-facade owner. The existing provider sync, IA worker, assignment
projection, mapping reads, signatures, transaction boundaries, filtering, and
directory semantics remain unchanged.

Added `tests/test_digisac_directory_repository.py` for repository ownership,
facade identity, and import-order compatibility. No migration, schema change,
provider call, new lifecycle, second pool, persistence authority, or public
contract was introduced.

README, ARCHITECTURE, `specs/README.md`, and `IMPLEMENTATION_PLAN.md` now record
the directory boundary and current local/disposable evidence. Graphify was
refreshed with `graphify update .`.

Validation:

- Baseline focused suites: directory/assignment **7 skipped**; mapping/
  preparation **4 passed, 13 skipped**.
- `PYTHONPATH=/app python -m pytest -q tests/test_digisac_directory_repository.py tests/test_digisac_directory.py tests/test_ticket_assignments.py tests/test_department_mapping.py tests/test_acessorias_preparation.py` — **6 passed, 20 skipped** without a configured test database.
- `python -m compileall -q src tests alembic scripts` — passed.
- `npx --yes pyright` — **0 errors, 0 warnings, 0 informations**.
- `PYTHONPATH=/app python scripts/verify.py` — compileall, Pyright, offline
  **220 passed/69 skipped**, disposable PostgreSQL 16 and Alembic head
  `0020_cycle_contact_provenance`, PostgreSQL **69 passed/220 deselected** —
  all passed.

No migration was required; the existing Alembic-owned schema was reused
unchanged. The evidence is local/disposable and does not claim Redis, provider,
deployment, replica, or production readiness.
