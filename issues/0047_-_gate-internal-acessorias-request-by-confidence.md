---
id: 0047
title: "Gate internal Acessórias Request creation by classification confidence"
type: feature
status: closed
priority: high
phase: 4
created_at: 2026-08-26
updated_at: 2026-08-26
closed_at: 2026-08-26
related_issues:
  - "0017"
  - "0018"
  - "0019"
  - "0021"
  - "0022"
  - "0026"
  - "0035"
  - "0036"
blocked_by: []
affects:
  - src/core/ia_classification.py
  - src/core/classification_repository.py
  - src/core/acessorias_request_provider.py
  - src/core/acessorias_requests.py
  - src/workers/ia_worker.py
  - tests/test_ia_worker_intent.py
  - tests/test_acessorias_request_provider.py
  - tests/test_acessorias_requests.py
  - tests/test_acessorias_preparation.py
  - tests/test_postgres_evolution.py
  - specs/0011-durable-acessorias-request-creation.md
  - specs/README.md
  - PRD.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
  - README.md
---

## Description

Before issue 0047, the durable Acessórias flow validated terminal-cycle eligibility,
canonical contact identity, and department mapping before the provider call,
but did not validate the classification `confidence` as a business gate.
`_operation_state()` can therefore return `not_started`, and
`create_request_for_cycle()` can eventually mark `post_started_at` and invoke
the provider without confirming that the classification has the minimum
confidence required to open a Request.

The required rule is: before sending any Acessórias Request, confirm the
persisted classification confidence; a score below 5 must not open the
Request. The existing IA contract emits and persists `confidence` on a `0..1`
scale, not a `0..10` scale. This issue defines the requested business rule as a
`0..10` gate without changing the IA field, database type, or public result
contract: `confidence_10 = confidence * 10`, so the minimum is `5.0`,
equivalent to `confidence >= 0.50`. `0.50` is accepted; values below it,
`NULL`, malformed values, and values outside the existing `0..1` contract are
blocked. The implementation must not compare the raw persisted `0.90` value
directly with `5` and must not silently migrate the IA contract to `0..10`.

The previous provider contract sent `tipo=E` from
`src/core/acessorias_request_provider.py`. Product behavior is now explicit:
automatic ticket opening in Acessórias is always **internal**, never external.
Every future automatic provider POST must therefore send `tipo=I`; there is no
caller override, configuration switch, or fallback to `tipo=E`.

This is a behavior/contract correction across the already implemented Request
boundary, not a request to change identity matching, routing, classification,
or Request lifecycle. The durable operation may record that a cycle was
blocked for auditability, but that row must never be described or treated as an
externally opened Acessórias Request.

### Current implementation evidence

- [`validate_result()`](/app/src/core/ia_classification.py:84) accepts only
  numeric confidence in `0..1`; the prompt and output example use the same
  scale at [`build_prompt()`](/app/src/core/ia_classification.py:161).
- [`insert_classification()`](/app/src/core/classification_repository.py:90)
  persists the result confidence in `ia_classifications.confidence`, whose
  PostgreSQL check is also `0..1`.
- [`_operation_state()`](/app/src/core/acessorias_requests.py:184) now selects
  and evaluates confidence before returning an eligible operation.
- [`create_request_for_cycle()`](/app/src/core/acessorias_requests.py:810)
  loads the payload, checks confidence, and relies on a final guard before
  `post_started_at` and the provider call.
- [`REQUEST_TYPE`](/app/src/core/acessorias_request_provider.py:35) is now
  the single internal value `"I"`, and payload tests assert `tipo=I`.
- The worker already orders preparation before Request creation at
  [`IAWorker._prepare_and_create_request()`](/app/src/workers/ia_worker.py:191);
  this issue adds the confidence and internal-type gates within the durable
  Request boundary and does not move provider work into the webhook path.

The links above intentionally describe the current checkout. The source path
for `_operation_state()` and `create_request_for_cycle()` is
`/app/src/core/acessorias_requests.py`.

## Scope

### In scope

- Read the confidence belonging to the source cycle's persisted
  `ia_classifications` row, after the existing terminal-cycle, identity, and
  mapping prerequisites and before any provider side effect.
- Implement one canonical, typed confidence policy at the durable Request
  boundary:
  - business scale: `0..10`;
  - normalized score: persisted `confidence * 10`;
  - minimum accepted score: `5.0`;
  - equivalent persisted threshold: `0.50`;
  - boundary `0.50`/`5.0`: allowed;
  - below the boundary: blocked;
  - `NULL`, non-numeric, non-finite, or outside `0..1`: blocked as invalid.
- Enforce the policy during operation eligibility and again in the final
  pre-send path, after payload load/validation and atomically before
  `post_started_at` can be set. A replay or legacy operation must not bypass
  the gate merely because its durable operation already exists.
- Persist a sanitized durable outcome for a confidence-blocked cycle using the
  existing operation state model (for example,
  `definitive_failure` with `confidence_below_threshold` or
  `confidence_invalid`). Preserve the originating classification unchanged.
  The outcome must contain no title, description, conversation content, PII,
  raw payload, token, or provider response. If an audit value is retained, it
  may contain only the normalized numeric score, scale, threshold, and safe
  decision category.
- Guarantee that a confidence-blocked operation has no provider call, no
  `post_started_at`, no `SolID`, no provider attempt, and no automatic retry
  path that can turn the same below-threshold classification into a Request.
  A durable blocked operation is an audit record, not an opened Request.
- Change the provider payload contract so `REQUEST_TYPE` is a single internal
  constant `I`. The multipart form must continue to contain exactly
  `assunto`, `empresa`, `departamento`, `prioridade`, `descricao`, and `tipo`,
  with `tipo=I` and `prioridade=2`. No public/internal caller may select `E`.
- Recompute or refresh payload fingerprint/metadata to represent `tipo=I`
  only for operations that can safely be attempted without rewriting evidence
  of a POST that already started. Completed, uncertain, reconciled, or
  otherwise post-start operations must not be edited or resent to convert an
  already existing external Request into an internal one.
- Keep existing conservative transport behavior unchanged: claims, leases,
  one operation per cycle, `SolID` handling, explicit pre-send retry,
  ambiguous transport outcomes, `429` reconciliation, and manual
  reconciliation remain in force. A safe future retry, when independently
  authorized by the existing state machine, uses `tipo=I`.
- Update implementation-derived documentation and the active specification
  index to replace the old `tipo=E` creation contract with the internal
  `tipo=I` contract and to record the confidence gate/scale. Closed issues
  remain historical records; do not rewrite their resolutions to hide that
  they implemented the previous contract.

### Out of scope

- Changing the IA model prompt's four-field shape, intent taxonomy, model
  confidence generation, or the persisted/public confidence scale from `0..1`
  to `0..10`.
- Reclassifying a low-confidence conversation, calling the model again,
  changing its title/description, or using `intent_type` as a substitute for
  confidence.
- Human approval of every high-confidence Request, a new admin endpoint/UI,
  a new Request lifecycle, comments, attachments, status changes, closure,
  reopening, or customer-facing communication.
- Identity matching, identity confirmation, department mapping, directory
  synchronization, contact hydration, assignment selection, or cycle-state
  changes.
- Converting existing completed `tipo=E` provider Requests, querying the
  provider by fragile subject/time correlation, or bulk reprocessing existing
  operations. Any live correction requires a separately authorized runbook.
- Provider idempotency keys, new public HTTP routes, webhook-time provider
  calls, Redis as authority, production calls, deployment, credentials, or
  production acceptance.

## Implementation Plan

1. Reconfirm the authoritative data path and preserve the scale boundary. Join
   the source cycle to its persisted classification confidence in the durable
   Request snapshot. Add a named domain policy/constant for the business
   threshold and normalization, rather than scattering `5`, `0.50`, or a
   converted value through worker/provider code. Validate finite numeric input
   and fail closed for `NULL`/invalid/out-of-range data. Keep
   `src/core/ia_classification.py`, `ia_classifications.confidence`, and the
   result API on `0..1`.
2. Extend `_operation_state()` and operation creation so confidence is checked
   alongside terminal status, classification presence, identity/mapping facts,
   and payload validity. Record a sanitized blocked outcome with a stable
   category and, if needed, safe gate metadata. Do not create a provider
   payload or call the adapter for a blocked classification. Preserve the
   existing operation state vocabulary unless a new state is proven necessary;
   prefer the existing `definitive_failure` semantics for a policy block.
3. Add a final race-safe guard immediately before the existing
   `_mark_post_started_sync()` transition. It must re-read or atomically
   predicate on the source classification confidence and distinguish
   `allowed`, `below_threshold`, and `invalid`. If the final guard blocks,
   release the claim and record the sanitized definitive outcome without
   setting `post_started_at`; provider construction/admission/HTTP must not
   happen. The guard must apply to normal creation, replay, and any permitted
   pre-send retry.
4. Change the provider boundary's single request type to `I`. Update
   `AcessoriasRequestPayload.form`, metadata, fingerprint fixtures, and the
   multipart adapter assertions. Keep the six field names, endpoint,
   authentication, priority, rate admission, response classification, and
   retry/reconciliation semantics unchanged. Make the absence of any `E`
   override an explicit invariant.
5. Define compatibility for durable operations created under the previous
   payload contract. Refresh only safe pre-send payload metadata/fingerprints
   when required for a future internal retry. Leave completed, uncertain,
   reconciled, and post-start evidence immutable; never issue an update request
   to the provider and never claim that a historical external Request became
   internal.
6. Add focused tests before implementation changes:
   - policy/unit coverage for `0.49`, `0.4999`, `0.50`, `0.5001`, `0.90`,
     `NULL`, malformed, `NaN`, `Infinity`, negative, and greater-than-`1`
     values;
   - disposable PostgreSQL coverage proving below-threshold/invalid cycles
     remain without provider calls, `post_started_at`, `SolID`, or attempts,
     while exactly-boundary and high-confidence cycles proceed;
   - replay/concurrency coverage proving an existing operation cannot bypass
     the final gate and that no provider call is added after a blocked result;
   - worker ordering coverage proving preparation still precedes the Request
     gate and provider call;
   - provider coverage proving the exact six multipart fields and `tipo=I`,
     never `tipo=E`, including payload metadata/fingerprint behavior;
   - regression coverage proving completed historical operations are no-op,
     uncertain operations remain reconciliation-only, and a separately safe
     retry uses the internal type without duplicating the Request.
7. Update `SPEC-0011`, `specs/README.md`, `PRD.md`, `ARCHITECTURE.md`,
   `IMPLEMENTATION_PLAN.md`, and any README source map/reference that still
   says automatic creation is external/`tipo=E`. Explicitly state that this
   issue supersedes those active statements for future automatic creation,
   while preserving history and the existing `0..1` IA contract. No OpenAPI
   route change is expected.
8. Run the focused tests, full offline suite, compileall, strict Pyright,
   disposable PostgreSQL/Alembic verification when available, secret/PII
   checks, `git diff --check`, and review the diff for any remaining
   production-path `tipo=E` or raw-confidence leakage. Update Graphify only if
   application code changes; do not treat local/double evidence as live
   provider or production acceptance.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migration:** no confidence-column range migration is expected; the
  canonical column remains `DOUBLE PRECISION` with its existing `0..1`
  constraint. Add an Alembic revision only if the implementation needs a new
  durable gate/audit column or a controlled, data-preserving metadata update.
  Never bulk-rewrite classifications or provider operation history. Any
  migration must be additive, downgrade-safe when populated, and verified on
  disposable PostgreSQL.
- **Durable semantics:** PostgreSQL remains authoritative. A confidence block
  may create/update the existing durable operation as a sanitized policy
  outcome, but it must not be confused with a provider Request. No Redis key
  may authorize a send or override the gate.
- **Compatibility:** preserve the IA four-field JSON response, stored/public
  confidence scale `0..1`, terminal-cycle semantics, identity/mapping
  preparation order, six multipart field names, endpoint, `prioridade=2`,
  claims/leases, one-cycle uniqueness, `SolID`, and conservative retry/
  reconciliation. The only provider payload value changed by this issue is
  `tipo`, from the old external value `E` to mandatory internal value `I`.
- **Security/privacy:** logs and durable metadata may expose only safe cycle/
  operation IDs, normalized gate decision/category, scale, threshold, and
  numeric score where necessary. Never log or persist classification title,
  description, conversation text, contact values, provider body, raw payload,
  authorization header, token, or credentials.
- **Observability:** operators must distinguish `confidence_below_threshold`,
  `confidence_invalid`, ordinary Request failures, reconciliation-required
  operations, and completed internal Requests. The wording must not say that a
  Request was opened when the confidence gate blocked it.
- **Rollout:** this issue authorizes source/spec/test work only. It does not
  authorize live Acessórias POSTs, replay of old operations, conversion of
  historical external Requests, deployment, credential validation, or a
  production readiness claim. A separate approved runbook is required for any
  operational population affected by the contract change.

## Tests

- **Confidence policy:** unit tests prove the `0..1` to `0..10` normalization,
  exact `5.0` acceptance, `<5.0` rejection, and fail-closed invalid values.
- **Durable gate:** PostgreSQL tests prove blocked operations are sanitized,
  preserve classification, have no provider call, no `post_started_at`, no
  `SolID`, and no automatic retry; allowed operations retain the existing
  one-operation/one-provider-call behavior.
- **Pre-send race boundary:** tests prove the final confidence check occurs
  after payload validation but before `post_started_at` and HTTP admission,
  including replay and concurrent execution.
- **Internal provider contract:** tests prove exactly six multipart fields,
  `tipo=I`, `prioridade=2`, unchanged endpoint/authentication, and no caller
  path can send `tipo=E`.
- **Historical safety:** tests prove completed old operations are no-op,
  uncertain/post-start operations remain reconciliation-required, no provider
  update is attempted, and an independently safe retry uses `tipo=I`.
- **Regression/security:** retain identity/mapping preparation ordering,
  transport/retry/`429` reconciliation, no raw classification/provider-secret
  leakage, compile/type checks, and disposable PostgreSQL migration evidence.

Required validation commands:

- `PYTHONPATH=/app python -m pytest -q tests/test_ia_worker_intent.py tests/test_acessorias_request_provider.py tests/test_acessorias_requests.py tests/test_acessorias_preparation.py tests/test_postgres_evolution.py`
- `APP_TIMEZONE=UTC PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`
- `python -m compileall -q src tests alembic scripts`
- `npx --yes pyright`
- `PYTHONPATH=/app python scripts/verify.py` when disposable PostgreSQL and
  Docker prerequisites are available; report unavailable prerequisites
  separately from local/disposable passes and skips.
- `git diff --check`
- `graphify update .` when application code changes.

## Acceptance Criteria

- [x] Every automatic Acessórias Request evaluates the persisted source
  classification confidence before any provider call; the final guard occurs
  before `post_started_at`.
- [x] The confidence policy is explicit and centralized: persisted `0..1`,
  business `0..10`, `confidence * 10`, minimum `5.0`, equivalent persisted
  threshold `0.50`; exactly `0.50` is allowed and any value below it is
  blocked.
- [x] `NULL`, malformed, non-finite, negative, and greater-than-`1`
  confidence values fail closed with a sanitized durable category.
- [x] A below-threshold/invalid operation has no provider call,
  `post_started_at`, `SolID`, provider attempt, or automatic retry, and it is
  distinguishable from an externally opened Request without changing the
  originating classification.
- [x] The exact same confidence gate applies to normal creation, replay,
  concurrency, and any independently safe pre-send retry; no durable existing
  operation bypasses it.
- [x] The provider sends exactly the approved six multipart fields with
  `tipo=I` for all future automatic openings; `tipo=E` cannot be selected by a
  caller, configuration, fallback, or retry.
- [x] Payload metadata and fingerprints match `tipo=I` for safe future
  attempts; completed, uncertain, reconciled, and post-start historical
  evidence is not rewritten and no provider update/conversion is attempted.
- [x] Existing priority, endpoint, authentication, rate admission,
  `SolID`, claim/lease, one-cycle uniqueness, retry, and reconciliation
  semantics remain unchanged except for the confidence gate and internal type.
- [x] IA parsing, persistence, and public result contracts remain on the
  existing `0..1` confidence scale; no model re-run or classification rewrite
  is introduced.
- [x] Updated SPEC-0011, specs index, PRD, ARCHITECTURE, IMPLEMENTATION_PLAN,
  and README/source references consistently describe the internal `tipo=I`
  Request and normalized confidence policy, without rewriting closed issue
  history.
- [x] Focused tests, offline tests, compileall, strict Pyright,
  `git diff --check`, and disposable PostgreSQL verification pass when their
  prerequisites are available; evidence for local/doubles/provider/production
  remains separated.
- [x] No secrets, tokens, authorization headers, raw provider bodies,
  classification content, conversation content, PII, or raw payloads appear in
  logs, durable gate metadata, fixtures, or exceptions.

## References

- **Primary Request contract to amend:**
  `specs/0011-durable-acessorias-request-creation.md`, especially the
  six-field payload, Request eligibility, pre-POST boundary, and
  retry/reconciliation rules. Its previous `tipo=E` statements are superseded
  for future automatic openings and were updated in this implementation.
- **IA contract to preserve:**
  `specs/0001-shared-data-and-analysis-contract.md`,
  `src/core/ia_classification.py`, and the `ia_classifications.confidence`
  `DOUBLE PRECISION`/`0..1` check.
- **Product and architecture:** `PRD.md` §§4, 5.5, 6, and 8;
  `ARCHITECTURE.md` §§2.1, 5, 12, and 14;
  `IMPLEMENTATION_PLAN.md` Phase 4 broader IA-policy decision and completed
  Milestone E traceability.
- **Current provider boundary:**
  `src/core/acessorias_request_provider.py` (`REQUEST_TYPE`,
  `AcessoriasRequestPayload.form`, and `create_request`).
- **Current durable boundary:**
  `src/core/acessorias_requests.py` (`_operation_state`,
  `_ensure_operation_sync`, `_mark_post_started_sync`, and
  `create_request_for_cycle`).
- **Preparation and worker:** `src/core/acessorias_preparation.py`,
  `src/workers/ia_worker.py`, and issues `0026`/`0035`/`0036`.
- **Prior Request safety issues:** `0017`–`0019`, `0021`–`0022`, and `0026`.

---

## Resolution

Implemented and closed on 2026-08-26.

- Added the centralized `evaluate_request_confidence()` policy in
  `src/core/acessorias_requests.py`: persisted `0..1` is normalized to the
  business `0..10` scale, `5.0`/`0.50` is the inclusive threshold, and invalid,
  non-finite, out-of-range, or lower values fail closed.
- Enforced the policy during durable operation eligibility and again under the
  operation lock immediately before `post_started_at`. Blocked operations are
  sanitized `definitive_failure` records with no attempt, marker, `SolID`,
  provider call, or automatic retry; the classification remains unchanged.
- Changed the provider boundary to the single internal `REQUEST_TYPE = "I"`.
  Safe future retries use the internal payload, while completed, uncertain,
  reconciled, and post-start historical evidence is not rewritten or converted.
- Added unit, provider, and disposable PostgreSQL coverage for threshold
  boundaries, invalid confidence, replay, final pre-send blocking, metadata,
  and the internal multipart contract.
- Updated SPEC-0011, the specs index, PRD, ARCHITECTURE, IMPLEMENTATION_PLAN,
  and README. No migration was needed because the existing confidence column
  and durable JSONB metadata support the policy. Graphify was refreshed after
  the source changes.

Validation passed: compileall, strict Pyright, offline pytest **269 passed,
82 skipped**, Alembic head `0023_manual_reconciliation`, and disposable
PostgreSQL pytest **82 passed, 269 deselected**. The evidence is local and
disposable; it does not claim live provider, credentials, deployment, or
production acceptance.
