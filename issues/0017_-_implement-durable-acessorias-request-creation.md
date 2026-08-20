---
id: 0017
title: "Implement durable Acessórias Request creation"
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
  - "0016"
blocked_by:
  - "0012"
  - "0013"
  - "0014"
  - "0015"
  - "0016"
affects:
  - alembic/versions/
  - src/core/
  - src/workers/
  - tests/
  - scripts/verify.py
  - .env.example
  - README.md
  - PRD.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
  - specs/README.md
---

## Description

Deliver the next approved Acessórias increment from `IMPLEMENTATION_PLAN.md`:
P1 **Milestone E — Durable Acessórias Request Creation**, item 5 in the
**Approved Acessórias milestones** section. A Request must be created only
from durable CAI facts already produced by the completed directory, contact,
identity-resolution, and department-mapping work, while the originating
classification remains authoritative and recoverable.

**Plan/spec references:** `IMPLEMENTATION_PLAN.md`, Approved Acessórias
milestones, item 5, **P1 | completed locally | implemented**; primary
`SPEC-0011` v1.1; cross-cutting `SPEC-0001` v1.2 and
`SPEC-0003`; dependency contracts `SPEC-0007` v1.1 through `SPEC-0010` v1.1.

**Dependencies:** closed issues `0012`, `0013`, `0014`, `0015`, and `0016`;
Alembic head `0019_acessorias_request_creation`; the existing Acessórias provider
boundary/configuration, PostgreSQL pool, persisted conversation-cycle and
classification state, identity-resolution outcome, department-mapping
snapshot, and disposable PostgreSQL verification runner. A real provider
credential is an operational prerequisite for an actual external call, not a
reason to weaken deterministic local verification or claim provider/production
acceptance.

**Verified gap at issue creation:** the checkout had the Acessórias directory adapter
and durable directory/identity/department facts, but no Request-creation
operation, multipart write boundary, Request migration/state, `SolID`
storage, cycle-level uniqueness, retry/reconciliation state, manual database
reconciliation procedure, or corresponding tests. No existing open or
in-progress issue covers this outcome. `SPEC-0011` is the canonical contract;
do not infer lifecycle behavior from this issue or from the provider's
directory endpoints.

Expected outcome: one eligible CAI cycle can produce at most one durable
Request operation and one external Request, with `SolID` persisted on a
response containing a non-empty `id`. Ineligible or unresolved cycles never
call the provider. Definite failures, proven-safe retries, uncertain POST
outcomes, crashes, concurrent claims, replay, and operator reconciliation are
durable, sanitized, and do not rewrite or invalidate the completed
classification.

## Scope

### In scope

- Add the next additive Alembic revision after `0018_department_mapping` for
  one durable Request operation per source cycle, references to the source
  cycle/classification and resolved company/department facts, safe payload
  representation/fingerprint, attempt/claim state, `SolID`, timestamps, and
  sanitized failure/reconciliation state. Enforce the equivalent of
  `UNIQUE(source_cycle_id)` and refuse a data-losing downgrade when populated.
- Extend or add the existing Acessórias provider boundary for authenticated
  `POST https://api.acessorias.com/requests` using multipart form data with
  only `assunto`, `empresa`, `departamento`, `prioridade`, `descricao`, and
  `tipo=E`. Apply local validation, deterministic title truncation, the
  centralized priority `2`, the configured rate limit, and bounded handling
  of `Retry-After`.
- Orchestrate creation from persisted terminal-cycle facts: `completed` is
  eligible; `completed_with_warnings` is eligible only when the warnings do
  not remove data required by the Request. Require exactly one valid confirmed
  company and the resolved department-mapping snapshot validated against the
  company's current relationship. Persist the operation before every POST.
- Implement durable claim/lease or equivalent locking, replay convergence,
  and conservative state transitions for `pending`/`not_started`,
  `attempting`, `completed`, `definitive_failure`, `retryable_failure`, and
  `reconciliation_required` as defined by SPEC-0011.
- Implement the initial controlled `manual_db` reconciliation boundary: an
  operator may record a verified `SolID` and reconcile an uncertain operation,
  or explicitly release a retry only after evidence that no remote Request was
  created. Preserve the actor as absent unless a trustworthy administrative
  identity exists.
- Add deterministic provider doubles, unit coverage, and disposable
  PostgreSQL coverage for positive, negative, integrity, retry,
  reconciliation, crash, privacy, idempotency, and concurrency behavior.
- Update implementation-derived documentation, the active specification
  index, exact local verification evidence, and Graphify metadata when the
  implementation closes this issue. Synchronize the Milestone E status in
  `IMPLEMENTATION_PLAN.md` only during the build pass.

### Out of scope

- Directory synchronization, DigiSac contact hydration/backfill, identity
  matching/confirmation, or department mapping changes.
- Any IA prompt/classification contract, confidence or intent policy,
  classification rewrite, cycle-state redesign, webhook behavior, finalization
  behavior, or public/admin HTTP endpoint.
- A provider idempotency key or a guessed correlation search based on subject,
  time, or other fragile fields. The provider does not document an idempotency
  key for this endpoint.
- Automatic retry after a possibly processed POST, including timeout,
  disconnect-after-send, illegible response, `5xx` without proof of
  non-processing, success without `id`, or crash before the local success
  commit.
- Request lifecycle operations such as edits, comments, attachments,
  responsible users, status changes, closure, reopening, or `tipo=I`; those
  belong to Milestone F and a later product decision/specification.
- Real provider credentials, production synchronization, deployment/rollout,
  hosted CI, retention policy, or unrelated cleanup.

## Implementation Plan

1. Reconfirm the current cycle/classification contracts and the durable facts
   emitted by identity resolution and department mapping. Define a typed
   Request-operation input/output boundary so provider-shaped payloads,
   sensitive classification content, and database rows do not leak into
   logs or unrelated pipeline code. Reuse the existing Acessórias
   configuration and provider-boundary conventions; add only the
   Request-specific configuration needed by SPEC-0011.
2. Add one Alembic migration after `0018_department_mapping`. Model the
   source cycle, classification, safe conversation/ticket reference where
   useful, resolved company and department, payload fingerprint, lifecycle
   state, attempt metadata, claim/lease fields, external `SolID`, timestamps,
   and sanitized error/reconciliation information. Add foreign keys,
   nonblank/state checks, indexes for recovery, the one-operation-per-cycle
   uniqueness rule, and a populated-state downgrade guard. Do not create or
   mutate schema at application startup.
3. Implement the Acessórias write adapter behind the provider boundary. Build
   only the approved multipart fields, derive `assunto` from the persisted
   classification title with deterministic non-empty truncation at 100
   characters, derive `descricao` from the persisted description, serialize
   the provider-required company/department identifiers without name-based
   lookup, and always send `prioridade=2` and `tipo=E`. Keep the Bearer token
   out of persistence, logs, metrics, exceptions, and returned operational
   state. Parse `id` as the only success identity and classify JSON `Erro`
   responses rather than trusting HTTP status alone.
4. Add the internal orchestration at the existing post-classification,
   post-mapping boundary. Refuse provider work for missing classification,
   non-terminal or ineligible cycle states, failed/media-blocked cycles,
   unresolved/ambiguous/conflict identity, missing mapping, or a department
   that is not currently related to the resolved company. Create or replay
   the PostgreSQL operation before the POST, claim it transactionally, and
   release/recover claims without allowing two workers to execute the same
   operation concurrently. Provider failure must never roll back or alter
   the classification.
5. Implement the specified outcome matrix. Mark a non-empty provider `id` as
   `completed` only after the `SolID` and operation state commit. Use a
   bounded retry only for failures with strong evidence that the POST was not
   processed, including safe pre-send connection failure and eligible `429` /
   transient responses. Treat business validation, `Erro`, and auth/
   permission outcomes as definitive or operational failures as specified;
   treat any possibly processed POST as
   `reconciliation_required` with no automatic second POST. Make completed
   replay a no-op.
6. Implement the controlled `manual_db` reconciliation operation with
   explicit `SolID`, `manual_db` source, timestamp, safe reason, and optional
   trustworthy actor. Require proof of remote absence before releasing an
   uncertain operation for another attempt, preserve the original evidence,
   and make reconciliation/release replay-safe and conflict-safe.
7. Add focused deterministic tests and PostgreSQL-marked tests covering
   eligibility, current company-department validation, payload shape and
   truncation, missing/ambiguous/mapping-negative cases, successful `id`,
   missing `id`, `Erro`, auth, `429`, `5xx`, pre-send and post-send failures,
   claim races, crashes before/during/after POST, completed replay, manual
   reconciliation/release, migration constraints, rollback, and secret/PII
   sanitization. Register the tests with the existing disposable runner only
   as needed.
8. Run focused tests, the applicable offline suite, compileall, strict
   Pyright, disposable PostgreSQL/Alembic verification, `git diff --check`,
   and `graphify update .`. Record unavailable external prerequisites
   separately from passing local evidence. Update only implementation-derived
   documentation and the exact Milestone E status/evidence, then close this
   issue only when all work is included in one focused commit.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migrations:** PostgreSQL is authoritative for Request operations,
  claims, attempts, `SolID`, and reconciliation. Use an additive revision
  after `0018_department_mapping`; preserve operation history and do not
  delete or rewrite the originating classification, identity outcome,
  department snapshot, or completed operation.
- **Compatibility:** preserve all existing HTTP routes, HMAC handling,
  webhook/finalization flow, IA four-field contract, cycle status semantics,
  identity-resolution and department-mapping contracts, and Redis's role as
  transport/coordination rather than durable Request authority. Do not add a
  provider call to the webhook handler or a public trigger.
- **Integrity/concurrency:** enforce one durable operation per source cycle;
  claim/lease and transaction boundaries must prevent duplicate POSTs from
  replay or concurrent workers. A completed `SolID` is immutable except for
  the explicitly audited reconciliation path allowed by SPEC-0011.
- **Security/privacy:** never persist or log Bearer tokens, request headers,
  raw multipart payloads, raw provider bodies, full title/description,
  phone, email, conversation content, or unredacted PII. Durable errors,
  metrics, fixtures, and exceptions expose only safe IDs, state, counts,
  timestamps, fingerprints, and sanitized categories.
- **Observability:** expose safe operation/cycle references, state, attempt
  count, duration, provider outcome category, and reconciliation-required
  status so operators can distinguish blocked eligibility, definitive
  failure, retryable failure, and uncertain remote processing.
- **Rollout:** local deterministic and disposable-PostgreSQL evidence is the
  acceptance boundary. No real provider call, credential validation,
  deployment, or production readiness claim is established by this issue.

## Tests

- **Focused:** run the new deterministic Request adapter/orchestration tests
  for payloads, eligibility gates, provider outcome classification,
  idempotency, concurrency, reconciliation, and sanitization.
- **PostgreSQL:** run the new `postgres`-marked Request-operation tests and
  verify the migration head, source references, one-cycle uniqueness,
  recovery claims, `SolID` persistence, rollback, and manual reconciliation.
- **Offline suite:**
  `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`
- **Static/repository validation:**
  `python -m compileall -q src tests alembic scripts`, `npx --yes pyright`,
  and `git diff --check`.
- **Canonical runner:**
  `PYTHONPATH=/app python scripts/verify.py` when Docker/disposable
  PostgreSQL prerequisites are available; report compileall, Pyright, offline,
  migration, and PostgreSQL stages separately, including unavailable
  prerequisites.
- **Graph/documentation:** run `graphify update .` after implementation and
  verify SPEC-0011 v1.1, the specification index, the plan, and the
  implementation-derived architecture/PRD/README claims remain consistent.

## Acceptance Criteria

- [x] An additive Alembic revision after `0018_department_mapping` creates the
  durable Request-operation state with source references, safe checks,
  recovery indexes, one-operation-per-cycle uniqueness, and a populated-state
  downgrade guard.
- [x] An eligible `completed` cycle with one confirmed company and a valid
  current company-department mapping creates one operation and sends exactly
  the approved multipart Request with `tipo=E`, `prioridade=2`, the six
  permitted fields, and no lifecycle fields.
- [x] `completed_with_warnings` is accepted only when required Request data is
  present; missing classification, non-terminal/failed/media-blocked cycle,
  unresolved/ambiguous/conflict identity, absent mapping, and invalid current
  department persist a blocked outcome and make no provider call.
- [x] The subject uses the persisted cycle protocol when available, prefixes the
  persisted title as `[protocol] - title`, and is deterministically
  truncated to a non-empty value at 100 characters, and the description is
  derived only from the persisted description without another IA call.
- [x] A provider response with a non-empty `id` persists that value as `SolID`
  and reaches `completed` only after the local transaction commits; `msg`
  alone or a response without `id` never confirms success.
- [x] Provider `Erro`, validation/business rejection, auth/permission failure,
  `429`, `5xx`, pre-send connection failure, and post-send uncertainty follow
  the SPEC-0011 state/retry matrix, including bounded rate-aware retry only
  when non-processing is proven and `reconciliation_required` without an
  automatic second POST for uncertain outcomes.
- [x] Claim/lease, replay, crash recovery, and concurrent execution converge
  to at most one durable operation and do not issue duplicate POSTs; a
  completed operation is a no-op on later execution.
- [x] `manual_db` reconciliation can record a verified `SolID` or explicitly
  release an operation only after proof of remote absence, preserves prior
  evidence, is replay/conflict-safe, and leaves actor unset without a
  trustworthy administrative identity.
- [x] Logs, metrics, exceptions, fixtures, durable state, and provider error
  handling contain no token, header, raw payload/provider body, full
  classification content, PII, or conversation content.
- [x] Existing HTTP, webhook, finalization, IA, classification, identity,
  department-mapping, and Redis authority contracts remain unchanged and no
  public/admin HTTP endpoint or lifecycle behavior is added.
- [x] Deterministic unit tests and disposable-PostgreSQL tests cover the
  positive, negative, data-integrity, retry, idempotency, concurrency, crash,
  reconciliation, rollback, and privacy cases above.
- [x] Focused tests, the applicable offline and PostgreSQL runner stages,
  compileall, strict Pyright, `git diff --check`, and `graphify update .`
  pass; missing external prerequisites are reported separately from local
  passes and skips.
- [x] Implementation-derived documentation, SPEC-0011/spec-index status,
  exact local evidence, Graphify metadata, and `IMPLEMENTATION_PLAN.md`
  Milestone E traceability are synchronized, and the issue is closed through
  one focused commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — Approved Acessórias milestones, item 5,
  **P1 | completed locally | implemented** Milestone E;
  see **Specification boundary and next gate**.
- Primary specification: `specs/0011-durable-acessorias-request-creation.md`
  v1.1.
- Cross-cutting contracts: `specs/0001-shared-data-and-analysis-contract.md`,
  `specs/0003-durable-finalization-and-media.md`, and
  `specs/0004-reproducible-verification-baseline.md`.
- Dependency contracts: `specs/0007-acessorias-external-directory-foundation.md`,
  `specs/0008-digisac-contact-identity-foundation.md`,
  `specs/0009-digisac-acessorias-identity-resolution.md`, and
  `specs/0010-digisac-acessorias-department-mapping.md`.
- Related implementation issues:
  `issues/0012_-_implement-acessorias-directory-foundation.md`,
  `issues/0013_-_implement-digisac-contact-identity-foundation.md`,
  `issues/0014_-_implement-digisac-contacts-full-backfill.md`,
  `issues/0015_-_implement-digisac-acessorias-identity-resolution.md`, and
  `issues/0016_-_implement-digisac-acessorias-department-mapping.md`.

---

## Resolution

Implemented the approved Milestone E increment in issue 0017.

- Added Alembic `0019_acessorias_request_creation` with one operation per source
  cycle, source classification/company/mapping references, safe payload metadata
  and fingerprint, claim/lease state, `SolID`, sanitized outcomes, audit rows,
  and populated-state downgrade refusal.
- Added `src/core/acessorias_requests.py` for the six-field multipart provider
  boundary, deterministic title truncation, centralized priority/type, bounded
  safe retry, conservative uncertainty handling, durable orchestration, claim
  races, crash classification, and `manual_db` reconciliation/release.
- Integrated the operation after terminal classification in `IAWorker`; Request
  delivery is isolated so provider/reconciliation failures cannot change the
  classification or cycle result. No HTTP route, lifecycle operation, provider
  idempotency key, or real credential was added.
- Added deterministic adapter tests and disposable PostgreSQL coverage for
  eligibility, payload/privacy, `id` confirmation, safe retry, uncertain
  outcomes, uniqueness, concurrent claims, replay, reconciliation, release,
  and migration-backed persistence.
- Updated SPEC-0011, `specs/README.md`, `IMPLEMENTATION_PLAN.md`, README, PRD,
  ARCHITECTURE, `scripts/verify.py`, database revision checks, and test database
  setup to record the implemented increment and local-only evidence.

Validation performed:

- `python -m compileall -q src tests alembic scripts` — passed.
- `npx --yes pyright` — 0 errors, 0 warnings, 0 informations.
- `PYTHONPATH=/app python -m pytest -q tests/test_acessorias_requests.py` —
  focused provider and PostgreSQL tests passed in the canonical runner.
- `PYTHONPATH=/app python scripts/verify.py` — compileall, Pyright, offline
  pytest (**183 passed, 60 skipped**), Alembic head
  `0019_acessorias_request_creation`, and PostgreSQL pytest (**60 passed,
  183 deselected**) passed on disposable PostgreSQL 16.
- `git diff --check` — passed before commit; `graphify update .` — completed.

No external provider, credential, Redis, deployment, or production acceptance
was claimed. The focused commit is created after final diff and hook checks.
