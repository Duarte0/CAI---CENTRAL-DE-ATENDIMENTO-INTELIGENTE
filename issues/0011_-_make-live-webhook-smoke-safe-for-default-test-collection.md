---
id: 0011
title: "Make the live webhook smoke check safe for default test collection"
type: refactor
status: closed
priority: medium
phase: 3
created_at: 2026-08-14
updated_at: 2026-08-14
closed_at: 2026-08-14
related_issues:
  - "0001"
  - "0002"
  - "0004"
blocked_by: []
affects:
  - tests/test_webhook_local.py
  - tests/test_webhook_local_boundary.py
  - scripts/verify.py
  - README.md
  - IMPLEMENTATION_PLAN.md
---

## Description

Complete the P2 test-hygiene follow-up in `IMPLEMENTATION_PLAN.md`: keep the
local live-webhook check available as an explicitly invoked smoke check while
making ordinary pytest collection and the canonical verification runner
network-independent.

**Verified gap:** `tests/test_webhook_local.py` is discovered by pytest's
`test_*.py` pattern, but its module body immediately calls
`requests.post("http://localhost:8000/webhook/digisac", ...)` and parses the
response. A bare pytest invocation therefore attempts a local HTTP request
during collection when no API was deliberately started. The canonical runner
currently avoids this only with `--ignore=tests/test_webhook_local.py`; the
repository's default test entrypoint remains unsafe, and the smoke command and
its prerequisites are not documented as a standalone invocation.

Expected outcome: importing or collecting the live smoke check performs no
network I/O, an operator can still run it deliberately against a started local
API, and the offline suite plus `scripts/verify.py` retain their existing
selection, disposable-PostgreSQL, and reporting boundaries.

## Scope

### In scope

- Convert the live webhook check into an explicit smoke entrypoint whose HTTP
  request runs only from deliberate execution, using the repository-native
  opt-in approach permitted by SPEC-0004 v1.5. A relocation out of pytest's
  test discovery path is preferred if it keeps the invocation clear; otherwise
  a guarded opt-in module is acceptable.
- Preserve the existing synthetic payload, local URL, response/status
  reporting, and the fact that this check is not part of the canonical test
  matrix.
- Add focused regression coverage or collection checks proving that default
  pytest collection does not contact `localhost:8000`, while the explicit
  smoke invocation remains available and fails visibly when its local API
  prerequisite is absent.
- Update the versioned README command/prerequisite text and any runner
  exclusion that becomes stale after the selected entrypoint change. Keep
  offline, PostgreSQL, and live-smoke results described as separate classes of
  evidence.
- Synchronize `IMPLEMENTATION_PLAN.md` and Graphify metadata on closure, and
  close the issue only in one focused commit.

### Out of scope

- Any production API, webhook handler, authentication/HMAC behavior, worker,
  queue, Redis, PostgreSQL, migration, fixture, configuration, or deployment
  change.
- Making the smoke check canonical, adding a provider-backed acceptance test,
  starting services automatically, adding CI/network requirements, or changing
  the canonical `scripts/verify.py` disposable-runner target.
- Changing the webhook payload contract, retry policy, idempotency semantics,
  response handling policy, timeout policy, or API error behavior beyond
  preventing import-time execution.
- Rewriting SPEC-0004 or the existing closed issue records; the specification
  already defines the opt-in boundary and required verification classes.

## Implementation Plan

1. Confirm the current pytest discovery behavior, the local smoke payload and
   URL, all README/runner references, and the SPEC-0004 v1.5 requirement that
   the live check remain excluded unless a local API is deliberately running.
   Preserve the existing synthetic data and do not introduce credentials,
   customer data, or a new endpoint.
2. Establish one explicit execution boundary for the smoke check. Prefer a
   clearly named script outside pytest discovery with a `main()` guard; if the
   existing path is retained, move every request and response parse behind the
   guard and document the direct command. In either form, module import and
   `pytest --collect-only` must be side-effect free, while deliberate execution
   must issue one request and report non-success responses instead of silently
   turning them into canonical test evidence.
3. Keep the canonical runner's offline and PostgreSQL selections unchanged
   except for removing an exclusion that is provably stale after relocation.
   It must never execute the smoke entrypoint, inherit a live API URL, or make a
   network request as part of collection. Do not add a fixture that starts an
   API or changes the disposable database boundary.
4. Add a focused regression check for the import/collection boundary and the
   explicit command contract. Cover the negative case where no local API is
   running without treating the resulting connection failure as a canonical
   suite failure; retain the existing visible failure/reporting behavior for an
   intentionally invoked smoke check.
5. Update README validation instructions with the exact opt-in command, local
   API prerequisite, expected failure when that prerequisite is absent, and
   explicit exclusion from the canonical matrix. Run the default collection,
   offline suite, applicable static checks, canonical runner when its Docker
   prerequisite is available, `git diff --check`, and `graphify update .`; then
   synchronize the plan status and exact evidence before closing in one focused
   commit.

## Data, compatibility, security, observability, and rollout

- **Data/migrations:** none. The smoke check may reach only the deliberately
  started local API; no production or developer database, Redis state, schema,
  or persisted record may be changed by collection or canonical verification.
- **Compatibility:** preserve the webhook URL, synthetic request shape,
  response/status output, current HMAC/API behavior, and the canonical runner's
  offline/PostgreSQL test selection. The live check remains opt-in and is not a
  pytest or CI acceptance guarantee.
- **Failure/retry/concurrency/idempotency:** the issue must not add retries,
  concurrency, queue publication, idempotency, or automatic recovery. A
  missing API, connection error, timeout, or non-success response must remain
  visible only for an explicitly invoked smoke check and must not run during
  collection or alter durable application state through new code.
- **Security/configuration:** use only the existing local URL and synthetic
  payload. Do not add secrets, authorization bypasses, raw customer payloads,
  production URLs, environment defaults, or automatic service startup.
- **Observability:** preserve concise status/response reporting for deliberate
  smoke execution, avoid printing credentials or raw sensitive webhook data,
  and keep live-smoke output distinct from offline and disposable-PostgreSQL
  evidence.
- **Rollout:** documentation and test-entrypoint change only; no service
  restart, migration, provider call, Redis operation, or production rollout is
  authorized.

## Tests

- **Collection safety:** `PYTHONPATH=/app python -m pytest --collect-only -q`
  and a default `PYTHONPATH=/app python -m pytest -q` complete without an HTTP
  request to `localhost:8000` and without requiring a local API.
- **Focused smoke boundary:** import/collection regression coverage proves
  the module or relocated script has no request-time side effect; an explicit
  `PYTHONPATH=/app python ...` smoke invocation remains documented and reports
  the expected connection failure when no local API is running.
- **Offline suite:** `PYTHONPATH=/app python -m pytest -q`.
- **Static/repository validation:** `python -m compileall -q src tests alembic
  scripts`, `npx --yes pyright`, and `git diff --check`.
- **Canonical runner:** `PYTHONPATH=/app python scripts/verify.py` when its
  disposable-PostgreSQL/Docker prerequisites are available; record offline,
  PostgreSQL, and unavailable stages separately and confirm no live smoke
  request occurred.
- **Graph/documentation:** `graphify update .`; verify the README, plan,
  SPEC-0004 v1.5 reference, and runner command remain consistent.

## Acceptance Criteria

- [x] Default pytest collection imports or discovers the live smoke entrypoint
  without making an HTTP request, opening a socket, requiring `localhost:8000`,
  or depending on a local API.
- [x] A bare default pytest run completes its applicable offline tests without
  the live smoke check changing pass/skip results or causing collection
  failure; the canonical runner remains network-independent and preserves its
  disposable PostgreSQL boundary.
- [x] The live webhook check remains explicitly invokable against a deliberately
  started local API, sends the existing synthetic payload to the existing
  local endpoint, and reports status/response failures without silently
  converting them into canonical evidence.
- [x] Missing API, connection error, timeout, and non-success response paths are
  visible only during deliberate smoke execution; no new retry, queue,
  idempotency, concurrency, persistence, or recovery behavior is introduced.
- [x] No database, migration, Redis state, production service, provider
  credential, raw customer payload, production URL, or authentication/HMAC
  contract is added or changed.
- [x] README instructions state the explicit smoke command, local API
  prerequisite, expected result boundary, and its exclusion from the canonical
  offline/PostgreSQL matrix; stale runner/path references are corrected.
- [x] Focused collection/smoke checks, the applicable offline suite,
  compileall, strict Pyright, `git diff --check`, and the canonical runner when
  available pass with exact results recorded; unavailable Docker/runtime stages
  are labeled rather than claimed.
- [x] `graphify update .` passes, the focused diff contains only this issue's
  implementation and required documentation/plan synchronization, and no
  application behavior is altered.
- [x] `IMPLEMENTATION_PLAN.md` marks only the P2 live-webhook test-hygiene item
  complete and records the observed validation evidence without claiming live
  provider or production readiness.
- [x] The issue is closed only after implementation, tests, documentation,
  Graphify metadata, and plan synchronization are included in one focused
  commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — **Separate pending work**, P2
  “Make the live webhook check safe for default test collection”; see the
  test-entrypoint discrepancy under **Dependencies, risks, and recorded
  discrepancies**.
- Primary specification: `specs/0004-reproducible-verification-baseline.md`
  v1.5 — canonical offline/PostgreSQL matrix, live-smoke opt-in boundary, and
  documentation requirements.
- Related completed issues: `issues/0001_-_isolate-persistent-canonical-test-suite.md`,
  `issues/0002_-_establish-disposable-postgresql-verification-runner.md`, and
  `issues/0004_-_verify-durable-operational-recovery-on-runner.md`.
- Current evidence: `tests/test_webhook_local.py`,
  `tests/test_webhook_local_boundary.py`, `tests/conftest.py`, `scripts/verify.py`,
  `README.md`, and the current pytest discovery behavior.

---

## Resolution

Implemented the guarded opt-in live webhook smoke boundary without changing
the production API, webhook contract, persistence, Redis, migrations, or
deployment configuration:

- moved the request, payload construction, response parsing, and failure
  reporting in `tests/test_webhook_local.py` behind `main()` and the existing
  direct-execution guard;
- added `tests/test_webhook_local_boundary.py` coverage for import safety,
  payload/endpoint preservation, success output, connection/timeout failures,
  and non-success HTTP responses; and
- removed the stale live-test exclusions from both canonical pytest selections
  in `scripts/verify.py`, then documented the direct smoke command and its
  local-API prerequisite in `README.md`.

Validation:

- `PYTHONPATH=/app python -m pytest --collect-only -q` — **187 tests
  collected**, with no local HTTP request;
- `PYTHONPATH=/app python -m pytest -q` — **151 passed, 36 skipped**;
- `PYTHONPATH=/app python -m pytest -q tests/test_webhook_local_boundary.py
  tests/test_verification_runner.py` — **13 passed**;
- `PYTHONPATH=/app python tests/test_webhook_local.py` with no API — exited
  **1** and reported the connection failure visibly;
- `python -m compileall -q src tests alembic scripts` — passed;
- `npx --yes pyright` — **0 errors, 0 warnings, 0 informations**;
- `PYTHONPATH=/app python scripts/verify.py` — compileall, Pyright, offline
  pytest (**151 passed, 36 skipped**), disposable PostgreSQL 16 connectivity,
  Alembic `0015_acessorias_directory`, and PostgreSQL pytest (**36 passed,
  151 deselected**) all passed;
- `git diff --check` — passed; and
- `graphify update .` — passed.

Documentation and plan synchronization completed in `README.md`,
`specs/README.md`, `specs/0004-reproducible-verification-baseline.md`, and
`IMPLEMENTATION_PLAN.md`. SPEC-0004 remains v1.5; its existing opt-in
contract was confirmed and only implementation/evidence notes were refreshed.
