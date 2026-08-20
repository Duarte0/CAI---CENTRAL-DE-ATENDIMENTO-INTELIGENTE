---
id: 0035
title: "Isolate the IA classification contract from the cycle worker"
type: refactor
status: closed
priority: medium
phase: 5
created_at: 2026-08-20
updated_at: 2026-08-20
closed_at: 2026-08-20
related_issues:
  - "0010"
  - "0024"
  - "0032"
blocked_by: []
affects:
  - src/workers/ia_worker.py
  - src/core/ia_classification.py
  - tests/test_ia_worker_intent.py
  - README.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
---

## Description

`src/workers/ia_worker.py` is a 1,312-line persistent-cycle worker that owns
queue consumption, cycle recovery, history/context preparation, media
coordination, provider cooldowns, classification, persistence, and the
Acessórias preparation handoff. Its model-facing classification contract is a
separate responsibility embedded near the end of that worker: the same class
builds the system/user prompts, recovers JSON from wrapped Groq output,
validates the four-field result, normalizes intent values, and emits parser
diagnostics (`src/workers/ia_worker.py:959-1238`).

The embedded contract is already independently observable and independently
tested. `tests/test_ia_worker_intent.py` constructs an uninitialized
`IAWorker` solely to exercise `_parse_result()` and `_build_prompt()`, while
the actual worker method also owns the Groq call and provider-error conversion.
The source search confirms that these private prompt/parser methods have no
consumer outside `IAWorker` and this focused test module. This couples pure
contract tests to a stateful worker and makes a prompt, parser, or privacy
change require loading unrelated cycle and Redis orchestration.

The repository already gives this boundary stable sources of truth: the intent
taxonomy and normalization live in `src/core/intents.py`, context construction
lives in `src/core/finalization.py`, and `SPEC-0001` defines the four-field
model result, invalid-output rule, and sanitized parser diagnostics. Closed
issues `0010` and `0024` establish the current prompt taxonomy and logging
privacy invariants. Extracting this contract is therefore a behavior-preserving
module-boundary cleanup, not a classification-policy change.

## Target boundary and expected outcome

Create one focused internal `src/core/ia_classification.py` boundary for the
model-facing classification contract. It owns only the current system prompt,
user prompt construction, wrapped/embedded JSON recovery, four-field result
validation, intent normalization, and the existing safe parser diagnostics.
It should remain independent of Redis, PostgreSQL, cycle state, the Groq SDK,
provider cooldown state, and Acessórias operations; it may use the established
intent taxonomy/normalizer.

`IAWorker` remains the lifecycle and orchestration owner. It continues to own
the empty-context fallback, Groq client construction and invocation, request
options, provider error conversion/cooldown behavior, message/timestamp
enrichment, cycle transitions, classification persistence, and the
Acessórias handoff. The worker calls the extracted boundary without duplicating
prompt or parser policy. If private forwarding methods are retained for local
compatibility, they must be zero-policy delegates; otherwise the direct tests
must migrate atomically to the extracted boundary.

No material architecture decision is required: existing `src/core` modules
already own context, taxonomy, presentation, and provider-retry contracts,
while the worker remains the process entrypoint and orchestration boundary.

## Scope

### In scope

- Extract the current prompt construction, response parsing/recovery,
  validation, normalization, and sanitized diagnostic behavior into one
  internal classification-contract module.
- Wire `IAWorker._analyze_with_groq()` to the extracted functions while
  preserving its current async/provider boundary, result enrichment, and
  exception behavior.
- Migrate or preserve the existing focused test seams and add direct tests for
  the extracted pure contract, including valid, wrapped, nested, truncated,
  incomplete, invalid, unsupported-intent, taxonomy, and privacy cases.
- Make ownership and dependency direction explicit in implementation-derived
  source-map documentation, then update Graphify after implementation without
  rewriting completed historical contracts or issues.

### Out of scope

- Changing the prompt wording, intent taxonomy, confidence interpretation,
  description rules, model choice, prompt version, or any other IA/product
  policy; broader classification-policy changes remain blocked by the plan.
- Correcting the existing source/spec discrepancy in which
  `src/core/finalization.py:294` and the worker's model-context path currently
  include a `PROTOCOLO` header while the active contract says protocol metadata
  must not enter model context. That policy correction belongs to a dedicated
  bug/spec pass; this extraction must preserve the current source behavior.
- Moving the Groq SDK call, credentials, model/token configuration, provider
  cooldown window, retry/backoff, dead-letter handling, or transient-error
  classification out of the worker.
- Changing empty-context/no-classifiable-message fallback, context rendering,
  history filtering, media waits, cycle claims/transitions/leases, Redis
  queues/results, classification persistence, protocol/display-title
  enrichment, or Acessórias identity/mapping/Request sequencing.
- Changing any HTTP route, webhook payload/event, CLI interface, database
  schema/persistence semantics, authorization/security/retention policy,
  logging policy, dependency, configuration, infrastructure, or provider
  contract.
- Repeating the classification persistence extraction already scoped by issue
  `0032`, or performing unrelated worker cleanup.

## Invariants

- Accepted model output remains the same four-field contract:
  `intent_type`, `confidence`, `title`, and `description`. Missing, malformed,
  incomplete, or truncated output is still rejected and never persisted as a
  valid classification.
- Complete JSON, JSON embedded after reasoning/markdown, nested valid objects,
  braces inside JSON strings, Portuguese/case/format intent variants, and
  unsupported intent values retain their current outcomes. Unsupported values
  still normalize to `other` with confidence `0.0`.
- The prompt retains the current client/attendant context instructions,
  canonical taxonomy including `financial`, payment-versus-billing guidance,
  description/status rules, and exclusion of application-derived fields such
  as department and agent. The existing model-context construction, including
  its current protocol/header behavior, remains unchanged; the `protocol`
  intent category remains part of the existing taxonomy.
- Parser diagnostics remain limited to the current safe outcome and bounded
  structural metadata. Raw or partial Groq responses, reasoning, customer
  content, secrets, URLs, and binary data remain absent from logs, snapshots,
  queue payloads, and durable operational records. Accepted title and
  description fields continue through the existing classification persistence
  path; this issue does not change that behavior.
- Groq invocation remains before the same parser, with the same model,
  temperature, token budget, JSON response format, reasoning option, empty
  response/token-limit errors, provider exception conversion, retry/dead-letter
  handling, cooldown behavior, and attempt ordering.
- Cycle state transitions, claims/leases, `ia:cycle:{cycle_id}` idempotency,
  `insert_classification()` inputs, `public_id`, prompt-version persistence,
  Redis compatibility results, protocol enrichment, and Acessórias preparation
  remain unchanged.
- No public API, route, event, CLI, schema, persisted-state shape, security or
  privacy policy, retry/idempotency/concurrency/failure semantic, dependency,
  configuration, or deployment contract changes.

## Implementation Plan

1. Inventory the current private method callers, imports, test monkeypatch
   seams, prompt text, parser recovery order, validation mutations, and safe
   log fields. Treat the existing result dictionary shape and all focused test
   outcomes as compatibility contracts.
2. Add `src/core/ia_classification.py` with the extracted pure boundary. Keep
   the current prompt text and parser/validator decision order intact; reuse
   `src.core.intents` rather than duplicating the canonical taxonomy, and do
   not introduce a provider client, database access, Redis state, or new
   configuration.
3. Replace the worker-owned prompt/parser logic with direct calls to the new
   boundary, or thin private delegates only where needed to preserve confirmed
   in-repository seams. Keep `_analyze_with_groq()` responsible for provider
   setup/invocation, empty-context behavior, transient classification, result
   enrichment, and timestamps.
4. Move the pure contract assertions to direct module-level tests while
   retaining worker-level tests for Groq request options, token truncation,
   provider failures, and the unchanged cycle integration. Do not weaken
   privacy assertions while changing test imports.
5. Run focused and canonical verification. Only after the implementation
   passes, synchronize the affected README/architecture/source-map and plan
   status text, run `graphify update .`, and close the issue with one focused
   commit.

## Tests

- **Classification boundary and provider seam:**
  `PYTHONPATH=/app python -m pytest -q tests/test_ia_worker_intent.py tests/test_ia_worker_retry.py`
- **Persistent-cycle regression:**
  `PYTHONPATH=/app python -m pytest -q tests/test_conversation_finalization.py tests/test_operational_recovery_db.py`
- **Static:** `python -m compileall -q src tests alembic scripts` and
  `npx --yes pyright`
- **Canonical disposable verification:**
  `PYTHONPATH=/app python scripts/verify.py`
- **Hygiene:** `git diff --check`, plus a targeted import search proving that
  the worker is the only owner of provider invocation and no duplicate prompt
  or parser implementation remains.
- **Graph:** `graphify update .` after implementation changes.

## Acceptance Criteria

- [x] `src/core/ia_classification.py` is the single substantive owner of the
  current IA prompt construction, JSON recovery, four-field validation,
  normalization, and parser diagnostics; `IAWorker` contains no duplicated
  implementation of those responsibilities.
- [x] The extracted boundary has no dependency on Redis, PostgreSQL, cycle
  state, Acessórias modules, Groq SDK/client state, credentials, or provider
  cooldowns, and remains usable in isolation with the existing dictionary
  result shape.
- [x] Direct boundary tests and worker integration tests prove unchanged
  valid, wrapped/nested, brace-containing, invalid, incomplete, truncated,
  unsupported-intent, taxonomy, and empty-response outcomes.
- [x] The prompt remains parity-checked against `VALID_INTENT_TYPES`, includes
  the approved `financial` guidance and existing payment/billing precedence,
  and does not introduce application-derived fields. Existing model-context
  header behavior, including the current protocol/header discrepancy, remains
  unchanged.
- [x] Parser logs retain only the existing safe outcome/bounded metadata and
  privacy tests prove that raw response, reasoning, title, description, and
  customer-content sentinels remain absent.
- [x] Groq request options, client/provider error handling, cooldown/backoff,
  retry/dead-letter behavior, result timestamps/counts, classification
  persistence, cycle terminal states, Redis result projection, and Acessórias
  sequencing are behaviorally unchanged.
- [x] No public HTTP/CLI/event contract, database schema or persistence
  semantics, security/retention policy, retry/idempotency/concurrency/failure
  semantics, dependency, configuration, or infrastructure changes are present.
- [x] Focused tests, persistent-cycle regression tests, compileall, strict
  Pyright, `git diff --check`, and the canonical disposable runner pass, with
  local/disposable evidence distinguished from Groq, Redis, deployment, and
  production evidence.
- [x] README/architecture/source-map and plan references remain internally
  consistent, Graphify is updated, all acceptance boxes remain unchecked until
  validation, and the issue is closed only after validation and one focused
  commit.

## References

- **Primary contract:** `specs/0001-shared-data-and-analysis-contract.md`
  v1.4, especially the four-field IA response, invalid-output rejection,
  intent normalization, privacy, observability, and worker-test requirements.
- **Finalization/context contract:**
  `specs/0003-durable-finalization-and-media.md` v1.5, especially persistent
  cycle ordering, context construction before classification, idempotent
  terminal analysis, and failure/recovery boundaries.
- **Verification contract:** `specs/0004-reproducible-verification-baseline.md`
  v1.6 and the canonical `scripts/verify.py` runner.
- **Product/architecture:** `PRD.md` §§5.4, 6, and 8;
  `ARCHITECTURE.md` §§2, 5.1, 8, 12, and 14; `README.md` §§Architecture,
  Finalização persistente, and Tratamento do contexto.
- **Plan:** `IMPLEMENTATION_PLAN.md` §§1, 3, and 4. Persistent analysis,
  taxonomy parity, and parser privacy are complete; broader IA policy is
  explicitly blocked. This is structural maintenance after the completed
  baseline, not a new product milestone.
- **Current source evidence:** `src/workers/ia_worker.py:61-90` shows the
  worker-wide lifecycle/configuration state; `:648-676` shows classification
  handoff and durable persistence; `:958-1015` retains provider invocation,
  empty-context handling, response limits, enrichment, and error conversion.
  `src/core/ia_classification.py:18-256` owns the pure prompt/parser contract;
  `src/core/intents.py` owns canonical intent normalization and
  `src/core/finalization.py` owns context construction. Source still emits a
  `PROTOCOLO` header at `src/core/finalization.py:294`, despite the active
  contract's prohibition on protocol metadata in model context; that
  discrepancy is explicitly outside this refactor.
- **Current tests/evidence:** `tests/test_ia_worker_intent.py` directly tests
  the extracted prompt/parser boundary and separately verifies Groq request
  options, truncation, and empty-response handling; the focused current run
  passed **27 tests**.
- **Related implementation issues:** `0010` restored prompt taxonomy parity;
  `0024` removed raw Groq-response logging; `0032` isolates classification
  persistence from `db.py` and is not a duplicate of this model-contract
  boundary.
- **Non-duplicate rationale:** no existing issue extracts the prompt,
  response parser, validation, and safe diagnostics from `IAWorker`. Issues
  `0010` and `0024` are closed behavior/privacy corrections, while issue
  `0032` concerns PostgreSQL classification persistence only.

## Resolution

Implemented the behavior-preserving IA classification boundary extraction.

- `src/core/ia_classification.py` now owns the existing system/user prompts,
  wrapped/nested JSON recovery, four-field validation, intent normalization, and
  bounded parser diagnostics. `IAWorker` directly calls this boundary and keeps
  Groq invocation, empty-context fallback, provider errors/cooldown, result
  enrichment, persistence, and cycle orchestration.
- `tests/test_ia_worker_intent.py` now exercises the pure contract directly and
  retains worker integration checks, including a regression for empty provider
  output. No migration was required.
- README, ARCHITECTURE, SPEC-0001, SPEC-0003, SPEC-0004, `specs/README.md`, and
  `IMPLEMENTATION_PLAN.md` record the new ownership and unchanged contracts;
  Graphify was updated with `graphify update .`.
- Key decision: retain the current prompt text, taxonomy, protocol/header
  behavior, parser order, safe log fields, and all provider/cycle semantics;
  this issue introduces only the internal module boundary.
- Validation: focused IA/retry tests **27 passed**; offline pytest **222
  passed, 69 skipped**; `python -m compileall -q src tests alembic scripts`
  passed; strict Pyright passed; the disposable canonical runner passed
  compileall, Pyright, offline pytest, PostgreSQL 16 startup/connectivity,
  Alembic head `0020_cycle_contact_provenance`, and **69 PostgreSQL tests**.
