---
id: 0028
title: "Isolate DigiSac contact persistence from the database facade"
type: refactor
status: closed
priority: medium
phase: 5
created_at: 2026-08-20
updated_at: 2026-08-20
closed_at: 2026-08-20
related_issues:
  - "0013"
  - "0014"
  - "0026"
blocked_by: []
affects:
  - src/core/db.py
  - src/core/digisac_contact_hydration.py
  - src/core/digisac_contact_backfill.py
  - src/api/routes.py
  - tests/test_digisac_contact_identity.py
  - tests/test_digisac_contact_backfill.py
  - tests/test_identity_resolution.py
  - tests/test_department_mapping_unit.py
  - tests/test_acessorias_preparation.py
  - README.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
---

## Description

`src/core/db.py` is the process-wide PostgreSQL lifecycle facade, but it also
contains the full DigiSac-contact storage implementation: contact upsert and
source precedence, atomic full-backfill publication, hydration request/claim/
completion/failure transitions, and contact/hydration reads. This cohesive
responsibility spans the contact-specific section beginning with
`_CONTACT_PROVIDER_FIELDS` through `get_digisac_contact_hydration`, while the
same file also owns unrelated schema lifecycle, assignments, classifications,
media, and conversation cycles.

The contact operation surface is independently consumed by ticket-webhook
capture, the hydration worker, the full-backfill service, identity resolution,
department mapping, Acessórias preparation, and PostgreSQL tests. Graphify
confirms direct import edges from both contact orchestration modules to
`db.py`; source inspection confirms further callers and tests import the same
async functions. Other domain-specific PostgreSQL capabilities already keep
their persistence orchestration in dedicated modules (`identity_resolution`,
`department_mapping`, and `acessorias_requests`) while relying on the shared
pool accessor. Leaving the contact repository embedded in the generic facade
makes contact-only changes require navigating unrelated persistence domains and
weakens isolation of its durable invariants.

Extract only the DigiSac-contact persistence/hydration repository behind a
dedicated internal module. Keep `src/core/db.py` as the owner of pool lifecycle,
schema verification, shared serialization/timestamp primitives where they are
already common, and a compatibility facade for its currently imported public
contact functions. The extraction must preserve the current async callable
signatures and all durable behavior; it is not authorization to change the
contact model, schema, provider policy, or workflow.

## Scope

### In scope

- Move the cohesive persistence implementation for `digisac_contacts` and
  `digisac_contact_hydrations` into one focused internal DigiSac-contact
  repository module, using the existing initialized PostgreSQL pool.
- Retain `src.core.db` compatibility exports for every existing public contact
  persistence function, or update all in-repository consumers atomically while
  preserving their import-time and call-time contracts.
- Make ownership explicit: the extracted module owns contact upsert/source
  precedence, atomic backfill publication, hydration state transitions and
  reads; `db.py` retains process lifecycle and unrelated persistence domains.
- Add or adjust focused tests that prove the extraction preserves the existing
  facade and all current contact durable invariants.
- Synchronize the active architecture/source map, plan status, and Graphify
  metadata after implementation, without rewriting completed historical specs
  or issues.

### Out of scope

- Changing public HTTP routes, webhook payload handling, the typed DigiSac
  client, CLI arguments, or the IA contract.
- Any Alembic migration, schema/index/constraint change, backfill execution,
  data rewrite, or retention-policy change.
- Changing contact identity from opaque `contact.id`, adding company matching,
  automatic confirmation, Request creation behavior, or a public/admin
  contact surface.
- Changing authentication, logging/privacy policy, retry limits/backoff,
  idempotency, claim/lease, locking, transaction, concurrency, failure, or
  provider-call semantics.
- Extracting unrelated assignment, classification, media, cycle, identity,
  department-mapping, or Acessórias Request persistence in this issue.

## Implementation Plan

1. Inventory the current public contact persistence exports, direct imports,
   monkeypatch seams, and PostgreSQL tests before moving code. Treat the
   existing async signatures and returned row/count shapes as compatibility
   contracts.
2. Introduce one internal contact persistence boundary that receives only the
   existing shared-pool/lifecycle primitives it needs. Move the contact field
   set, cursor upsert, full-backfill transaction, hydration request/claim/
   completion/failure operations, and contact/hydration reads together; do not
   duplicate SQL or create a second pool.
3. Preserve `db.py` lifecycle ownership and avoid circular imports. Keep its
   contact-facing API import-compatible for existing callers, or make an
   atomic, source-confirmed caller migration that preserves all observable
   behavior and test monkeypatch seams.
4. Verify exact preservation of transaction boundaries: one advisory-locked
   atomic backfill publication; row locking and `SKIP LOCKED` hydration claims;
   lease comparison on completion/failure; source/timestamp precedence; and
   sanitized failure persistence. Do not alter SQL predicates, state values,
   timestamps, retry calculations, or provider invocation boundaries as part
   of this extraction.
5. Run focused offline and disposable-PostgreSQL contact, identity, mapping,
   and preparation tests, followed by the repository static and canonical
   verification commands. Update only implementation-era documentation and
   Graphify after the code passes, then close the issue with one focused commit.

## Tests

- **Focused contact boundary:** `PYTHONPATH=/app python -m pytest -q tests/test_digisac_contact_identity.py tests/test_digisac_contact_backfill.py`
- **Dependent PostgreSQL domains:** `PYTHONPATH=/app python -m pytest -q tests/test_identity_resolution.py tests/test_department_mapping_unit.py tests/test_acessorias_preparation.py`
- **Static:** `python -m compileall -q src tests alembic scripts` and `npx --yes pyright`
- **Canonical disposable verification:** `PYTHONPATH=/app python scripts/verify.py`
- **Graph:** `graphify update .` after implementation changes.

## Acceptance Criteria

- [x] DigiSac contact persistence/hydration is isolated behind one cohesive
  internal boundary rather than embedded with unrelated database domains in
  `src/core/db.py`.
- [x] The existing public async contact persistence API remains import- and
  call-compatible for current webhook, hydration, backfill, preparation, and
  test consumers, or an atomic migration preserves those contracts and seams.
- [x] Exactly one existing initialized process-local pool is used; there is no
  new connection lifecycle, provider call, runtime schema creation, migration,
  or persistence authority.
- [x] `contact.id` remains the opaque canonical identity; no phone/email/name/
  group matching, company resolution, confirmation, Request permission, route,
  CLI, or IA behavior is introduced or changed.
- [x] Upsert replay/concurrency and source/timestamp precedence preserve the
  newest valid contact metadata without duplicate contacts or deletion inferred
  from absence.
- [x] Full backfill remains globally deduplicated, advisory-locked, atomic, and
  rollback-safe; partial or invalid acquisition cannot publish a partial
  snapshot or change absence semantics.
- [x] Hydration remains deduplicated and recoverable with the same claim,
  `SKIP LOCKED`, lease, retry, terminal-failure, and sanitized-error behavior;
  no inline Contacts call is introduced into the webhook path.
- [x] Privacy, authorization, public HTTP, retry/idempotency/concurrency, and
  provider contracts remain unchanged, with no secrets or raw contact data
  added to logs, fixtures, or persisted operational metadata.
- [x] Focused tests, compileall, strict Pyright, and the canonical disposable
  runner pass with results recorded accurately and separately from production
  evidence.
- [x] README/architecture/plan synchronization (where affected), Graphify
  metadata, and source-map references are updated after implementation; the
  issue is closed only after validation and one focused commit.

## References

- Primary contract: `specs/0008-digisac-contact-identity-foundation.md` v1.4,
  especially data integrity, ingestion/hydration/backfill, compatibility, and
  verification requirements.
- Related contracts: `specs/0001-shared-data-and-analysis-contract.md`,
  `specs/0002-digisac-webhook-and-query-api.md`, and
  `specs/0004-reproducible-verification-baseline.md`.
- Product/architecture: `PRD.md` §§5.1, 5.5, and 8;
  `ARCHITECTURE.md` §§2.1, 9, and 14.
- Plan: `IMPLEMENTATION_PLAN.md` — completed Milestone B and the durable
  schema/verification baseline; this is a structural maintenance slice, not a
  new milestone.
- Related implementation issues: `0013` (contact identity foundation), `0014`
  (full Contacts backfill), and `0026` (canonical ticket-contact provenance).
- Current evidence: `src/core/db.py`, `src/core/digisac_contact_hydration.py`,
  `src/core/digisac_contact_backfill.py`, `src/api/routes.py`, and their direct
  contact/identity/mapping/preparation tests. Graphify path queries show the
  direct `digisac_contact_hydration.py` → `db.py` and
  `digisac_contact_backfill.py` → `db.py` dependencies.

## Resolution

- **Implementation:** Added `src/core/digisac_contact_repository.py` as the
  single owner of DigiSac contact upsert/source precedence, atomic full-backfill
  publication, hydration request/claim/completion/failure transitions, and
  contact/hydration reads. `src/core/db.py` retains the existing async API
  through lazy compatibility delegates and remains the owner of the
  process-local pool, schema verification, and shared timestamp/row
  serialization primitives.
- **Compatibility and invariants:** Existing async signatures and import paths
  remain available to routes, hydration/backfill orchestration, identity and
  preparation consumers, and tests. The extraction changes no SQL predicate,
  transaction boundary, advisory lock, `SKIP LOCKED` claim, lease comparison,
  source precedence, retry calculation, provider call, schema, or canonical
  `contact.id` behavior. No second pool, migration, provider call, route, CLI,
  or runtime schema operation was added.
- **Tests and validation:** Focused contact tests passed **19 passed, 6
  skipped**; dependent identity/mapping/preparation tests passed **6 passed, 8
  skipped**. `python -m compileall -q src tests alembic scripts` passed; strict
  Pyright passed with **0 errors, 0 warnings, 0 informations**; and
  `PYTHONPATH=/app python scripts/verify.py` passed compileall, Pyright, offline
  pytest (**213 passed, 69 skipped**), disposable PostgreSQL/Alembic head
  `0020_cycle_contact_provenance`, and PostgreSQL pytest (**69 passed, 213
  deselected**). `git diff --check` passed.
- **Migrations and external evidence:** No migration or data operation was
  required. PostgreSQL evidence used only the runner-owned disposable target;
  no provider credential, Redis deployment, or production target was used.
- **Documentation and Graphify:** Updated `README.md`, `ARCHITECTURE.md`,
  `IMPLEMENTATION_PLAN.md`, SPEC-0008, and `specs/README.md`; Graphify was
  refreshed after the implementation changes.
