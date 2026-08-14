---
id: 0016
title: "Implement DigiSac–Acessórias department mapping"
type: feature
status: closed
priority: high
phase: 4
created_at: 2026-08-14
updated_at: 2026-08-14
closed_at: 2026-08-14
related_issues:
  - "0012"
  - "0013"
  - "0014"
  - "0015"
blocked_by:
  - "0012"
  - "0013"
  - "0014"
  - "0015"
affects:
  - alembic/versions/
  - src/core/
  - tests/
  - scripts/verify.py
  - IMPLEMENTATION_PLAN.md
  - ARCHITECTURE.md
  - PRD.md
  - specs/README.md
---

## Description

Implement the next approved Acessórias increment: a PostgreSQL-authoritative,
auditable mapping from the current DigiSac department of a resolved
conversation/cycle to one Acessórias department. The mapping is configuration
and validation state, not an IA decision or name-based inference, and must
produce durable cycle evidence that a later Request operation can consume
without changing classification or historical results.

**Plan/spec references:** `IMPLEMENTATION_PLAN.md`, **Approved Acessórias
milestones**, item 4, **P1 | implementation-ready after Milestone C | specified**
Milestone D — DigiSac Department → Acessórias Department Mapping; primary
contract `SPEC-0010` v1.1; cross-cutting contracts `SPEC-0001`, `SPEC-0003`,
`SPEC-0004`, and directory/identity contracts `SPEC-0007`–`SPEC-0009`.

**Dependencies:** closed issues `0012`, `0013`, `0014`, and `0015`; Alembic
head `0017_digisac_acessorias_identity`; the existing Acessórias directory
tables and current `company_departments` relation; the identity resolver's
confirmed company outcome; the persisted cycle/assignment boundary; the
existing PostgreSQL transaction/pool; and the disposable PostgreSQL runner.
SPEC-0010 v1.1 already approves global stable-ID mapping, one active rule per
DigiSac department, many-to-one targets, active/inactive lifecycle, and the
initial `manual_db` procedure with an optional actor.

**Verified gap:** the current checkout has assignment history, Acessórias
department/company relationship state, and immutable identity-resolution
outcomes, but no department-mapping migration, durable rule/audit state,
manual administration operation, cycle-mapping snapshot, or evaluation tests.
The current implementation does not select a department by mapping, and
`intent_type`, names, history, or first-match fallbacks must not be introduced
as substitutes. The next Request milestone therefore has no durable,
validated department fact to consume.

Expected outcome: an explicitly administered active rule can map a confirmed
company's current DigiSac department to an Acessórias department only when the
target is present and active in that company's current directory relationship;
all missing, inactive, ambiguous, invalid, concurrent, and failed cases remain
persisted, sanitized, recoverable, and unable to create a Request.

## Scope

### In scope

- Add an additive Alembic revision after `0017_digisac_acessorias_identity`
  for global mapping rules, lifecycle/audit state, and per-cycle mapping
  evaluation/snapshot state. Preserve stable external DigiSac and Acessórias
  department identities, enforce one active rule per DigiSac department, allow
  multiple DigiSac departments to target one Acessórias department, and add
  references/indexes/checks required by SPEC-0010.
- Implement typed PostgreSQL persistence and the internal mapping boundary.
  PostgreSQL remains the only durable authority; Redis must not hold the only
  rule or snapshot and no hardcoded mapping may be added to Python, prompts, or
  environment variables.
- Implement the initial transactional `manual_db` administration operation
  for explicit stable IDs, including create/activate/inactivate behavior,
  sanitized reason/source/metadata, optional actor handling, serialized
  updates, preserved historical audit, and safe replay.
- Evaluate a cycle using the existing persisted current DigiSac department
  contract and the identity resolver's `confirmed` company only. Find the
  single active global rule, resolve its Acessórias target, and validate that
  the target is currently available through that company's
  `company_departments` relation. Persist the rule/version, validation facts,
  source references, state, timestamp, and sanitized reason in a separate
  cycle snapshot/evaluation record.
- Make evaluation and administration conflict-safe and convergent. Missing or
  inactive rules, non-confirmed identity (`ambiguous`, `unresolved`, or other
  non-eligible state), inactive/missing companies or departments, absent
  current relationships, incomplete directory state, lock conflict, and
  transaction failure must remain explicit `unresolved`/`invalid` operational
  outcomes as defined by the spec, never trigger a fallback selection, and
  never alter a completed classification or terminal snapshot.
- Add deterministic unit tests and disposable-PostgreSQL tests for migration,
  rule lifecycle, audit, concurrency, evaluation, snapshot immutability,
  stable-ID rename behavior, privacy, and the two-DigiSac-to-one-Acessórias
  mapping case. Extend schema reset/verification registration only as needed
  for the new PostgreSQL-backed tests.
- On completion, update implementation-derived documentation and Graphify
  metadata through the established workflow, and synchronize the exact
  Milestone D status/evidence in `IMPLEMENTATION_PLAN.md` as part of closing
  the build issue.

### Out of scope

- Acessórias or DigiSac synchronization changes, contact matching or
  confirmation, changes to the completed identity/directory foundations, or
  any provider write.
- IA, `intent_type`, confidence, names, fuzzy matching, first-match or
  default-department fallback, responsible user, historical Request, or any
  other unapproved routing input.
- Request creation, retry/reconciliation, lifecycle, webhook/finalization
  changes, new HTTP routes, public/admin UI, or a public refresh/configuration
  endpoint. SPEC-0011 remains a later milestone.
- Redis-backed authority, production/provider acceptance, real credentials,
  deployment/rollout changes, retention policy, hard deletion of rules/audit
  or snapshots, and unrelated cleanup.
- Retroactively rewriting a terminal cycle snapshot or an already persisted
  Request when a rule or directory relationship later changes.

## Implementation Plan

1. Reconfirm the current Alembic head, `conversation_processing_cycles` and
   `ticket_assignment_history` semantics, identity-resolution states, the
   Acessórias directory's stable IDs and current company-department relation,
   PostgreSQL transaction helpers, and the SPEC-0004 runner boundary. Define
   typed mapping inputs/outputs so database rows do not leak provider-shaped
   data or sensitive values into logs.
2. Add one additive migration after `0017_digisac_acessorias_identity` for
   mapping rules, auditable lifecycle/administration transitions, and cycle
   evaluation snapshots. Enforce nonblank safe fields, stable-ID references,
   allowed states, timestamps, one active mapping per DigiSac department, and
   data-preserving downgrade refusal. Do not create schema at application
   startup or mutate unrelated completed tables.
3. Implement the transactional `manual_db` operation with explicit stable
   department IDs. Lock the affected rule scope, reject an active duplicate,
   preserve prior versions/transitions when inactivating or replacing a rule,
   record the source and sanitized reason, and leave the actor unset when no
   trustworthy administrative identity exists. Replaying the same operation
   must not duplicate rules or audit records.
4. Implement cycle evaluation at the existing PostgreSQL boundary. Obtain the
   current DigiSac department according to the established cycle/assignment
   contract, require exactly one confirmed identity outcome, load the active
   global rule by stable ID, and validate the mapped target against the
   resolved company's current `company_departments` state. Persist a resolved
   snapshot only for the valid case; otherwise persist the specified
   unresolved/invalid state and sanitized reason without selecting another
   department. Never use IA output, names, historical Request data, or a
   default.
5. Serialize concurrent administration/evaluation and make replay idempotent.
   A later directory or rule change must produce a new evaluation where needed
   and must not rewrite a terminal cycle snapshot. A failed lock, incomplete
   directory read, or transaction rollback must preserve the last valid rule
   and snapshot and must not modify classification state.
6. Add focused deterministic tests for active/inactive lifecycle, one-active
   uniqueness, many-to-one target mappings, confirmed versus non-confirmed
   identity, unavailable company departments, invalid/incomplete directory
   state, stable-ID renames, replay, concurrent operations, rollback,
   terminal-snapshot preservation, and sanitized logs/state. Add PostgreSQL
   coverage for the migration head, foreign keys, constraints, audit, and
   transaction behavior.
7. Run focused tests, the applicable offline suite, disposable PostgreSQL
   verification, compileall, strict Pyright, `git diff --check`, and
   `graphify update .`. Record unavailable prerequisites separately from
   passes, review the focused diff, and synchronize only implementation-derived
   documentation plus the exact Milestone D evidence. Close this issue only
   after implementation, tests, documentation/Graphify updates, and
   `IMPLEMENTATION_PLAN.md` sync are included in one focused commit.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migrations:** PostgreSQL is the durable authority for rules, audit,
  and cycle snapshots. Use an additive Alembic revision after `0017`; preserve
  historical rules/transitions and snapshots; refuse downgrade before data
  loss when this state is populated; and do not use startup initialization or
  legacy SQL to create the schema.
- **Compatibility:** preserve the existing HTTP routes, HMAC webhook,
  assignment history, IA/classification contract, finalization behavior,
  Acessórias refresh, DigiSac contact behavior, identity outcomes, and Redis
  transport boundary. This is an internal capability with no public HTTP
  surface.
- **Integrity/concurrency:** enforce one active rule per DigiSac department
  while permitting explicit many-to-one targets. Lock rule and cycle scopes as
  needed to prevent duplicate active rules, lost administration, duplicate
  audit/snapshot rows, arbitrary target selection, and silent terminal-state
  rewrites.
- **Security/privacy:** logs, metrics, exceptions, fixtures, and operational
  state may expose only safe IDs, rule/version/state names, counts, timestamps,
  and sanitized categories. Do not expose contact identifiers, conversation
  content, raw payloads, tokens, headers, or secrets.
- **Observability:** record safe operation/cycle references, rule version,
  result state, duration/counts, and sanitized failure/conflict category so
  operators can distinguish missing configuration, invalid directory state,
  lock failure, and transaction failure without PII.
- **Rollout:** validation is local deterministic/disposable-PostgreSQL
  evidence only. No provider credential, production schedule, deployment, or
  production acceptance is established by this issue.

## Tests

- **Focused mapping tests:** run the new deterministic mapping unit tests for
  rule lifecycle, stable-ID behavior, evaluation states, prohibited fallbacks,
  replay, concurrency, snapshot preservation, and sanitization.
- **PostgreSQL:** run the new `postgres`-marked mapping tests against the
  disposable database and verify migration head, references, active-rule
  uniqueness, many-to-one mappings, audit, transactional rollback, and
  terminal-snapshot preservation.
- **Offline suite:**
  `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`
- **Static/repository validation:**
  `python -m compileall -q src tests alembic scripts`, `npx --yes pyright`,
  `git diff --check`, and focused migration/schema inspection.
- **Canonical runner:**
  `PYTHONPATH=/app python scripts/verify.py` when disposable PostgreSQL/Docker
  prerequisites are available; record compileall, Pyright, offline, migration,
  and PostgreSQL stages separately, including unavailable prerequisites.
- **Graph/documentation:** run `graphify update .` after implementation and
  verify `SPEC-0010` v1.1, the plan, architecture/PRD traceability, and current
  head/schema claims remain consistent.

## Acceptance Criteria

- [x] An additive Alembic revision after `0017_digisac_acessorias_identity`
  creates durable mapping-rule, lifecycle/audit, and cycle-evaluation state
  with safe references, checks, timestamps, indexes, and a data-preserving
  downgrade guard.
- [x] PostgreSQL enforces at most one active rule for each DigiSac department,
  while two or more DigiSac departments may explicitly map to the same
  Acessórias department; rule activation/inactivation preserves history.
- [x] The `manual_db` operation accepts explicit stable IDs, records the
  approved source/reason/timestamps, leaves the actor absent without a
  trustworthy identity, serializes conflicts, and replays without duplicate
  rules or audit transitions.
- [x] A cycle with one confirmed company, a current DigiSac department, one
  active rule, and a currently valid company-department relationship produces
  one resolved, auditable snapshot containing the rule/version and validation
  facts.
- [x] A cycle with no confirmed company, an ambiguous/unresolved identity, no
  active rule, an inactive/missing department, or an invalid/currently absent
  company relationship persists the specified unresolved/invalid outcome and
  never selects by name, IA, history, first match, or default.
- [x] Rule and directory changes after a terminal cycle evaluation do not
  rewrite its snapshot or any completed classification; a later evaluation is
  represented separately when applicable.
- [x] Replay and concurrent administration/evaluation converge without
  duplicate active rules, audit rows, or snapshots, and lock, incomplete
  directory, migration, and transaction failures preserve the last valid
  state and remain recoverable/auditable.
- [x] The implementation never changes the existing webhook, finalization,
  HTTP, IA, assignment-history, identity-resolution, or Redis authority
  contracts and adds no public/admin HTTP endpoint or Request side effect.
- [x] Logs, metrics, exceptions, fixtures, and durable operational state
  contain no PII, conversation content, raw payload, token, header, or secret;
  safe IDs, states, versions, counts, and sanitized categories remain
  available for diagnosis.
- [x] Deterministic unit tests and disposable-PostgreSQL tests cover the
  positive, negative, integrity, lifecycle, idempotency, concurrency,
  rollback, privacy, stable-ID rename, and terminal-snapshot cases above.
- [x] Focused tests, applicable offline and PostgreSQL verification,
  compileall, strict Pyright, `git diff --check`, and `graphify update .` pass;
  unavailable prerequisites are reported separately from skips and passes.
- [x] Implementation-derived documentation, the SPEC-0010/spec-index status,
  exact local evidence, and Graphify metadata are synchronized; the issue is
  closed only after `IMPLEMENTATION_PLAN.md` records Milestone D completion
  and all changes are included in one focused commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — **Approved Acessórias milestones**, item 4,
  **P1 | implementation-ready after Milestone C | specified** Milestone D;
  see **Specification boundary and next gate**.
- Primary specification: `specs/0010-digisac-acessorias-department-mapping.md`
  v1.1.
- Dependencies: `specs/0001-shared-data-and-analysis-contract.md`,
  `specs/0003-durable-finalization-and-media.md`,
  `specs/0004-reproducible-verification-baseline.md`,
  `specs/0007-acessorias-external-directory-foundation.md`,
  `specs/0008-digisac-contact-identity-foundation.md`, and
  `specs/0009-digisac-acessorias-identity-resolution.md`.
- Related implementation issues: `issues/0012_-_implement-acessorias-directory-foundation.md`,
  `issues/0013_-_implement-digisac-contact-identity-foundation.md`,
  `issues/0014_-_implement-digisac-contacts-full-backfill.md`, and
  `issues/0015_-_implement-digisac-acessorias-identity-resolution.md`.

---

## Resolution

Implemented Milestone D as one PostgreSQL-authoritative internal capability.

- Added Alembic `0018_department_mapping` with stable-ID rule references,
  versioned active/inactive rules, transition audit, append-only cycle
  evaluations, indexes, constraints, and a populated-state downgrade guard.
- Added `src/core/department_mapping.py` with serialized `manual_db`
  administration, safe metadata/reason/actor handling, advisory-lock
  serialization, idempotent operation keys, confirmed-identity/current-assignment
  evaluation, current company-department validation, and immutable default cycle
  snapshots with explicit later-evaluation keys.
- Added deterministic sanitization tests and PostgreSQL coverage for lifecycle,
  many-to-one mappings, replay/concurrency, positive and negative evaluation,
  rollback, privacy, stable-ID renames, and terminal snapshot preservation.
- Updated schema-head guards, the disposable-test fixture, strict Pyright scope,
  SPEC-0010, the specification index, README, PRD, architecture, and plan with
  local-only evidence. No HTTP, IA, webhook, finalization, Redis, or Request
  behavior was changed.

Validation:

- `PYTHONPATH=/app python -m pytest -q tests/test_identity_resolution.py tests/test_identity_matching.py tests/test_acessorias_directory.py tests/test_ticket_assignments.py tests/test_conversation_cycles_db.py`: 22 passed, 18 skipped before implementation.
- `PYTHONPATH=/app python scripts/verify.py`: compileall PASS; Pyright PASS; offline pytest 177 passed, 56 skipped; disposable PostgreSQL/Alembic `0018_department_mapping` PASS; PostgreSQL pytest 56 passed, 177 deselected.
- `git diff --check`: PASS.
- `graphify update .`: completed after the implementation diff.
