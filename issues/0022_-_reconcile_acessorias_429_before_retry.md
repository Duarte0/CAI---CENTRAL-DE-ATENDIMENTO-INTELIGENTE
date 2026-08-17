---
id: 0022
title: "Require proof before retrying Acessórias 429 responses"
type: bug
status: closed
priority: high
phase: 4
created_at: 2026-08-17
updated_at: 2026-08-17
closed_at: 2026-08-17
related_issues:
  - "0017"
  - "0018"
  - "0019"
blocked_by: []
affects:
  - src/core/acessorias_requests.py
  - tests/test_acessorias_requests.py
  - specs/0011-durable-acessorias-request-creation.md
  - README.md
  - PRD.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
---

## Description

The Acessórias Request adapter treats every HTTP `429` response as safe to
retry, although the approved durable Request contract permits a retry only
when the adapter can prove that the remote Request was not created. A `429`
response without such evidence can be returned after the provider has received
or processed the POST; retrying it can create a duplicate external Request and
the later response can overwrite the operation's only persisted `SolID`.

**Plan/spec references:** `IMPLEMENTATION_PLAN.md`, Approved Acessórias
milestones, item 5, Milestone E; SPEC-0011 v1.1 §§3.8, 5.3–5.7; PRD §5.5 and
§8; and ARCHITECTURE §§2.1 and 12. Issue 0017 records the same outcome matrix
as completed implementation evidence, but the current adapter does not meet
its `429` requirement.

**Dependencies:** the durable operation, claim/lease, and manual reconciliation
boundary from issue 0017; the ambiguous-transport safeguards in issue 0018;
and the shared limiter work in issue 0019. No migration, new provider
idempotency parameter, or product decision is required. If the provider has no
documented signal proving non-creation for this endpoint, the existing
contract's conservative reconciliation path is the available behavior.

**Root cause:** `AcessoriasRequestAdapter.create_request()` enters the shared
`status in {408, 425, 429}` branch at
`src/core/acessorias_requests.py:326-334`. For `429`, it sleeps and issues the
next POST whenever `attempt < max_attempts`, then returns
`retryable_failure/provider_rate_limit` after the final attempt. The branch
does not inspect any provider evidence that proves the first POST was rejected
before processing. The adapter has no documented idempotency key or safe
correlation query that could make a second POST harmless.

**Reproduction:**

1. Build a valid Request payload and configure
   `AcessoriasRequestAdapter(max_attempts=2)` with a deterministic session.
2. Make the first `post()` record the outgoing call and return HTTP `429`
   without a non-empty `id` (for example, `{"Erro": "rate limited"}`); make
   the second call return `{"id": "SOL-2"}`.
3. Call `create_request()`.
4. Observe two provider calls and a `completed/provider_success` outcome. With
   `max_attempts=1`, observe `retryable_failure/provider_rate_limit`, which
   permits the durable orchestration to post again later without proof that
   the first attempt was not processed.

The current deterministic probe produced
`{"state": "retryable_failure", "category": "provider_rate_limit", "calls": 1}`
for one `429` response; the existing focused tests exercise a `429` followed by
success and assert two calls. The focused directory/Request tests pass
(`28 passed, 7 deselected`) and the offline suite passes (`189 passed, 60
deselected`), but neither proves the required no-duplicate behavior for a
`429` whose processing outcome is unknown.

**Actual behaviour:** a status-only `429` is automatically retried and can
produce a second external POST; after the final response it remains
retryable, so a later durable replay can issue another POST as well.

**Expected behaviour:** a `429` may enter the bounded retry path only when an
already documented provider response or boundary fact proves that no Request
was created. Otherwise the adapter must return
`reconciliation_required`, preserve the sanitized outcome, issue no second
POST, and leave the operation for `manual_db` reconciliation. No status,
message, subject, timestamp, or guessed correlation may be treated as proof,
and no provider idempotency field may be invented.

## Scope

### In scope

- Correct the Acessórias Request outcome classification and retry boundary for
  `429` responses without proof of remote absence.
- Preserve the existing six-field multipart payload, Bearer boundary,
  `Retry-After` parsing, shared rate-limit coordination, durable one-operation
  constraint, claim/lease behavior, `SolID` persistence, and manual
  reconciliation/release guards.
- Add deterministic unit and PostgreSQL regression coverage proving one POST
  for an uncertain `429`, durable reconciliation, replay no-op, and any
  explicitly documented safe-`429` path if one is already supported by the
  provider contract.

### Out of scope

- Provider idempotency headers or parameters, subject/time correlation, blind
  retries, or live-provider assumptions not documented by the repository.
- The ambiguous `ConnectionError` classification in issue 0018, shared limiter
  state in issue 0019, payload-load ordering in issue 0021, department mapping,
  identity resolution, Request lifecycle operations, or classification changes.
- Migration redesign, new HTTP/admin endpoints, credential changes, or
  production synchronization.

## Implementation Plan

1. Reconfirm SPEC-0011's `429` and crash/reconciliation matrix and identify
   which response facts, if any, can prove non-creation without inventing
   provider behavior. Treat an unproven `429` conservatively.
2. Update the adapter/orchestration boundary so an unproven `429` cannot
   consume an automatic retry route or issue a second POST. If a documented
   proof path exists, keep it narrow, bounded, rate-aware, and explicit;
   preserve `Retry-After` only for an admitted safe retry.
3. Preserve durable operation state, attempt evidence, payload fingerprint and
   metadata, provider status, sanitized categories, claim ownership, manual
   `SolID` reconciliation, and proof-gated retry release. Do not clear or
   rewrite evidence from a POST that may already have reached Acessórias.
4. Add regression coverage for a `429` with no `id` and no proof: assert one
   POST, `reconciliation_required`, no automatic replay, and no duplicate
   `SolID`. Cover durable manual reconciliation and proof-of-absence release.
   Retain the existing successful response, `Retry-After`, shared limiter,
   ambiguous transport, definitive failure, and safe pre-send tests.
5. Run focused and canonical verification, then synchronize only the
   implementation-derived Request documentation, plan traceability, and
   Graphify metadata required by the corrected outcome contract.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migration:** no migration is expected. Existing operation rows,
  `post_started_at`, provider status, failure categories, reconciliation data,
  and `SolID` values remain readable; uncertain attempts must not be erased or
  silently released.
- **Compatibility:** preserve the approved endpoint, six multipart fields,
  `prioridade=2`, `tipo=E`, Bearer authentication, one operation per cycle,
  shared limiter, and all non-`429` outcome semantics unless the primary
  contract requires otherwise.
- **Integrity/concurrency:** a status-only `429` must not cause a second POST
  through in-call retry, later durable replay, or concurrent claims. Manual
  reconciliation remains the only path to record a remote `SolID` or release
  after proof of absence.
- **Security/privacy:** no token, header, raw payload/provider body, title,
  description, PII, or database error may enter logs or durable state. Only
  safe status, category, attempt, timestamp, fingerprint, and reconciliation
  metadata may be exposed.
- **Rollout:** focused tests, the offline suite, compileall, strict Pyright,
  `git diff --check`, and disposable PostgreSQL verification are the local
  acceptance boundary; they do not prove live-provider semantics or
  production quota behavior.

## Tests

- **Unit:** `tests/test_acessorias_requests.py` — unproven `429`, exact POST
  count, no automatic retry, sanitized outcome, `Retry-After` handling for
  any admitted safe path, and unchanged provider outcome cases.
- **PostgreSQL:** Request-operation tests in
  `tests/test_acessorias_requests.py` — uncertain `429` persistence, replay
  no-op, concurrent claim behavior, manual `SolID` reconciliation, and
  proof-gated release.

Required validation commands:

- `PYTHONPATH=/app python -m pytest -q tests/test_acessorias_requests.py`
- `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`
- `python -m compileall -q src tests alembic scripts`
- `npx --yes pyright`
- `PYTHONPATH=/app python scripts/verify.py` when disposable PostgreSQL and
  Docker prerequisites are available; report unavailable external
  prerequisites separately.
- `git diff --check`

## Acceptance Criteria

- [x] A `429` without an already documented proof of remote non-creation
  results in `reconciliation_required` and exactly one provider POST,
  regardless of `max_attempts`.
- [x] An uncertain `429` never becomes `retryable_failure` merely because the
  response status or `Retry-After` header was present, and a later replay or
  concurrent caller issues no second POST.
- [x] If an existing provider contract explicitly proves non-creation for a
  `429`, only that narrow case may retry within the configured bound, honors
  `Retry-After`/shared rate limiting, and still cannot create duplicate POSTs.
  N/A: the repository records no provider-supported non-creation proof for this
  endpoint, so no safe-`429` retry path exists to implement.
- [x] Manual reconciliation can record a verified `SolID`, while retry release
  remains rejected without explicit proof of remote absence; repeated keys are
  replay/conflict-safe.
- [x] Existing success-with-`id`, business/`Erro`, auth/permission,
  ambiguous-transport, timeout, `5xx`, safe pre-send, payload, and credential
  boundary behavior remains covered and unchanged.
- [x] No provider idempotency header/parameter, guessed correlation, token,
  header, raw body, PII, or unsanitized error is added to code, logs, or state.
- [x] Focused tests, applicable offline/PostgreSQL verification, compileall,
  strict Pyright, and `git diff --check` pass, with unavailable prerequisites
  reported separately from skips and passes.
- [x] SPEC-0011, its index, `README.md`, `PRD.md`, `ARCHITECTURE.md`, and
  `IMPLEMENTATION_PLAN.md` remain consistent with the corrected `429`
  outcome; Graphify metadata is updated according to repository workflow.
- [x] The issue is closed only after validation and one focused commit.

## References

- **Primary contract:** `specs/0011-durable-acessorias-request-creation.md`
  v1.3, §§3.8, 5.3–5.7, especially the rule that `429` is retryable only
  when non-creation is proven.
- **Plan:** `IMPLEMENTATION_PLAN.md`, Approved Acessórias milestones, item 5,
  Milestone E.
- **Product/architecture:** `PRD.md` §5.5 and §8; `ARCHITECTURE.md` §§2.1
  and 12.
- **Related implementations:**
  `issues/0017_-_implement-durable-acessorias-request-creation.md` and
  `src/core/acessorias_requests.py:348-359`.
- **Non-duplicates:** issue 0018 covers ambiguous connection/transport
  retries; issue 0019 covers limiter state shared across adapter instances;
  issue 0021 covers false post-start state before payload loading. This issue
  covers status-only `429` responses that are retried without proof of remote
  absence.

---

## Resolution

Implemented issue 0022 by separating HTTP `429` from the generic transient
branch in `AcessoriasRequestAdapter`. A status-only or `Retry-After`-only `429`
now returns sanitized `reconciliation_required/uncertain_rate_limit`, makes no
second POST, and preserves the existing explicit pre-send retry path for
provenably safe transport failures. No migration or provider idempotency
parameter was added.

Added unit coverage for exact POST count, status/category preservation, ignored
`Retry-After`, and unchanged safe transient/pre-send behavior. Added disposable
PostgreSQL coverage for durable `429` reconciliation, replay/concurrent no-op,
and manual `SolID` reconciliation.

Validation passed:

- `PYTHONPATH=/app python -m pytest -q tests/test_acessorias_requests.py` — 14 passed, 7 skipped;
- `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py` — 199 passed, 66 skipped;
- `python -m compileall -q src tests alembic scripts` — passed;
- `npx --yes pyright` — 0 errors, 0 warnings, 0 informations;
- `PYTHONPATH=/app python scripts/verify.py` — compileall, Pyright, offline 199/66, disposable PostgreSQL 16, Alembic head `0019_acessorias_request_creation`, and PostgreSQL 66 passed/199 deselected all passed;
- `git diff --check` — passed.

Synchronized SPEC-0011 v1.3, `specs/README.md`, README, PRD, architecture,
`IMPLEMENTATION_PLAN.md`, and Graphify metadata. The provider contract has no
documented proof of non-creation for `429`, so the safe-`429` retry criterion is
explicitly N/A and the conservative reconciliation path is canonical.
