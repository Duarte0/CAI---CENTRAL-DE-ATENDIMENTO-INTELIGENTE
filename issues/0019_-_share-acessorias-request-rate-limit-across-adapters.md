---
id: 0019
title: "Enforce the shared Acessórias Request rate limit across adapter instances"
type: bug
status: closed
priority: high
phase: 4
created_at: 2026-08-17
updated_at: 2026-08-17
closed_at: 2026-08-17
related_issues:
  - "0017"
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

The Acessórias Request provider boundary does not enforce the configured
sliding-window limit across the adapter instances used by the automatic Request
path. Each operation creates a fresh adapter, so each adapter starts with an
empty limiter and successive Requests can exceed the provider's documented
limit without waiting.

**Root cause:** `_RateLimiter` keeps its request timestamps in an instance-local
`deque` (`src/core/acessorias_directory.py:228-253`).
`AcessoriasRequestAdapter.__init__` creates a new limiter for every adapter
(`src/core/acessorias_requests.py:250-256`), and the default orchestration path
constructs a new adapter for each operation
(`src/core/acessorias_requests.py:724-729`). No shared registry or coordination
state connects those limiters.

**Reproduction:**

1. Configure two `AcessoriasRequestAdapter` instances with the same provider
   endpoint and `rate_limit_per_minute=1`, using a deterministic session,
   clock, and sleep recorder.
2. Send one valid payload through the first adapter and then the same payload
   through the second adapter at the same clock value.
3. Observe two provider POSTs and no recorded sleep. The same behavior occurs
   in the default orchestration path because it constructs a new adapter for
   each Request operation.

**Actual behaviour:** the two calls complete immediately even though the
configured limit allows only one Request per minute. The focused provider and
directory tests pass because their rate assertions exercise one adapter
instance at a time; the full offline suite currently reports 189 passed and 60
skipped, and does not cover cross-instance throttling.

**Expected behaviour:** all automatic Acessórias Request POSTs covered by the
configured provider boundary share one sliding-window limiter at the active
worker/coordination scope. Calls that would exceed the limit wait before the
POST, while `Retry-After` handling and bounded retry behavior remain intact.
The limiter must not be reset merely because a durable Request operation creates
another adapter instance.

## Implementation Plan

1. Reconfirm SPEC-0011 v1.1 §3.8 and the Milestone E rate-limit contract. Define
   the limiter scope for the current worker topology and preserve the existing
   provider endpoint, multipart fields, credential boundary, retry categories,
   and durable Request state machine.
2. Replace the per-adapter Request limiter state with a shared, concurrency-safe
   sliding-window coordination path used by the default Request adapters. It
   must be keyed so unrelated provider endpoints/configurations do not consume
   one another's budget, and it must preserve the configured upper bound.
3. Keep rate-limit coordination separate from Request payloads and durable
   operation data. Do not add provider idempotency fields, persist tokens or
   raw payloads, or change `completed`, `retryable_failure`, or
   `reconciliation_required` semantics. Preserve provider `Retry-After` and
   bounded backoff behavior after the shared limiter admits an attempt.
4. Add deterministic regression coverage with multiple adapter instances and a
   shared fake clock/session: prove that the limit is enforced across instances,
   that the second call waits before POST, and that calls after the window do
   not wait unnecessarily. Add a concurrent-call case for the shared state and
   retain existing single-adapter, `Retry-After`, retry, and uncertainty tests.
5. Run focused and repository verification, then synchronize only the
   implementation-derived documentation and traceability required by the
   corrected rate-limit claim. Update Graphify metadata after implementation
   and close this issue only after one focused commit.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migration:** no Acessórias Request schema, classification, mapping,
  reconciliation, or migration semantics should change. Limiter state is
  coordination metadata only; if the established coordination boundary needs
  storage, it must not contain tokens, headers, payload bodies, or
  classification content.
- **Compatibility:** preserve the six-field multipart payload, `prioridade=2`,
  `tipo=E`, provider URL, and all existing durable operation outcomes. A
  throttled call must remain a single provider attempt and must not create a
  second Request.
- **Concurrency/integrity:** concurrent adapters must serialize admission to
  the shared window so the configured limit is not exceeded due to races.
  Durable one-operation-per-cycle and conservative uncertainty handling remain
  authoritative; throttling must not turn an uncertain POST into an automatic
  retry.
- **Security/privacy:** never include the Bearer token, headers, multipart
  values, title, description, PII, or raw provider response in limiter keys,
  state, logs, metrics, or exceptions. Only safe endpoint/configuration
  identity, counts, timestamps, and bounded wait/outcome metadata may be
  observable.
- **Rollout:** local deterministic tests, the offline suite, compileall, and
  Pyright are the acceptance boundary. They do not prove the live provider's
  quota or production multi-process topology; report those external limits
  separately.

## Tests

- **Unit:** `tests/test_acessorias_requests.py` — multiple adapter instances,
  shared sliding-window admission, concurrent calls, window expiry,
  `Retry-After`, bounded retries, and unchanged uncertain-outcome behavior.
- **Offline suite:**
  `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`
- **Static/repository validation:**
  `python -m compileall -q src tests alembic scripts`, `npx --yes pyright`,
  and `git diff --check`.
- **Canonical runner:**
  `PYTHONPATH=/app python scripts/verify.py` when disposable PostgreSQL and
  Docker prerequisites are available; report unavailable external
  prerequisites separately.

## Acceptance Criteria

- [x] Two automatic Request adapter instances using the same configured
  Acessórias provider cannot issue more than the configured number of POSTs in
  any sliding one-minute window.
- [x] Shared limiter admission is concurrency-safe; simultaneous calls cannot
  bypass the limit, and the admitted call occurs only after the required wait.
- [x] A call made after the configured window has expired is admitted without
  an unnecessary additional wait, and independent provider configurations do
  not share budget.
- [x] Existing `Retry-After` parsing, bounded backoff, safe retry eligibility,
  and post-send uncertainty classification remain unchanged and are covered by
  regression tests.
- [x] The six approved fields, `prioridade=2`, `tipo=E`, Bearer boundary,
  payload fingerprinting, durable one-operation-per-cycle rule, and
  reconciliation behavior remain unchanged.
- [x] Limiter state, logs, metrics, and exceptions contain no token, header,
  raw payload/provider body, classification content, or PII.
- [x] Focused tests, the applicable offline suite, compileall, Pyright, and
  `git diff --check` pass; disposable PostgreSQL evidence is reported when
  available.
- [x] README, PRD, ARCHITECTURE, SPEC-0011/specs index, and
  `IMPLEMENTATION_PLAN.md` remain consistent with the corrected rate-limit
  scope; Graphify metadata is updated according to repository workflow.
- [x] The issue is closed only after validation and one focused commit.

## References

- **Primary contract:** `specs/0011-durable-acessorias-request-creation.md`
  v1.1, §3.8 and §§5.3-5.4; it requires a conservative shared 100
  requests/minute Sliding Window limit and bounded `Retry-After` handling.
- **Plan:** `IMPLEMENTATION_PLAN.md`, Milestone E — Durable Acessórias Request
  Creation, especially the provider rate-limit and conservative retry outcome.
- **Product:** `PRD.md` §§5.5 and 8; PostgreSQL remains durable Request truth
  and the external Request boundary must be auditable and conservative.
- **Architecture:** `ARCHITECTURE.md` §§2.1 and 12; the Request provider
  boundary and uncertain-outcome invariant must remain intact.
- **Related implementation:**
  `issues/0017_-_implement-durable-acessorias-request-creation.md`.
- **Non-duplicate related bug:**
  `issues/0018_-_prevent_duplicate_acessorias_posts_after_ambiguous_connection.md`
  covers ambiguous transport retries; this issue covers rate-limit state being
  reset across adapter instances.
- **Verified evidence:** before the fix, a deterministic two-adapter run
  produced two immediate POSTs with `rate_limit_per_minute=1` and no sleep.
  After the fix, focused Request tests passed **12 passed, 5 skipped**; the
  canonical runner passed compileall, strict Pyright, offline pytest (**197
  passed, 61 skipped**), Alembic `0019_acessorias_request_creation`, and
  PostgreSQL pytest (**61 passed, 197 deselected**).

---

## Resolution

<!-- Filled by the agent on close. DO NOT edit manually. -->

Implemented and closed issue 0019.

- Replaced the per-adapter Request limiter state with a concurrency-safe shared
  in-process Sliding Window registry keyed by provider endpoint and configured
  rate. Directory adapters retain their existing local limiter scope.
- Kept admission before every provider POST, including bounded retries, and
  preserved `Retry-After`, six-field multipart payload, Bearer boundary,
  payload fingerprint, durable one-operation-per-cycle state, and uncertain
  outcome reconciliation. No migration, provider idempotency field, token,
  header, payload, or PII was added to limiter state.
- Added deterministic unit coverage for cross-instance throttling, window
  expiry, independent endpoint budgets, and concurrent calls.
- Synchronized SPEC-0011, the specs index, README, PRD, ARCHITECTURE, and
  IMPLEMENTATION_PLAN.md; refreshed Graphify metadata.

Validation:

- `PYTHONPATH=/app python -m pytest -q tests/test_acessorias_requests.py` — **12 passed, 5 skipped**.
- `PYTHONPATH=/app python -m pytest -q tests/test_acessorias_requests.py tests/test_acessorias_directory.py` — **33 passed, 8 skipped**.
- `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py` — **197 passed, 61 skipped**.
- `python -m compileall -q src tests alembic scripts` — passed.
- `npx --yes pyright` — **0 errors, 0 warnings, 0 informations**.
- `PYTHONPATH=/app python scripts/verify.py` — all stages passed; PostgreSQL stage **61 passed, 197 deselected**.
- `git diff --check` on issue files — passed.
- `graphify update .` — passed; graph rebuilt with **1,852 nodes and 3,966 edges**.
