---
id: 0026
title: "Prepare identity and department mapping before Acessórias Request creation"
type: bug
status: closed
priority: high
phase: 4
created_at: 2026-08-17
updated_at: 2026-08-17
closed_at: 2026-08-17
related_issues:
  - "0015"
  - "0016"
  - "0017"
  - "0018"
  - "0019"
  - "0020"
  - "0021"
  - "0022"
blocked_by: []
affects:
  - src/workers/ia_worker.py
  - src/api/routes.py
  - src/core/db.py
  - src/core/identity_resolution.py
  - src/core/department_mapping.py
  - src/core/acessorias_preparation.py
  - src/core/acessorias_requests.py
  - alembic/versions/0020_cycle_contact_provenance.py
  - tests/test_identity_resolution.py
  - tests/test_department_mapping.py
  - tests/test_acessorias_requests.py
  - tests/test_acessorias_preparation.py
  - README.md
  - PRD.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
  - specs/README.md
---

## Description

The implemented Acessórias milestones provide the conservative identity resolver,
the persisted cycle department-mapping evaluation, and the durable Request
operation. Their integration point is missing. The IA worker currently persists
the terminal classification and invokes `create_request_for_cycle()` directly;
it does not first execute and persist `resolve_cycle_identity()` and
`evaluate_department_mapping()` for that cycle. `create_request_for_cycle()`
then reads the default mapping snapshot and creates a durable operation in
`definitive_failure/mapping_missing` when none exists.

This is a confirmed production integration defect, not a provider rejection or
an uncertain send. On 2026-08-17, 55 terminal cycles created durable Acessórias
operations and all ended as `definitive_failure` with `mapping_missing`. There
were zero completed Requests, zero provider POST attempts, and zero Request
reconciliation operations. A further 42 cycles were `open` or `waiting_media`
and were not eligible for preparation or Request creation.

**Root cause:** [the worker](/app/src/workers/ia_worker.py:684) calls the
Request operation immediately after terminal classification. The Request
operation requires a pre-existing default row in
`conversation_cycle_department_mappings`; [its eligibility check](/app/src/core/acessorias_requests.py:450)
fails closed when that row is absent. The identity and mapping services exist,
but the current worker/orchestration flow does not call them, and no durable
cycle preparation path bridges their contracts.

**Expected orchestration:**

```text
terminal classification/cycle
  -> canonical ticket-contact identity resolution
  -> persisted department-mapping snapshot
  -> durable Request operation
  -> provider POST
```

The correction must make that sequence explicit and durable. It must preserve
the approved contracts rather than introduce a new routing or matching rule.
In particular, an identity or mapping failure is a blocked precondition and
must prevent a provider POST; it must not be converted into an inferred company
or department.

**Plan/spec references:** `IMPLEMENTATION_PLAN.md`, Approved Acessórias
milestones C–E; primary contracts SPEC-0009 v1.2, SPEC-0010 v1.3, and
SPEC-0011 v1.4; contact provenance contract SPEC-0008 v1.4; PRD §5.5 and §8;
ARCHITECTURE §§2.1, 9, and 12. The source code contradicts the documented
orchestration order by omitting the preparation calls. This issue corrects that
implementation defect; it does not change the approved business contracts.

**Dependencies:** the closed identity, mapping, and durable Request work in
issues 0015–0022; Alembic head `0020_cycle_contact_provenance`; persisted
ticket/cycle, contact, assignment-history, directory, identity, mapping, and
Request-operation data; and the existing disposable PostgreSQL verification
runner. No provider credential, live provider call, public endpoint, or
production database operation is a dependency for this implementation issue.

## Scope

### In scope

- Add one internal worker/orchestration preparation boundary that runs after
  terminal classification and before `create_request_for_cycle()`. It must
  obtain the canonical ticket contact from the durable ticket-contact source,
  resolve the cycle identity, persist the outcome, evaluate the department
  mapping, persist the outcome, and invoke Request creation only after the
  required facts are available.
- Preserve the canonical DigiSac ticket contact from `data.contact.id`. For a
  group, `message.contactId` identifies an individual message sender and must
  never replace the ticket contact. Do not derive the cycle contact from a
  sender, group participant, first message, name, phone, `idFromService`,
  `jidId`, or `lidId`. If current durable cycle/ticket data cannot identify the
  canonical ticket contact at this point, add only the minimal additive durable
  provenance required to carry that already-approved `data.contact.id` fact;
  do not introduce a heuristic fallback.
- Call the existing conservative identity contract. Exact phone, exact email,
  and the approved Brazilian mobile variant may create evidence/candidates but
  never automatic confirmation. A group without one applicable explicit
  confirmed link remains unresolved. `candidate`, `ambiguous`, `unresolved`,
  and `conflict` results remain blocked and must not call mapping or the
  provider as if a company had been chosen.
- Call the existing mapping contract only with the persisted cycle and identity
  outcome. It must select the applicable assignment inside the persisted
  `cycle_started_at`/`ticket_closed_at` boundaries, use the active stable-ID
  mapping rule, and validate the selected Acessórias department against the
  confirmed company's current active directory relationship. Persist the
  resulting mapping snapshot and its safe reason/state before Request creation.
- Keep every missing or invalid prerequisite explicit and fail closed: missing
  canonical contact, missing/ambiguous/unconfirmed identity, group without an
  applicable confirmed link, insufficient cycle boundaries, missing assignment,
  missing/inactive rule, unavailable company/department, or absent/inactive
  company-department relationship must result in no provider POST. Preserve
  the classification and durable diagnostic facts; do not select a fallback.
- Provide a controlled internal recovery/reprocessing path for the existing
  `mapping_missing` operations and future equivalent preparation omissions. It
  must re-run the canonical preparation sequence under transaction/claim
  protection, then allow the normal Request operation path only when the
  preconditions become valid. It must target only operations for which durable
  evidence proves no provider POST started: at minimum the observed
  `mapping_missing` state with no `post_started_at`, no `SolID`, no provider
  attempt evidence, and no reconciliation requirement. It must not use ad-hoc
  SQL state mutation, delete/rewrite classifications, silently overwrite an
  existing immutable cycle snapshot, or re-open a completed operation.
- Preserve the Request operation's one-cycle uniqueness, claim/lease behavior,
  safe pre-POST retry boundary, `SolID` integrity, and manual reconciliation.
  If `post_started_at` is set, the outcome is uncertain, or reconciliation is
  required, recovery must not resend automatically; retain the existing
  `manual_db` reconciliation or proof-of-absence release path. No provider
  idempotency parameter, guessed correlation, or blind retry may be added.
- Add deterministic and disposable-PostgreSQL coverage, then synchronize
  implementation-derived documentation, active spec/index traceability, exact
  verification evidence, and Graphify metadata when this issue closes.

### Out of scope

- New identity matching, automatic company confirmation, name/fuzzy matching,
  group-member matching, message-sender substitution, or changes to the
  canonical `contact.id` contract.
- `intent_type`, classification title/description, confidence, department
  names, responsible users, historical Requests, a first available department,
  or any default-department rule as identity or routing input.
- Changes to Acessórias directory synchronization, mapping-rule administration,
  Request payload fields, priority, Request lifecycle, attachments, comments,
  status synchronization, responsible users, closure, reopening, or `tipo=I`.
- Public/admin HTTP triggers, webhook-time provider calls, Redis as a durable
  identity/mapping/Request authority, provider credentials, production
  operations, live reprocessing, deployment, or a broad migration redesign.
- Automatically retrying, releasing, or resending an operation whose POST may
  have started, whose provider outcome is unknown, or which is already
  `completed` or `reconciliation_required`.

## Implementation Plan

1. Reconfirm the actual durable source of the ticket's `data.contact.id`, the
   terminal-cycle transition, and the existing identity/mapping/Request APIs.
   Make the orchestration accept only that canonical contact for the cycle; do
   not use `message.contactId` as a substitute. If the source is not durably
   reachable from a later worker replay, add the smallest additive persisted
   link/provenance required for the existing ticket-contact fact and protect it
   from sender/group substitution.
2. Introduce a typed internal preparation routine at the post-classification
   boundary. It must first call `resolve_cycle_identity(cycle_public_id,
   contact_id)` (or the equivalent existing persisted-contact API), inspect the
   durable resolution result, and stop before mapping/Request delivery unless
   it is exactly `confirmed`. Discovery evidence may be refreshed only through
   the existing conservative resolver; it may not promote a candidate.
3. For a confirmed cycle, call `evaluate_department_mapping(cycle_public_id)`
   and persist/read its cycle snapshot. Continue only when it is `resolved` and
   its stable IDs, selected assignment facts, active rule/version, confirmed
   company, and current company-department relationship satisfy SPEC-0010.
   Preserve the existing immutable default-snapshot semantics; an existing
   non-default/later evaluation must not be silently substituted for a missing
   default snapshot without an explicit contract-compliant path.
4. Invoke `create_request_for_cycle()` only after preparation succeeds. Keep
   preparation and delivery independently durable: a blocked identity or
   mapping must be visible without creating a provider attempt; a Request
   provider failure must never roll back classification, identity, or mapping
   facts. Ensure worker replay and concurrent workers converge on the same
   identity result, mapping snapshot, and one Request operation.
5. Implement an explicit internal recovery command/service for proven pre-POST
   `mapping_missing` operations. It must select a narrowly auditable target,
   lock/claim it, prove that no POST could have started, run the same canonical
   preparation routine, and re-enter the normal durable Request state machine.
   A mapping or identity corrected after the original terminal cycle must not
   rewrite historical facts; recovery may create only the missing preparation
   facts permitted by the existing contracts. Any row with a post-start marker,
   `SolID`, provider-attempt evidence, or uncertain state is excluded and
   remains subject to existing manual reconciliation/release safeguards.
6. Add safe observability for preparation stage, cycle/operation IDs, identity
   state, mapping state/reason, and recovery disposition. Never log or persist
   token/header, raw payload, contact name/phone/email, message text, group
   participants, classification content, raw provider responses, or database
   connection details.
7. Run focused tests, the applicable offline suite, compileall, strict
   Pyright, and disposable PostgreSQL/Alembic verification. Review the focused
   diff, run `git diff --check` and `graphify update .`, and update only the
   implementation-derived documentation/traceability required by the completed
   behavior. Close with one focused commit after `IMPLEMENTATION_PLAN.md` is
   synchronized.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migration:** PostgreSQL remains authoritative for canonical contacts,
  identity outcomes, mapping snapshots, Request operations, claims, attempts,
  `SolID`, and reconciliation. No migration is presumed. If replayable
  ticket-contact provenance is demonstrably absent, use one additive,
  data-preserving Alembic revision rather than startup DDL or an ephemeral
  Redis-only link. Never mutate production rows in this issue's build pass.
- **Compatibility:** preserve webhook HMAC behavior, terminal-cycle semantics,
  assignment history, IA's four-field contract, existing internal/public HTTP
  surface, existing Acessórias payload/adapter behavior, and Redis's transient
  coordination role. The provider POST remains outside the webhook request
  path and receives no new idempotency field.
- **Integrity/concurrency:** preparation replays must converge without duplicate
  evidence, identity outcomes, mapping snapshots, recovery records, durable
  operations, or provider calls. One cycle remains one durable Request
  operation and at most one external Request. The recovery gate must be
  conservative: any inability to prove a pre-POST state is a no-send result.
- **Security/privacy:** retain only safe IDs, states, rule versions, counts,
  timestamps, fingerprints, and sanitized categories in logs/metrics/durable
  recovery state. Do not expose PII, conversation content, raw contact or
  provider payloads, secrets, headers, or credentials.
- **Observability:** operators must be able to distinguish terminal-but-not-
  prepared, identity-blocked, mapping-blocked, eligible/prepared, normal
  Request state, proven-pre-POST recovery candidate, and reconciliation-only
  outcome without inspecting raw payloads.
- **Rollout:** local deterministic and disposable-PostgreSQL verification are
  the acceptance boundary. This issue does not authorize production replay of
  the 55 rows, a live Acessórias POST, a directory sync, credential validation,
  deployment, or a claim of production readiness. A later authorized runbook
  must verify prerequisites and reconcile each operational population before
  acting.

## Tests

- **Worker/orchestration:** terminal classification invokes canonical contact
  preparation, persists the identity resolution and resolved mapping snapshot,
  then calls the Request operation; assert ordering rather than only final
  success.
- **Identity/contact:** exact phone/email remains candidate-only; unconfirmed,
  ambiguous, conflicting, and unresolved identities create no provider call;
  group tickets use the persisted `data.contact.id`, while a differing
  `message.contactId` sender cannot change the selected contact or company.
- **Mapping:** selection uses only the assignment inside the persisted cycle
  boundaries and the active stable-ID rule/current company relationship.
  Missing assignment/boundary/rule, inactive directory facts, and invalid or
  absent relationships persist a blocked mapping result and make zero POSTs.
- **Request safety:** missing preparation facts create no provider POST;
  successful preparation creates one operation and one POST; repeated worker
  replay and concurrent preparation/delivery produce one identity outcome, one
  mapping snapshot, one operation, and at most one provider call.
- **Recovery:** a proven `mapping_missing` pre-POST operation is recoverable
  only after identity/mapping facts become valid, then follows the ordinary
  operation claim/delivery path. Assert it neither directly mutates state by
  SQL nor duplicates classification/snapshots. Operations with `post_started_at`,
  an uncertain result, reconciliation requirement, or a completed `SolID` are
  rejected from automatic recovery and make zero additional POSTs.
- **Regression/security:** retain payload-load pre-POST retry, ambiguous
  transport, `429`, manual reconciliation/release, completed replay, and
  sanitization coverage; assert no contact/Payload/provider-secret leakage in
  preparation/recovery logs or durable state.

Required validation commands:

- `PYTHONPATH=/app python -m pytest -q tests/test_identity_resolution.py tests/test_department_mapping.py tests/test_acessorias_requests.py`
- `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`
- `python -m compileall -q src tests alembic scripts`
- `npx --yes pyright`
- `PYTHONPATH=/app python scripts/verify.py` when disposable PostgreSQL and
  Docker prerequisites are available; report unavailable prerequisites
  separately from skips and passes.
- `git diff --check`
- `graphify update .`

## Acceptance Criteria

- [x] The worker/orchestration implements and tests the canonical sequence:
  terminal classification/cycle → identity resolution → department-mapping
  snapshot → durable Request operation → provider POST.
- [x] The cycle contact is derived only from the canonical ticket
  `data.contact.id`; a message sender `contactId`, group participant, name,
  phone, first message, or other metadata cannot replace it.
- [x] Exact phone/email and other approved discovery evidence remain
  candidate-only; unresolved, ambiguous, conflicting, unconfirmed, and group
  identities without one explicit confirmed link are blocked before mapping and
  make zero provider POSTs.
- [x] A successful path persists a confirmed identity outcome and a resolved
  mapping snapshot with the cycle-applicable assignment, active stable-ID rule,
  and current confirmed-company relationship before it creates/claims the
  Request operation.
- [x] Missing or invalid contact, identity, cycle boundaries, assignment, rule,
  directory company/department, or company-department relationship persists a
  safe blocked outcome and makes zero provider POSTs; no prohibited fallback or
  routing input is introduced.
- [x] Worker replay and concurrent execution converge without duplicate
  identity/mapping facts, durable Request operations, or external Requests;
  the invariant remains one cycle = one operation = at most one Request.
- [x] The controlled recovery path handles only operations with durable proof
  that no POST started, including the observed `mapping_missing` population;
  it does not directly mutate state with ad-hoc SQL, alter classifications, or
  overwrite immutable snapshots.
- [x] Any post-start, uncertain, reconciliation-required, or completed
  operation remains excluded from automatic recovery/retry and preserves the
  existing reconciliation/proof-of-absence controls.
- [x] The existing Request payload, `SolID`, claim/lease, safe pre-send retry,
  ambiguous transport, `429`, reconciliation, lifecycle, webhook, IA, and
  Redis authority contracts remain unchanged.
- [x] Tests cover ordering, persisted preparation facts, blocked/no-POST cases,
  group-contact provenance, valid-after-blocked recovery, replay, concurrency,
  duplicate protection, and sanitization.
- [x] Focused tests, applicable offline/PostgreSQL verification, compileall,
  strict Pyright, `git diff --check`, and `graphify update .` pass, with
  unavailable prerequisites reported separately.
- [x] README, PRD, architecture, implementation-plan traceability, SPEC-0008
  through SPEC-0011/index status as applicable, and Graphify metadata are
  synchronized only with implemented behavior and one focused commit closes
  this issue.

## References

- **Primary contracts:**
  `specs/0008-digisac-contact-identity-foundation.md` v1.4;
  `specs/0009-digisac-acessorias-identity-resolution.md` v1.2;
  `specs/0010-digisac-acessorias-department-mapping.md` v1.3; and
  `specs/0011-durable-acessorias-request-creation.md` v1.4.
- **Product/architecture:** `PRD.md` §§5.5 and 8;
  `ARCHITECTURE.md` §§2.1, 9, and 12; `README.md`, Acessórias integration and
  Request reconciliation sections; and `IMPLEMENTATION_PLAN.md`, Milestones
  C–E.
- **Source evidence:** `src/workers/ia_worker.py`, post-classification call to
  `create_request_for_cycle()`; `src/core/identity_resolution.py`,
  `resolve_cycle_identity()`; `src/core/department_mapping.py`,
  `evaluate_department_mapping()`; and `src/core/acessorias_requests.py`,
  `_mapping_snapshot()`, `_operation_state()`, and
  `create_request_for_cycle()`.
- **Related issues:** 0015 (identity), 0016 and 0020 (mapping), 0017 (durable
  Request), 0018 (uncertain transport), 0019 (shared rate admission), 0021
  (pre-POST payload failure), and 0022 (uncertain `429`). None covers the
  missing worker preparation/orchestration sequence or the proven-pre-POST
  recovery of `mapping_missing` operations.

---

## Resolution

Implemented issue #0026 with a durable preparation boundary in the IA worker:
terminal cycles now carry the canonical ticket `data.contact.id`, resolve and
persist identity, evaluate and persist cycle-scoped department mapping, and
enter the existing durable Request path only when both stages are ready. Missing
or unconfirmed prerequisites fail closed before any provider call. A controlled
internal recovery path now claims only proven pre-POST `mapping_missing`
operations, records safe preparation audit data, and reuses normal Request
creation after successful preparation; post-start, uncertain, reconciliation,
and completed operations remain excluded.

Migration `0020_cycle_contact_provenance` adds canonical cycle contact
provenance and preparation-recovery audit storage. Tests cover ordering,
group-contact provenance, blocked/no-POST behavior, durable provenance,
recovery, replay, and provider-call count. Documentation and Graphify metadata
were synchronized in README, PRD, ARCHITECTURE, IMPLEMENTATION_PLAN,
SPEC-0008 through SPEC-0011, and `specs/README.md`.

Validation completed:

- `python -m compileall -q src tests alembic scripts` — passed.
- `npx --yes pyright` — 0 errors, 0 warnings, 0 informations.
- `PYTHONPATH=/app python scripts/verify.py` — compileall, Pyright, 203 offline
  tests/68 skips, Alembic `0020`, and 68 PostgreSQL tests/203 deselected passed.
- `git diff --check` and `graphify update .` — passed.

No provider credentials, live provider calls, production database, or public
endpoint were used.
