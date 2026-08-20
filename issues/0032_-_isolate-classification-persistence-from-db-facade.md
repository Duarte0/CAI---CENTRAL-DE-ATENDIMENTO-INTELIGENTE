---
id: 0032
title: "Isolate classification persistence from the database facade"
type: refactor
status: closed
priority: medium
phase: 5
created_at: 2026-08-20
updated_at: 2026-08-20
closed_at: 2026-08-20
related_issues:
  - "0004"
  - "0029"
blocked_by: []
affects:
  - src/core/db.py
  - src/workers/ia_worker.py
  - src/utils/backfill_redis_history.py
  - tests/test_ia_history_db.py
  - tests/test_postgres_evolution.py
  - tests/test_postgres_concurrency.py
  - README.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
---

## Description

`src/core/db.py` is the process-wide PostgreSQL lifecycle and schema-capability
facade, but it also embeds the durable classification repository.  The section
from `_intent_type()` through `ticket_has_classification()` owns classification
normalization, transactional insertion into `ia_classifications`, ordered and
deduplicated `classification_messages` links, UUIDv7/idempotency-key handling,
protocol projection, and legacy existence queries.  These concerns are used by
the IA worker's terminal-cycle processing, the Redis-history backfill, the
Acessórias preparation/Request test fixtures, and PostgreSQL evolution and
concurrency coverage, while unrelated assignment, directory, contact, media,
and cycle persistence remains in the same generic facade.

This is a cohesive durable boundary rather than a file-size cleanup.  The
existing contract requires one transaction to retain a valid classification
identity and its message links; a concurrent caller with the same idempotency
key must obtain the same persisted identity; ordered source message IDs remain
in the JSONB snapshot while duplicate association rows are suppressed; an
unknown complete textual intent normalizes to `other`; and protocol remains
application metadata updated independently of the model context.  Extracting
only this repository behind an internal module can preserve those guarantees
while keeping `src.core.db` as the sole pool/lifecycle and schema-capability
owner and as a compatibility import surface.

## Scope

### In scope

- Extract classification insertion, classification-message association,
  protocol update, and classification existence queries into one focused
  internal persistence boundary using the existing initialized PostgreSQL pool
  and the current schema-capability information.
- Keep `src.core.db` responsible for database URL/pool lifecycle, Alembic-head
  verification, shared primitives, and compatibility exports for
  `insert_classification()`, `update_analysis_protocol()`,
  `classification_exists()`, and `ticket_has_classification()`, unless all
  source-confirmed consumers and monkeypatch seams move atomically with the
  same observable contract.
- Preserve direct consumers in the IA worker and Redis-history backfill and
  add or adjust focused coverage only as needed to prove the extracted boundary
  is behaviorally equivalent.
- Synchronize implementation-era architecture/source-map, plan status, and
  Graphify metadata after implementation.

### Out of scope

- Any Alembic migration, schema/index/constraint change, data rewrite,
  classification-message backfill, retention-policy change, runtime schema
  creation, or second persistence authority.
- Changing the IA four-field output contract, intent taxonomy, validation,
  prompt/context construction, protocol/display-title behavior, public routes
  or response bodies, Redis keys/payloads, worker or CLI interfaces, logs, or
  provider calls.
- Changing transaction scope, idempotency-key conflict behavior, UUIDv7
  generation, JSONB message snapshot semantics, message-link ordering or
  deduplication, error behavior, concurrency handling, or compatibility for
  historical backfill.
- Extracting contact persistence (issue 0028), cycle persistence (issue 0029),
  ticket-assignment persistence (issue 0030), durable-media persistence (issue
  0031), or directory, identity, mapping, or Acessórias Request repositories.

## Implementation Plan

1. Inventory every classification-facing export, IA/backfill consumer, direct
   PostgreSQL test, migration-capability dependency, and monkeypatch seam.
   Treat async signatures, `ClassificationIdentity`, boolean returns, and
   existing legacy-schema paths as compatibility contracts.
2. Introduce one internal classification persistence module that receives only
   the initialized pool, schema capabilities, and shared timestamp/row
   primitives required by its current implementation.  Move intent
   normalization at the persistence boundary, classification insert,
   idempotency conflict lookup, ordered message-link write, protocol update,
   and existence reads together; do not duplicate SQL or create a pool.
3. Keep `db.py` as lifecycle and schema-capability owner without circular
   imports.  Retain its classification-facing facade, or perform one
   source-confirmed atomic consumer migration that preserves import behavior
   and test seams.
4. Preserve exact durable semantics: write the supplied `message_ids` snapshot
   unchanged in its current JSONB form; create at most one association per
   message ID with the first observed position; use the existing conditional
   idempotency conflict predicate and return the already-persisted identity;
   retain current behavior when identity/link schema capabilities are absent;
   normalize only unsupported complete intent text to `other`; and keep
   protocol updates idempotent without placing protocol in model input.
5. Run focused classification, IA/backfill-consumer, evolution, and concurrency
   coverage, followed by static and canonical disposable verification.  Update
   implementation-era documentation and Graphify only after validation, then
   close the issue with one focused commit.

## Tests

- **Classification persistence and worker consumer:** `PYTHONPATH=/app python -m pytest -q tests/test_ia_history_db.py tests/test_ia_worker_intent.py tests/test_backfill_ticket_assignments.py`
- **Schema evolution and dependent fixtures:** `PYTHONPATH=/app python -m pytest -q tests/test_postgres_evolution.py tests/test_ticket_assignments.py tests/test_acessorias_preparation.py tests/test_acessorias_requests.py`
- **Concurrency:** `PYTHONPATH=/app python -m pytest -q tests/test_postgres_concurrency.py`
- **Static:** `python -m compileall -q src tests alembic scripts` and `npx --yes pyright`
- **Canonical disposable verification:** `PYTHONPATH=/app python scripts/verify.py`
- **Graph:** `graphify update .` after implementation changes.

## Acceptance Criteria

- [x] Classification persistence is isolated behind one cohesive internal
  boundary instead of being embedded with unrelated domains in `src/core/db.py`.
- [x] `insert_classification()`, `update_analysis_protocol()`,
  `classification_exists()`, and `ticket_has_classification()` remain import-
  and call-compatible for workers, utilities, tests, and dependent fixtures,
  or an atomic migration preserves their signatures, returned shapes, and
  monkeypatch seams.
- [x] Exactly one initialized process-local pool and the current Alembic schema
  verification/capability behavior remain in use; no migration, runtime schema
  creation, provider call, new lifecycle, or persistence authority is added.
- [x] A valid classification persists the same JSONB message snapshot and the
  same durable classification fields, while `classification_messages` retains
  one link per distinct message ID at its first supplied position.
- [x] The current UUIDv7 and conditional idempotency-key behavior remains
  atomic under concurrent callers: all callers for the same valid key receive
  the same stored identity and no extra classification or link rows are
  created.
- [x] Unsupported complete textual intent values still normalize to `other`,
  while the four-field IA contract, title/description handling, model/context
  behavior, and persisted historical data semantics remain unchanged.
- [x] Protocol updates remain idempotent and preserve their current result when
  a classification exists; protocol is still application metadata and never
  becomes model input.
- [x] Redis-history backfill and all existing worker, utility, public HTTP,
  persistence, retry, idempotency, concurrency, failure, authorization,
  security/privacy, provider, and compatibility semantics remain unchanged;
  no secret, raw payload, model response, or sensitive content is added to
  logs, fixtures, or durable metadata.
- [x] Focused tests, compileall, strict Pyright, and the canonical disposable
  runner pass, with local/disposable evidence distinguished from external
  runtime or production evidence.
- [x] README/architecture/plan synchronization where affected, Graphify
  metadata, and source-map references are updated after implementation; the
  issue is closed only after validation and one focused commit.

## References

- Primary contract: `specs/0001-shared-data-and-analysis-contract.md` v1.4,
  especially durable classification identity, IA fields, protocol separation,
  ordered message association, privacy, and concurrent idempotency.
- Verification contract: `specs/0004-reproducible-verification-baseline.md`
  v1.6.
- Product/architecture: `PRD.md` §§5.4, 6, and 8; `ARCHITECTURE.md` §§2, 5,
  8, 9, 12, and 14.
- Plan: `IMPLEMENTATION_PLAN.md` — completed persistent-analysis baseline;
  this is structural maintenance, not a new milestone.
- Related issues: `0004` defines the canonical disposable verification;
  `0029` is the separate conversation-cycle persistence extraction whose worker
  flow consumes the classification result.
- Current evidence: `src/core/classification_repository.py` classification
  persistence boundary and `src/core/db.py` compatibility exports;
  `src/workers/ia_worker.py` classification insertion; and
  `src/utils/backfill_redis_history.py` legacy existence/backfill path; plus
  `tests/test_ia_history_db.py`, `tests/test_postgres_evolution.py`, and
  `tests/test_postgres_concurrency.py`.
- Non-duplicate rationale: issues `0028`–`0031` isolate DigiSac contacts,
  conversation cycles, ticket assignments, and durable media respectively.
  No existing issue isolates the classification and classification-message
  repository while preserving its current facade and semantics.

---

## Resolution

Implemented `src/core/classification_repository.py` as the cohesive internal
boundary for classification insertion, ordered `classification_messages`
links, protocol updates, and classification existence reads. `src/core/db.py`
remains the sole process-local pool, Alembic schema-capability, shared timestamp
primitive, and compatibility-facade owner; all existing worker, Redis-history
backfill, utility, test, and dependent fixture imports remain unchanged.

Added a structural facade-compatibility regression test. No migration, schema
change, provider call, new lifecycle, persistence authority, or IA/public
contract change was introduced.

README, ARCHITECTURE, SPEC-0001, SPEC-0004, `specs/README.md`, and
`IMPLEMENTATION_PLAN.md` now document the boundary and validation evidence;
Graphify metadata was refreshed with `graphify update .`.

Validation:

- `PYTHONPATH=/app python -m pytest -q tests/test_ia_history_db.py tests/test_ia_worker_intent.py tests/test_backfill_ticket_assignments.py tests/test_postgres_evolution.py tests/test_ticket_assignments.py tests/test_acessorias_preparation.py tests/test_acessorias_requests.py tests/test_postgres_concurrency.py tests/test_classification_repository.py` — 37 passed, 28 skipped without a configured test database.
- `python -m compileall -q src tests alembic scripts` — passed.
- `npx --yes pyright` — 0 errors, 0 warnings, 0 informations.
- `PYTHONPATH=/app python scripts/verify.py` — compileall, Pyright, offline 218 passed/69 skipped, disposable PostgreSQL 16 and Alembic head `0020_cycle_contact_provenance`, PostgreSQL 69 passed/218 deselected — all passed.

The evidence is local/disposable and does not claim Redis, provider, deployment,
replica, or production readiness.
