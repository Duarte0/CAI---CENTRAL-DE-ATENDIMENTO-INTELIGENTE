---
id: 0030
title: "Isolate ticket-assignment persistence from the database facade"
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
  - "0026"
  - "0028"
  - "0029"
blocked_by: []
affects:
  - src/core/db.py
  - src/core/ticket_assignment_repository.py
  - src/api/routes.py
  - src/workers/ia_worker.py
  - src/core/digisac_directory.py
  - tests/test_ticket_assignments.py
  - tests/test_digisac_directory.py
  - tests/test_department_mapping.py
  - tests/test_postgres_concurrency.py
  - tests/test_ticket_assignment_repository.py
  - README.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
---

## Description

`src/core/db.py` owns the process-wide PostgreSQL lifecycle, but it also holds
the ticket-assignment repository in two distant sections: `_record_ticket_assignment_sync()` /
`record_ticket_assignment()` write the idempotency key and chronological history,
while `_resolve_ticket_assignments_sync()` / `resolve_ticket_assignments()` read
that history with the DigiSac department and user directory names. This splits
one durable responsibility across the generic database facade and makes a
ticket-assignment-only change require navigation through unrelated lifecycle,
directory, contact, media, cycle, classification, identity, mapping, and Request
persistence.

The assignment boundary is independently exercised by webhook capture, the IA
worker's historical name projection, directory refresh, and cycle-scoped
department mapping. Its current contract is already defined: a `ticket.updated`
observation is captured idempotently; history is chronological; duplicate event
keys and immediately repeated assignments do not create another history row;
name projection retains ordering and reports unresolved provider IDs without
treating them as names; and mapping selects only a persisted assignment inside
the target cycle's bounds. Extracting this cohesive repository behind one
internal module can preserve that behavior while leaving `src.core.db` as the
single pool/lifecycle facade and compatibility import surface.

## Scope

### In scope

- Extract ticket-assignment event-key persistence, chronological assignment
  writes, and assignment-plus-directory name projection into one focused
  internal persistence boundary using the existing initialized PostgreSQL pool.
- Keep `src.core.db` responsible for pool lifecycle, schema verification, and
  shared primitives, and retain import- and call-compatible exports for
  `record_ticket_assignment()` and `resolve_ticket_assignments()`, unless all
  confirmed in-repository consumers and monkeypatch seams move atomically with
  the same observable contract.
- Preserve the existing direct consumers in webhook capture, the IA worker,
  directory-related code, and tests; add or adjust focused coverage only to
  demonstrate that the extracted boundary preserves their contracts.
- Synchronize implementation-era architecture/source-map, plan status, and
  Graphify metadata after implementation.

### Out of scope

- Any Alembic migration, schema/index/constraint change, data rewrite,
  backfill execution, retention-policy change, or runtime schema creation.
- Changing webhook normalization, event-key construction, routes, response
  bodies, Redis queues/payloads, worker or CLI interfaces, provider calls, or
  directory refresh/retry policy.
- Changing assignment-history selection, source timestamp parsing, duplicate
  suppression, transaction scope, idempotency, concurrency, error handling,
  mapping rules, identity resolution, cycle behavior, classification, or
  Acessórias Request behavior.
- Extracting DigiSac contact persistence (issue 0028), conversation-cycle
  persistence (issue 0029), the DigiSac directory repository, or any other
  database domain.

## Implementation Plan

1. Inventory the assignment-facing exports, direct imports, route/worker
   monkeypatch seams, SQL consumers, and PostgreSQL tests. Treat async
   signatures and the returned boolean and four-list tuple shapes as
   compatibility contracts.
2. Introduce one internal ticket-assignment persistence module that receives
   only the existing initialized pool and shared timestamp primitives it needs.
   Move event-key insertion, prior-assignment comparison, chronological history
   insertion, and assignment/directory projection together; do not add a pool,
   duplicate SQL, or create a new persistence authority.
3. Keep `db.py` as lifecycle and schema-capability owner without circular
   imports. Retain its assignment-facing facade, or make one source-confirmed,
   atomic consumer migration that preserves current imports and test seams.
4. Preserve exact durable semantics: event-key uniqueness is claimed in the
   same transaction as the history decision; an event with no department and
   user is ignored; an immediately repeated assignment yields no history row;
   history and resolved/unresolved outputs remain ordered and deduplicated as
   they are now; and directory-name changes affect projection rather than
   historical provider IDs. Do not change mapping's cycle-bounded direct SQL
   selection or its snapshot behavior.
5. Run focused PostgreSQL route, assignment, directory, mapping, and
   concurrency coverage, then static and canonical disposable verification.
   Update implementation-era documentation and Graphify only after validation,
   then close with one focused commit.

## Tests

- **Assignment and webhook boundary:** `PYTHONPATH=/app python -m pytest -q tests/test_ticket_assignments.py tests/test_history_finalization_webhook.py`
- **Directory and mapping consumers:** `PYTHONPATH=/app python -m pytest -q tests/test_digisac_directory.py tests/test_department_mapping.py tests/test_acessorias_preparation.py`
- **Concurrency:** `PYTHONPATH=/app python -m pytest -q tests/test_postgres_concurrency.py`
- **Static:** `python -m compileall -q src tests alembic scripts` and `npx --yes pyright`
- **Canonical disposable verification:** `PYTHONPATH=/app python scripts/verify.py`
- **Graph:** `graphify update .` after implementation changes.

## Acceptance Criteria

- [x] Ticket-assignment persistence and projection are isolated behind one
  cohesive internal boundary instead of being split across unrelated sections
  of `src/core/db.py`.
- [x] `record_ticket_assignment()` and `resolve_ticket_assignments()` remain
  import- and call-compatible for routes, workers, utilities, and tests, or an
  atomic migration preserves their current returned shapes and monkeypatch
  seams.
- [x] Exactly one initialized process-local pool and the existing Alembic
  schema verification remain in use; no lifecycle, migration, provider call,
  runtime schema creation, or new persistence authority is introduced.
- [x] Webhook capture keeps the same timestamp/event-key construction and safe
  failure behavior; public routes, responses, Redis publication, worker and
  CLI interfaces remain unchanged.
- [x] Event-key idempotency, chronological ordering, duplicate adjacent-pair
  suppression, source event fields, and transaction boundaries preserve the
  current history rows under replay and concurrent writers.
- [x] Assignment-name projection retains its current ordered, deduplicated
  department/agent lists and unresolved-ID lists, and a directory refresh still
  changes displayed names without rewriting assignment history.
- [x] Cycle-scoped department mapping continues to select only the persisted
  assignment within the cycle interval, with the same stable-ID rule,
  validation-snapshot, identity, and Request gates.
- [x] Persistence semantics, authorization/security/privacy, retry,
  idempotency, concurrency, failure behavior, provider contracts, and
  compatibility remain unchanged; no secret, raw payload, or sensitive data is
  added to logs, fixtures, or durable metadata.
- [x] Focused tests, compileall, strict Pyright, and the canonical disposable
  runner pass, with local/disposable evidence distinguished from production or
  external-runtime evidence.
- [x] README/architecture/plan synchronization where affected, Graphify
  metadata, and source-map references are updated after implementation; the
  issue is closed only after validation and one focused commit.

## References

- Primary contracts: `specs/0002-digisac-webhook-and-query-api.md` v1.6 and
  `specs/0010-digisac-acessorias-department-mapping.md` v1.3, especially
  idempotent assignment capture, chronological/cycle-bounded selection, and
  compatibility requirements.
- Verification contract: `specs/0004-reproducible-verification-baseline.md`
  v1.6.
- Product/architecture: `PRD.md` §§5.2, 5.5, and 8; `ARCHITECTURE.md` §§2,
  2.1, 9, 12, and 14.
- Plan: `IMPLEMENTATION_PLAN.md` — completed persistent-analysis and approved
  Acessórias milestones; this is structural maintenance, not a new milestone.
- Related issues: `0016`, `0020`, and `0026` establish the assignment-history
  consumers and mapping prerequisites; `0028` and `0029` are separate
  persistence extractions from the same facade.
- Current evidence: `src/core/ticket_assignment_repository.py` assignment
  persistence and projection; compatibility exports in `src/core/db.py`;
  `src/api/routes.py` ticket-assignment capture; `src/workers/ia_worker.py`
  assignment-name resolution; `src/core/digisac_directory.py`; and
  `tests/test_ticket_assignments.py`, `tests/test_digisac_directory.py`,
  `tests/test_department_mapping.py`, and `tests/test_postgres_concurrency.py`.
- Non-duplicate rationale: no existing issue isolates ticket-assignment
  persistence. Issue `0028` covers only DigiSac contacts/hydration, and issue
  `0029` covers only conversation cycles; issues `0016`, `0020`, and `0026`
  change or consume assignment behavior rather than extracting this repository
  while preserving its facade.

## Resolution

Implemented issue 0030 as a behavior-preserving ticket-assignment
persistence-boundary extraction.

### Implementation

- Moved assignment event-key persistence, chronological history writes, and
  ordered assignment-plus-directory name projection into
  `src/core/ticket_assignment_repository.py`.
- Kept `src/core/db.py` as the single PostgreSQL pool/lifecycle,
  schema-capability, and compatibility-facade owner. The existing async
  exports remain direct aliases with unchanged signatures and returned tuple or
  boolean shapes; no second pool, migration, runtime schema creation, or
  persistence authority was added.
- Added repository-ownership coverage while retaining the existing webhook,
  directory, mapping, worker, and concurrency consumers unchanged.

### Tests and validation

- `PYTHONPATH=/app python -m pytest -q tests/test_ticket_assignment_repository.py tests/test_ticket_assignments.py tests/test_history_finalization_webhook.py`: 4 passed, 4 skipped.
- `PYTHONPATH=/app python scripts/verify.py`: compileall, strict Pyright,
  offline pytest (**215 passed, 69 skipped**), disposable PostgreSQL 16 and
  Alembic head `0020_cycle_contact_provenance`, and PostgreSQL pytest (**69
  passed, 215 deselected**); all stages passed and the temporary Compose
  project was removed.
- `git diff --check`: passed.

### Migrations

N/A. The existing Alembic-owned schema was reused unchanged.

### Documentation and key decisions

- Updated SPEC-0002, SPEC-0010, SPEC-0004, `specs/README.md`, `README.md`,
  `ARCHITECTURE.md`, and `IMPLEMENTATION_PLAN.md` with the implementation
  boundary and local/disposable evidence.
- Preserved webhook timestamp/event-key construction, transaction scope,
  duplicate suppression, ordering, unresolved-ID reporting, cycle-bounded
  mapping reads, privacy behavior, and all provider/workflow contracts.
