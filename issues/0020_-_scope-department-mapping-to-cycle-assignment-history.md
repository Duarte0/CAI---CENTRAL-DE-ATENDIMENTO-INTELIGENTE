---
id: 0020
title: "Constrain department mapping to the target cycle assignment history"
type: bug
status: closed
priority: high
phase: 4
created_at: 2026-08-17
updated_at: 2026-08-17
closed_at: 2026-08-17
related_issues:
  - "0016"
  - "0017"
blocked_by: []
affects:
  - src/core/department_mapping.py
  - tests/test_department_mapping.py
  - specs/0010-digisac-acessorias-department-mapping.md
  - IMPLEMENTATION_PLAN.md
  - ARCHITECTURE.md
  - PRD.md
---

## Description

The implemented department-mapping evaluation can assign a reopened/closed
conversation cycle to the department from a later cycle. This is a confirmed
data-integrity defect: the resulting Acessórias department snapshot can be
wrong, and the durable Request operation in issue 0017 can consume that wrong
snapshot.

**Plan/spec references:** `IMPLEMENTATION_PLAN.md`, Approved Acessórias
milestones, item 4, Milestone D; `SPEC-0010` v1.1, especially the current
cycle evaluation and immutable per-cycle snapshot contract; the durable cycle
boundary contract in `SPEC-0003`; and the Acessórias Request preconditions in
`SPEC-0011`.

**Dependencies:** the existing `conversation_processing_cycles` and
`ticket_assignment_history` tables, the mapping implementation from closed
issue `0016`, and the Request consumer from issue `0017`. No new product or
provider decision is required: the cycle and assignment timestamps already
persist the facts needed to prevent a later-cycle assignment from being used.

**Root cause:** `src/core/department_mapping.py:569-577` loads only the cycle
ID and conversation ID. The assignment query at `:606-615` then selects the
latest non-null assignment for the entire conversation, ordered by
`event_timestamp DESC, id DESC`, without constraining it to the selected
cycle's `cycle_started_at`/`ticket_closed_at` interval. The cycle table already
persists those timestamps in `alembic/versions/0013_conversation_cycles.py:18-23`,
and assignment events already persist `event_timestamp` in
`alembic/versions/0001_initial.py:89-101`.

**Reproduction:**

1. Create a conversation with DigiSac assignment `d-old` at 10:05 and close
   its first cycle at 11:00.
2. Reopen the conversation at 12:00 and record assignment `d-new` at 12:05.
3. Configure valid mappings `d-old → acc-old` and `d-new → acc-new`, with both
   target departments available for the confirmed company.
4. Evaluate the first cycle by its public ID.

In a disposable PostgreSQL reproduction against the current checkout, the
first cycle returned `state=resolved`,
`validation.assignment_history_id=2`, `digisac_department=d-new`, and
`acessorias_department=acc-new`. The expected first-cycle result is the
assignment at or before that cycle's close (`d-old → acc-old`). The current
offline suite (189 passed, 60 skipped) and PostgreSQL suite (60 passed) do not
cover a close/reopen conversation with two assignment events.

**Actual behaviour:** mapping is based on the conversation-wide latest
assignment, including an assignment recorded after the target cycle closed.

**Expected behaviour:** mapping obtains the DigiSac department relevant to the
selected cycle under the established cycle boundary contract. An assignment
from a later cycle must never be selected for an earlier cycle. When the
persisted boundary facts do not prove an applicable assignment, evaluation must
remain explicitly unresolved/blocked rather than silently falling back to a
later assignment or another department. The selected assignment and boundary
facts must remain auditable in the existing per-cycle evaluation/snapshot, and
terminal snapshots must remain immutable.

## Implementation Plan

1. Reconfirm the cycle boundary semantics in `SPEC-0003` and the current cycle
   lifecycle implementation, including how open and closed cycles expose
   `cycle_started_at` and `ticket_closed_at`.
2. Update the assignment lookup in `src/core/department_mapping.py` to select
   only an assignment applicable to the target cycle's persisted interval,
   retaining deterministic timestamp/ID ordering and excluding events from
   later cycles. Preserve the existing stable-ID mapping, identity, directory,
   unresolved/invalid states, evaluation-key idempotency, and snapshot
   immutability. If the available boundary is insufficient, fail closed using
   the existing explicit unresolved/blocked outcome rather than inventing a
   fallback.
3. Keep the selected assignment ID and relevant validation facts durable in the
   existing evaluation state. Do not change mapping rules, infer by name/IA,
   select a first/default/history-based department, or add an external Request
   side effect to the mapping operation.
4. Add PostgreSQL regression coverage for a closed cycle followed by a reopen
   and new assignment: the old cycle must map to the old department and the new
   cycle to the new department. Cover missing/insufficient boundaries, replay,
   concurrent evaluation, and preservation of an existing terminal snapshot.
5. Synchronize implementation-derived references and status in the approved
   plan/specification documentation after the code and tests are complete.

## Tests

- **Focused:** `PYTHONPATH=/app python -m pytest -q tests/test_department_mapping.py`
- **Offline suite:** `PYTHONPATH=/app python -m pytest -q -m "not postgres"`
- **Canonical verification:** `PYTHONPATH=/app python scripts/verify.py` with
  compileall, strict Pyright, disposable migration, and PostgreSQL stages
  recorded separately.
- **Repository hygiene:** `git diff --check`; inspect the focused source,
  migration, and test diff for prohibited PII, secrets, and unrelated changes.

## Acceptance Criteria

- [x] A cycle closed before a later assignment never selects that later
  assignment; the reproduced `d-old → acc-old` / reopened `d-new → acc-new`
  case passes for both cycles.
- [x] The selected assignment is the latest applicable event under the
  established cycle boundary contract, with deterministic ID tie-breaking.
- [x] Missing or insufficient boundary/assignment evidence persists an explicit
  unresolved/blocked result and never chooses a later, first, default, named,
  IA-derived, responsible-party, or historical-Request department.
- [x] `validation_json.assignment_history_id` and the persisted department
  result identify the same cycle-applicable assignment, and a later mapping
  evaluation cannot rewrite an existing terminal snapshot.
- [x] Replay and concurrent evaluation remain idempotent and do not create
  duplicate mapping snapshots or alter classification state.
- [x] The Request path consumes only a resolved, cycle-correct mapping and the
  mapping evaluation itself performs no provider call or other external side
  effect.
- [x] Logs, metrics, exceptions, fixtures, and durable state expose only safe
  IDs, states, timestamps, counts, and sanitized failure categories; no PII,
  conversation content, payload, token, header, or secret is added.
- [x] Focused tests, applicable offline/PostgreSQL verification, compileall,
  strict Pyright, and `git diff --check` pass, with unavailable prerequisites
  reported separately from skips and passes.
- [x] `SPEC-0010`, `SPEC-0003`, `IMPLEMENTATION_PLAN.md`, and the relevant
  architecture/PRD traceability remain consistent; Graphify metadata is
  updated after implementation.
- [x] The issue is closed only after validation and one focused commit.

## References

- Primary contract: `specs/0010-digisac-acessorias-department-mapping.md` v1.2,
  §§Objetivo e não objetivos, Dados/integridade e ciclo de vida, and
  Avaliação/falhas/compatibilidade.
- Cycle contract: `specs/0003-durable-finalization-and-media.md`, persistent
  cycle, history-boundary, and reopen behavior.
- Request contract: `specs/0011-durable-acessorias-request-creation.md`,
  resolved mapping prerequisite and no-effect-on-invalid-result rule.
- Product/architecture traceability: `PRD.md` §§5.2, 5.5, and 8;
  `ARCHITECTURE.md` §2.1 and Request integration sections;
  `IMPLEMENTATION_PLAN.md` Milestones D and E.
- Source evidence: `src/core/department_mapping.py:569-615`;
  `alembic/versions/0013_conversation_cycles.py:18-23`;
  `alembic/versions/0001_initial.py:89-101`.
- Related implementation/consumer: `issues/0016_-_implement-digisac-acessorias-department-mapping.md`;
  `issues/0017_-_implement-durable-acessorias-request-creation.md`.
- Non-duplicates: open bug `0018` covers ambiguous Acessórias POST transport
  outcomes, and `0019` covers sharing the provider rate limit; neither covers
  selecting an assignment from a later conversation cycle.

---

## Resolution

<!-- Filled by the agent on close. DO NOT edit manually. -->
<!-- What was done, decisions made, and why. -->
<!-- Include: files modified, tests added, edge cases handled. -->

Implemented the cycle-scoped assignment boundary in
`src/core/department_mapping.py`. Evaluation now requires non-null,
chronologically valid `cycle_started_at` and `ticket_closed_at`, selects only
assignments inside the inclusive interval with deterministic timestamp/ID
ordering, and persists the selected assignment ID plus boundary facts. Missing
or inverted boundaries persist `unresolved/cycle_boundary_insufficient` without
selecting a fallback. No migration or provider side effect was added.

Added PostgreSQL regressions for close/reopen isolation, same-timestamp ID
tie-breaking, insufficient boundaries, explicit later evaluation, and terminal
snapshot preservation. Updated SPEC-0010 to v1.2, SPEC-0003 to v1.4, the specs
index, implementation plan, PRD, architecture, and Graphify metadata.

Validation:

- Pre-fix disposable run reproduced the two intended failures while compileall,
  Pyright, and offline stages passed.
- `PYTHONPATH=/app python scripts/verify.py` passed compileall, strict Pyright,
  offline pytest (**197 passed, 64 skipped**), Alembic head
  `0019_acessorias_request_creation`, and PostgreSQL pytest (**64 passed, 197
  deselected**) on disposable PostgreSQL 16.
- `PYTHONPATH=/app python -m pytest --collect-only -q`: **261 tests collected**.
- `git diff --check`: passed.
