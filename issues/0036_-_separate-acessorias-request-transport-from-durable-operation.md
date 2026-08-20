---
id: 0036
title: "Separate Acessórias Request transport from durable operation orchestration"
type: refactor
status: closed
priority: medium
phase: 5
created_at: 2026-08-20
updated_at: 2026-08-20
closed_at: 2026-08-20
related_issues:
  - "0017"
  - "0018"
  - "0019"
  - "0021"
  - "0022"
  - "0026"
  - "0034"
blocked_by:
  - "0034"
affects:
  - src/core/acessorias_requests.py
  - src/core/acessorias_request_provider.py
  - src/core/acessorias_preparation.py
  - src/workers/ia_worker.py
  - tests/test_acessorias_requests.py
  - tests/test_acessorias_request_provider.py
  - tests/test_acessorias_preparation.py
  - README.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
---

## Description

`src/core/acessorias_requests.py` is a 1,178-line module whose own docstring
identifies two different responsibilities: the Acessórias HTTP provider adapter
and the PostgreSQL-backed Request operation/orchestration layer. The provider
section at `:28-394` imports `requests`, configuration, and the rate-admission
primitive, owns multipart encoding, Bearer authentication, provider retry and
`Retry-After` handling, response parsing, `SolID` confirmation, and outcome
classification. The remainder at `:397-1178` imports `psycopg` and the database
pool, reads cycle and mapping facts, persists one operation per cycle, manages
claims/leases and `post_started_at`, and implements manual reconciliation and
release. These responsibilities currently share one import and test boundary.

Graphify confirms that `AcessoriasRequestAdapter` and
`create_request_for_cycle()` are coupled inside the same module, while source
inspection confirms the stronger split: the adapter is instantiated only as the
default provider at `:956`, and the durable path is independently exercised by
operation, reconciliation, preparation, and PostgreSQL tests. The same module
also imports a private `_RateLimiter` from `acessorias_directory.py`, which is
already scheduled for removal by issue `0034`.

The approved architecture already describes Acessórias provider access and the
durable Request operation as separate boundaries. SPEC-0011 likewise makes the
provider responsible for the six-field multipart call and conservative outcome
classification while PostgreSQL remains the authority for operation state,
claims, reconciliation, and duplicate prevention. Extracting the provider
transport behind `src/core/acessorias_request_provider.py` will make those
existing responsibilities independently testable and prevent database changes
from requiring HTTP/provider code to be imported, without changing either
contract.

## Target boundary and expected outcome

Create one focused internal `src/core/acessorias_request_provider.py` module as
the substantive owner of the existing provider-facing contract and adapter:
`AcessoriasRequestPayload`, `build_request_payload`, `AcessoriasRequestOutcome`,
`AcessoriasRequestProvider`, `AcessoriasRequestPreSendError`, the safe provider
outcome normalization it currently uses, and `AcessoriasRequestAdapter`. It may
depend on `requests`, the existing configuration source, and the neutral
provider-coordination boundary produced by issue `0034`; it must not depend on
PostgreSQL, Redis, cycle state, identity/mapping persistence, or the worker.

Keep `src/core/acessorias_requests.py` as the durable Request operation boundary.
It remains responsible for cycle/mapping eligibility, payload reload and
validation before `post_started_at`, operation creation, claims/leases,
provider invocation orchestration, durable outcome recording, and
`manual_db` reconciliation/release. It imports the provider-facing types and
adapter, but contains no substantive HTTP/session/authentication/response
classification implementation.

Retain compatibility imports from `src.core.acessorias_requests` for all
currently imported provider symbols, including the adapter, payload builder,
outcome type, and pre-send marker. Existing worker/preparation imports,
provider injection, async signatures, test monkeypatch seams, and returned
shapes must remain compatible. No new public HTTP, CLI, provider, or database
layer is introduced.

## Scope

### In scope

- Extract the provider-facing payload/outcome contract and HTTP adapter from
  `acessorias_requests.py` into the focused provider module.
- Leave the PostgreSQL operation/reconciliation implementation in
  `acessorias_requests.py`, with a one-way dependency on the extracted provider
  contract and adapter and no duplicate provider logic.
- Preserve compatibility re-exports or perform one source-confirmed atomic
  import migration while retaining existing worker, preparation, and test
  seams.
- Split direct adapter tests from durable operation tests where useful, while
  retaining integration coverage proving the durable path invokes the same
  provider protocol and no second provider implementation exists.
- Synchronize only implementation-derived source ownership/documentation and
  plan traceability after the implementation passes; run `graphify update .`
  after code changes.

### Out of scope

- Changing the six multipart fields, `tipo=E`, priority `2`, subject protocol
  prefix/truncation, description source, endpoint, Bearer configuration,
  `SolID` success rule, provider response/error categories, or rate-admission
  behavior.
- Changing pre-send retry eligibility, ordinary transport uncertainty,
  `429` reconciliation, bounded retry/backoff, provider call count, claims,
  leases, transaction boundaries, `post_started_at`, durable state values,
  idempotency, concurrency, failure, or manual reconciliation/release rules.
- Moving or redesigning the Directory repository, identity/mapping/preparation
  behavior, database facade slices from issues `0028-0033`, the neutral limiter
  from issue `0034`, or the future Request lifecycle in Milestone F.
- Any Alembic migration, schema/index/constraint change, data rewrite, runtime
  schema creation, Redis state change, configuration/dependency/infrastructure
  change, public API/CLI/event change, authorization/security/retention change,
  or provider idempotency key.
- Changing prompt/classification behavior, Request scheduling, worker
  lifecycle, user-visible workflow, or documentation policy beyond updating
  the implementation source map and structural ownership.

## Invariants

- The provider adapter still sends exactly one approved multipart Request shape:
  `assunto`, `empresa`, `departamento`, `prioridade`, `descricao`, and `tipo=E`.
  Protocol-aware subject construction, deterministic 100-character truncation,
  description provenance, and default priority remain unchanged.
- Bearer authentication continues to come only from the existing secure
  configuration boundary. Tokens, headers, raw payloads, provider bodies,
  classification content, contact values, and PII remain absent from logs,
  exceptions, limiter state, fixtures, and durable operational metadata.
- A provider response confirms completion only when it contains a valid,
  non-empty `id`; `SolID` persistence and all sanitized success, definitive,
  retryable, and reconciliation outcome categories remain unchanged.
- Only `AcessoriasRequestPreSendError` can enter the existing bounded retry
  path. Ordinary connection, timeout, protocol, uncertain `5xx`, and unproven
  `429` outcomes remain reconciliation-required and cannot cause an automatic
  second POST. Admission remains immediately before every existing provider
  attempt, using the issue-0034 neutral boundary without changing its scope or
  key semantics.
- A terminal eligible cycle still creates at most one durable operation;
  PostgreSQL remains authoritative for operation state. Payload load and
  validation still happen before `post_started_at`; claims, lease expiry,
  crash-before/after-POST classification, transaction commits, replay no-op,
  `SolID`, and manual reconciliation/release guards remain identical.
- The existing `create_request_for_cycle`, `recover_mapping_missing_request`,
  `reconcile_request_operation`, and `release_request_operation` signatures,
  return shapes, provider-injection protocol, worker call path, and preparation
  ordering remain unchanged.
- No public route, response, event, CLI interface, database schema or
  persistence semantics, authorization/security policy, retry/idempotency/
  concurrency/failure semantics, dependency, configuration, or deployment
  contract changes.

## Implementation Plan

1. Inventory the current imports, top-level provider symbols, default adapter
   construction, provider-injection protocol, compatibility imports, test
   monkeypatches, and all durable operation seams. Mark the historical provider
   block (`acessorias_requests.py:28-394`) and durable block
   (`acessorias_requests.py:397-1178`) as the extraction boundary, and confirm
   issue `0034` has established the neutral limiter dependency first.
2. Add `src/core/acessorias_request_provider.py` with the current provider
   payload/outcome contract, safe provider-category normalization, explicit
   pre-send marker, and `AcessoriasRequestAdapter` implementation. Preserve
   the current constructor injection points for session, sleep, clock, timeout,
   attempts, retry limits, and rate limit. Do not add database or Redis imports.
3. Replace the provider implementation in `acessorias_requests.py` with
   imports from the new boundary. Keep its durable helpers and orchestration
   local, retain compatibility re-exports for every existing provider symbol,
   and verify that no HTTP/session/authentication/response-classification logic
   is duplicated or left in the durable module.
4. Move only direct provider-adapter tests to
   `tests/test_acessorias_request_provider.py` if that improves isolation; keep
   operation/reconciliation tests and their existing monkeypatch targets in
   `tests/test_acessorias_requests.py`. Add an explicit compatibility test for
   old imports and an integration test for the injected provider protocol.
5. Run the focused provider, durable-operation, preparation, and Directory
   regression tests, then compileall, strict Pyright, `git diff --check`, and
   the canonical disposable runner when its prerequisites are available.
   After validation, update only the affected implementation source map and
   plan traceability, run `graphify update .`, and close the issue with one
   focused commit.

## Tests

- **Provider boundary:**
  `PYTHONPATH=/app python -m pytest -q tests/test_acessorias_request_provider.py`
  (or the provider-focused subset retained in
  `tests/test_acessorias_requests.py`) covering payload fields, subject
  truncation, missing credentials, response `id`, error classification,
  pre-send retry, ambiguous transport, `429`, `Retry-After`, endpoint/rate
  isolation, and concurrent admission.
- **Durable operation and compatibility:**
  `PYTHONPATH=/app python -m pytest -q tests/test_acessorias_requests.py tests/test_acessorias_preparation.py`
  covering operation uniqueness, payload-load failure before POST, claim/lease
  recovery, replay, concurrent execution, `SolID`, reconciliation/release,
  mapping gates, compatibility imports, and provider injection.
- **Directory regression after limiter prerequisite:**
  `PYTHONPATH=/app python -m pytest -q tests/test_acessorias_directory.py`
  covering the unchanged adapter-local admission and snapshot publication.
- **Static and hygiene:**
  `python -m compileall -q src tests alembic scripts`,
  `npx --yes pyright`, and `git diff --check`; inspect imports/AST to prove the
  provider module has no PostgreSQL/Redis dependency and the durable module has
  no duplicate HTTP adapter implementation.
- **Canonical disposable verification:**
  `PYTHONPATH=/app python scripts/verify.py`; report unavailable Compose,
  PostgreSQL, Redis, DigiSac, Groq, or production prerequisites separately from
  local/disposable passes and skips.
- **Graph:** `graphify update .` after implementation changes.

## Acceptance Criteria

- [x] `src/core/acessorias_request_provider.py` is the single substantive owner
  of the current Acessórias Request payload/outcome contract and HTTP adapter;
  `acessorias_requests.py` contains no duplicate provider transport logic.
- [x] `acessorias_requests.py` remains the single substantive owner of durable
  Request operation creation, eligibility, claims/leases, pre-POST payload
  validation, outcome persistence, and manual reconciliation/release, with no
  PostgreSQL or cycle-state dependency in the provider module.
- [x] Existing imports from `src.core.acessorias_requests` for the adapter,
  payload builder, outcome, provider protocol, and pre-send marker remain
  import- and call-compatible, and `IAWorker`/preparation provider injection
  continues to work without source changes to their public contracts.
- [x] Provider tests prove unchanged multipart fields, credentials boundary,
  subject/description construction, `id` confirmation, safe categories,
  pre-send-only retry, ambiguous outcomes, unproven `429`, bounded backoff,
  and rate admission; no provider call is added, removed, reordered, or
  duplicated.
- [x] Durable tests prove unchanged one-operation-per-cycle behavior,
  PostgreSQL authority, payload-load boundary before `post_started_at`, claim
  and lease recovery, concurrent convergence, replay no-op, `SolID`, and
  manual reconciliation/release semantics.
- [x] The neutral rate-admission boundary from issue `0034` is the only limiter
  dependency; no cross-domain private Directory import or accidental shared
  Directory/Request budget is reintroduced.
- [x] No public API/CLI/event contract, schema or persistence semantics,
  security/privacy/retention policy, retry/idempotency/concurrency/failure
  semantics, dependency, configuration, Redis state, credential, PII, or
  provider behavior changes are present.
- [x] Focused tests, compileall, strict Pyright, `git diff --check`, and the
  canonical disposable runner pass, with local/disposable evidence separated
  from external-provider, Redis, deployment, and production evidence.
- [x] README/ARCHITECTURE/IMPLEMENTATION_PLAN source ownership and traceability
  remain internally consistent, SPEC-0011 is not rewritten, Graphify is
  updated, all acceptance boxes remain unchecked until validation, and the
  issue is closed only after validation and one focused commit.

## References

- **Primary contract:** `specs/0011-durable-acessorias-request-creation.md`
  v1.4, especially the provider boundary, six-field multipart contract,
  conservative retry/reconciliation rules, durable operation state, and
  compatibility/verification requirements.
- **Cross-cutting contracts:**
  `specs/0001-shared-data-and-analysis-contract.md`,
  `specs/0003-durable-finalization-and-media.md`, and
  `specs/0004-reproducible-verification-baseline.md`.
- **Product/architecture:** `PRD.md` §§5.5 and 8;
  `ARCHITECTURE.md` §§2.1, 12, and 14. These already distinguish the
  Acessórias provider boundary from the durable PostgreSQL Request operation.
- **Plan:** `IMPLEMENTATION_PLAN.md` Milestone E and its completed local
  evidence; this is structural maintenance after the approved Request
  contract, not Milestone F lifecycle work.
- **Current source evidence:** `src/core/acessorias_request_provider.py:1-340`
  owns the provider contract and adapter; `src/core/acessorias_requests.py:1-844`
  owns the durable operation, claims, reconciliation, and release path and
  imports the provider symbols for compatibility. The default adapter is
  constructed at `:646`, while `create_request_for_cycle()` starts at `:610`
  and `reconcile_request_operation()` at `:832`. `src/workers/ia_worker.py`
  still imports only the durable entrypoint, and direct boundary/compatibility
  coverage is in `tests/test_acessorias_request_provider.py`.
- **Graph evidence:** the post-extraction Graphify path query confirms
  `AcessoriasRequestAdapter` is called by `create_request_for_cycle()` across
  the new provider/durable boundary; `graphify update .` rebuilt the graph with
  2,111 nodes and 4,438 edges.
- **Related implementation:** issue `0017` introduced the combined provider
  and durable Request module; `0018`, `0019`, `0021`, `0022`, and `0026`
  established the transport, admission, pre-POST, uncertain-`429`, and
  preparation invariants that this extraction must preserve. Issue `0034` is
  the prerequisite neutral-limiter move. Issues `0028-0033` concern unrelated
  PostgreSQL-facade slices.
- **Non-duplicate rationale:** no existing issue separates the Acessórias
  Request HTTP/provider implementation from the durable Request operation.
  Issue `0034` only moves the shared limiter and explicitly leaves Request
  persistence/orchestration out of scope; the completed behavior issues change
  contracts rather than module ownership.

## Resolution

Implemented the behavior-preserving provider/durable-operation boundary.

- `src/core/acessorias_request_provider.py` now owns the existing Request
  payload builder, outcome categories, pre-send marker, provider protocol, and
  multipart HTTP adapter, including the existing credentials, response-ID,
  retry, `429`, backoff, and neutral rate-admission behavior.
- `src/core/acessorias_requests.py` now contains only the durable PostgreSQL
  operation path: eligibility, operation creation, payload reload and
  pre-POST validation, claims/leases, post-start marking, outcome persistence,
  preparation recovery, and manual reconciliation/release. Provider symbols
  remain compatibility re-exports; `IAWorker`, preparation, and existing
  injected providers required no public-contract changes.
- Added direct provider-boundary and compatibility coverage in
  `tests/test_acessorias_request_provider.py`; existing provider, durable,
  preparation, Directory, concurrency, retry, reconciliation, and injection
  coverage remains in the established test modules. No migration was needed.
- Updated README, ARCHITECTURE, SPEC-0011, `specs/README.md`, and
  `IMPLEMENTATION_PLAN.md` with implementation-derived ownership and
  traceability. Graphify was updated after the code changes.

Key decisions preserved: the six multipart fields, `tipo=E`, priority `2`,
protocol subject prefix/truncation, Bearer boundary, `SolID` confirmation,
pre-send-only retry, uncertain transport/`429` reconciliation, claims,
leases, operation uniqueness, and all durable state transitions. No public
API/CLI/event, schema, dependency, configuration, Redis, credential, PII,
idempotency, concurrency, or provider behavior changed.

Validation performed:

- `PYTHONPATH=/app python -m pytest -q tests/test_acessorias_request_provider.py tests/test_acessorias_requests.py tests/test_acessorias_preparation.py tests/test_acessorias_directory.py` — **41 passed, 12 skipped**.
- `python -m compileall -q src tests alembic scripts` — passed.
- `npx --yes pyright` — **0 errors, 0 warnings, 0 informations**.
- `git diff --check` — passed.
- `PYTHONPATH=/app python scripts/verify.py` — compileall, Pyright, offline
  pytest **224 passed, 69 skipped**, disposable PostgreSQL 16/Alembic head
  `0020_cycle_contact_provenance`, and PostgreSQL pytest **69 passed, 224
  deselected** all passed. This is local/disposable evidence only; it does
  not prove external provider, Redis, deployment, or production readiness.
- `graphify update .` — updated the code graph to 2,111 nodes, 4,438 edges,
  and 146 communities. Known warnings for `pyrightconfig.json` and SQL
  extraction without `tree_sitter_sql` remain non-blocking repository/tooling
  limitations.
