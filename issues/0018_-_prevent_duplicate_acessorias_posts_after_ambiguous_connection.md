---
id: 0018
title: "Prevent duplicate Acessórias Requests after ambiguous connection failures"
type: bug
status: closed
priority: critical
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

The Acessórias Request adapter can issue a second external `POST /requests`
after a transport failure whose processing outcome is unknown. This violates
the durable Request contract and can create duplicate customer Requests,
because the provider endpoint has no documented idempotency key.

**Root cause:** `AcessoriasRequestAdapter.create_request()` catches every
`requests.ConnectionError` as `pre_send_connection` and retries while attempts
remain (`src/core/acessorias_requests.py:295-308`). The local Requests transport
maps protocol/socket failures from `conn.urlopen()` to `ConnectionError`, and
such a failure can occur after the request body has been sent. The adapter has
no evidence at this boundary that the POST was not processed.

**Reproduction:**

1. Build any valid Request payload and configure the adapter with
   `max_attempts=2`.
2. Use a deterministic session double whose first `post()` records the outgoing
   call and then raises `requests.ConnectionError`, representing a peer
   disconnect after request bytes were sent; return `{\"id\": \"SOL-2\"}` on
   the second call.
3. Call `create_request()`.
4. Observe two calls to `post()` and a `completed/provider_success` result. The
   current focused suite passes because its connection-error case does not
   distinguish an explicitly proven pre-send failure from an ambiguous one.

**Actual behaviour:** an ambiguous `ConnectionError` is retried automatically;
if the first POST was accepted, the second POST may create another Request.
The durable orchestration then accepts the later `id` as the operation's
`SolID`, with no durable record that a duplicate may already exist.

**Expected behaviour:** automatic retry is allowed only when the provider
boundary has strong evidence that the POST could not have started. An
ambiguous connection/protocol failure after or during transmission must produce
`reconciliation_required`, release no retryable path, issue no second POST, and
remain available for `manual_db` reconciliation. A completed operation remains
an idempotent no-op.

## Implementation Plan

1. Reconfirm the Request outcome matrix in SPEC-0011 v1.1 and preserve the
   provider boundary's sanitized error categories. Keep the existing durable
   one-operation-per-cycle and manual reconciliation model; do not add a
   provider idempotency header or infer success from a message, status alone,
   subject, or timing.
2. Separate an explicitly proven pre-send transport failure from an ambiguous
   `requests.ConnectionError`/protocol failure. Only the former may enter the
   bounded retry path. Treat the latter as `reconciliation_required` and clear
   any automatic retry route before returning the outcome to the durable
   operation.
3. Preserve the existing behavior for definitive provider validation,
   authentication/permission, and successful non-empty `id` responses. Ensure
   provider exceptions remain sanitized and cannot expose the token, headers,
   multipart body, raw provider response, or classification content.
4. Add regression coverage for a connection failure after the session double
   has recorded the POST: assert one call, `reconciliation_required`, no
   `SolID`, and no automatic second attempt. Retain coverage for a genuinely
   safe pre-send failure and prove its bounded retry still succeeds without
   changing the durable state or payload contract.
5. Exercise the durable orchestration with an uncertain transport outcome and
   verify that replay does not call the provider again, manual reconciliation
   can record a verified `SolID`, and release is possible only after explicit
   proof of remote absence. Do not alter unrelated cycle, identity, mapping,
   or Request-lifecycle behavior.

## Tests

- **Unit:** `tests/test_acessorias_requests.py` — ambiguous connection after
  transmission, explicitly safe pre-send failure, bounded attempts, sanitized
  outcome, and exact call count.
- **PostgreSQL:** the Request-operation tests in
  `tests/test_acessorias_requests.py` — uncertain outcome persistence, replay
  no-op, `SolID` reconciliation, and proof-gated retry release.

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

- [x] A transport failure with no evidence that the Request POST was prevented
  from starting results in `reconciliation_required` and never causes a second
  provider POST.
- [x] A failure explicitly proven to occur before transmission retains bounded,
  rate-aware retry behavior and can complete exactly once when a later attempt
  returns a non-empty provider `id`.
- [x] An uncertain operation remains durably visible with a sanitized category,
  no `SolID`, and no automatic retry path; replay and concurrent execution do
  not issue another POST.
- [x] `manual_db` reconciliation can record a verified remote `SolID`, while
  retry release remains rejected unless proof of remote absence is supplied;
  repeated reconciliation keys remain replay/conflict-safe.
- [x] The originating classification, cycle, identity resolution, department
  mapping, payload field set, priority, Request type, and provider credential
  boundary remain unchanged.
- [x] Tests cover ambiguous disconnect, safe pre-send failure, provider
  success, definitive failure, sanitization, durable replay, concurrency, and
  data-integrity call counts.
- [x] Documentation and traceability are synchronized with the corrected
  Request retry/reconciliation behavior in SPEC-0011, its index, the
  implementation-derived README/PRD/architecture, and
  `IMPLEMENTATION_PLAN.md`.
- [x] Graphify metadata is updated according to the repository workflow after
  implementation, and the issue is closed only after validation and one
  focused commit.

## References

- Primary contract: `specs/0011-durable-acessorias-request-creation.md` v1.1,
  especially §§4–5 and the retry/reconciliation matrix.
- Cross-cutting contracts: `specs/0001-shared-data-and-analysis-contract.md`,
  `specs/0003-durable-finalization-and-media.md`, and
  `specs/0004-reproducible-verification-baseline.md`.
- Product and architecture: `PRD.md` §§5.5 and 8; `ARCHITECTURE.md` §§2.1 and
  12; `IMPLEMENTATION_PLAN.md`, Milestone E and its Request retry notes.
- Related implementation: `issues/0017_-_implement-durable-acessorias-request-creation.md`.
- Local evidence: `AcessoriasRequestAdapter.create_request()` currently retries
  all `requests.ConnectionError` values; a deterministic session double
  produced two POST calls and a completed result from one ambiguous failure.

---

## Resolution

Implemented the conservative Acessórias transport-outcome correction.

- Added the explicit `AcessoriasRequestPreSendError` boundary marker for the
  only connection failure eligible for bounded automatic retry. Ordinary
  `requests.ConnectionError`, timeout, and protocol failures now return a
  sanitized `reconciliation_required` outcome without a second POST.
- Preserved the existing multipart payload, credential boundary, rate limiter,
  backoff, durable one-operation-per-cycle claim/replay model, and
  `manual_db` reconciliation/release rules. No migration or provider
  idempotency key was added.
- Added unit coverage for explicit safe pre-send retry/success, ambiguous
  connection/timeout/protocol failures and exact provider call counts, plus
  PostgreSQL-marked replay/concurrency coverage for an uncertain operation.
- Updated SPEC-0011, `specs/README.md`, README, PRD, ARCHITECTURE, and
  `IMPLEMENTATION_PLAN.md` with the corrected retry matrix and current local
  evidence. Ran `graphify update .`.

Validation:

- `PYTHONPATH=/app python -m pytest -q tests/test_acessorias_requests.py` — **10 passed, 5 skipped**.
- `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py` — **192 passed, 61 skipped**.
- `python -m compileall -q src tests alembic scripts` — passed.
- `npx --yes pyright` — **0 errors, 0 warnings, 0 informations**.
- `PYTHONPATH=/app python scripts/verify.py` — compileall, Pyright, offline pytest, disposable PostgreSQL/Alembic `0019`, and PostgreSQL pytest all passed; PostgreSQL stage **61 passed, 192 deselected**.
- `git diff --check` — passed.
