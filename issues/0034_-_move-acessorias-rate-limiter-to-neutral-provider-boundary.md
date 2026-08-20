---
id: 0034
title: "Move shared Acessórias rate admission behind a neutral provider boundary"
type: refactor
status: closed
priority: medium
phase: 5
created_at: 2026-08-20
updated_at: 2026-08-20
closed_at: 2026-08-20
related_issues:
  - "0017"
  - "0019"
blocked_by: []
affects:
  - src/core/provider_coordination.py
  - src/core/acessorias_directory.py
  - src/core/acessorias_requests.py
  - src/core/
  - tests/test_provider_coordination.py
  - tests/test_acessorias_directory.py
  - tests/test_acessorias_requests.py
  - README.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
---

## Description

The Acessórias sliding-window primitive has the wrong module owner. The private
`_RateLimiter` and `_RateLimiterState` are defined in
`src/core/acessorias_directory.py:229-274`, where the Directory adapter uses an
instance-local limiter at `:339-347`. The Request provider imports that private
symbol across the domain boundary from `src/core/acessorias_requests.py:28` and
constructs it at `:281-286` for the issue-0019 shared Request admission path.

This leaves Request delivery coupled to an implementation detail of directory
synchronization: a change to the Directory module can silently alter Request
admission, and the generic coordination primitive cannot be isolated without
loading an unrelated provider adapter module. Graphify confirms the direct
relationship `_RateLimiter <-imports- acessorias_requests.py ->
AcessoriasRequestAdapter`; direct source inspection confirms there are only the
two adapter call sites.

Issue `0019` already corrected the behavior that Request adapter instances must
share a concurrency-safe in-process Sliding Window. This issue is the remaining
structural cleanup: relocate that already-approved primitive behind one neutral
internal provider-coordination boundary while preserving every current rate,
key, locking, sleep, expiry, retry, and privacy invariant.

## Target boundary and expected outcome

Create one neutral internal provider-coordination boundary under `src/core/`
that owns the generic in-process sliding-window state and admission operation.
It must have no dependency on HTTP clients, Acessórias domain records,
PostgreSQL, Redis, Groq, configuration, credentials, or request payloads.

`AcessoriasDirectoryAdapter` and `AcessoriasRequestAdapter` should depend on
that boundary directly. The Directory adapter must retain its current
instance-local limiter scope. Request adapters must retain the issue-0019
shared registry keyed by the sanitized provider endpoint and configured limit.
The provider-specific adapters, their public classes and aliases, and all
existing callers remain in place.

No material architecture decision is required: the approved architecture
already gives each Acessórias provider boundary its own adapter and defines
rate admission as transient in-process coordination. This issue does not
change that contract or introduce a new application layer.

## Scope

### In scope

- Extract the sliding-window state, lock, validation, and admission operation
  from the Directory module into the neutral internal boundary.
- Update both Acessórias adapters to import the neutral boundary rather than a
  private symbol from one another's domain module.
- Preserve the current Request endpoint/configuration key construction and the
  Directory adapter's default non-shared scope.
- Preserve the existing focused test seams and add only structural or
  regression assertions needed to prove the dependency direction and exact
  behavior equivalence.
- Synchronize implementation-derived source ownership/documentation only if
  the new boundary changes the relevant source map; verify the active plan and
  contracts remain consistent, and refresh Graphify after implementation.

### Out of scope

- Changing the configured limit, 60-second window, endpoint isolation, process
  scope, lock strategy, sleep behavior, retry/backoff, `Retry-After` handling,
  or the timing of admission before provider calls.
- Changing any Request outcome, including `completed`,
  `retryable_failure`, `definitive_failure`, or
  `reconciliation_required`, and any pre-send/ambiguous transport semantics.
- Changing Directory authentication, endpoints, pagination, parsing,
  snapshot validation, PostgreSQL publication, advisory locking, sync state,
  or failure preservation.
- Moving or redesigning Acessórias Request persistence/orchestration, the
  Directory repository, Groq retry helpers, or any other provider adapter.
- Adding configuration, durable coordination, a provider idempotency key,
  public API/CLI surface, dependency, migration, Redis state, logs, metrics,
  or credentials/PII to limiter keys or state.
- Reopening or duplicating issue `0019`; its shared Request-admission behavior
  is a completed invariant for this structural move.

## Implementation Plan

1. Inventory all current limiter references, constructor arguments, key
   prefixes, adapter test doubles, and import seams. Treat the `1..100`
   validation range, `shared_key=None` Directory behavior, Request key format,
   and the existing injectable `sleep`/`clock` callbacks as compatibility
   contracts.
2. Introduce the neutral internal boundary with the current state registry,
   per-state lock, 60-second sliding-window eviction, bounded validation, and
   `before_request()` admission semantics. Keep the primitive independent of
   HTTP, provider payloads, secrets, database state, and application settings.
3. Migrate the Directory and Request adapters atomically to the neutral
   boundary. Preserve the Directory's separate limiter instances and preserve
   Request sharing only for identical sanitized endpoint/configuration keys;
   do not accidentally make Directory and Request budgets share state.
4. Remove the cross-domain private import and the old Directory-owned helper
   after all confirmed references move. Keep the adapter constructors,
   `AcessoriasDirectoryAdapter`, `AcessoriasRequestAdapter`, `AcessoriasClient`,
   `create_request()`, and all public/domain call signatures unchanged.
5. Run focused behavioral and static validation. Only after it passes, update
   implementation-derived source-map/ownership text if needed, confirm
   SPEC-0011 and the Acessórias documentation still describe the same
   behavior, run `graphify update .`, and close the issue with one focused
   commit.

## Invariants

- Request adapters with the same sanitized provider endpoint and configured
  rate continue to serialize admission through one in-process Sliding Window;
  endpoint/configuration keys remain isolated from one another.
- Directory adapters retain their current instance-local admission scope, and
  the Directory and Request adapters do not begin sharing a budget merely
  because they use the same neutral primitive.
- The limit remains validated in the current range, timestamps are evicted on
  the same 60-second boundary, concurrent callers use the same lock-protected
  admission, and injected clocks/sleep callbacks retain their current test
  behavior.
- Admission remains before every Directory GET and Request POST attempt,
  including the existing bounded pre-send retry path; no retry is added,
  removed, or reordered.
- Bearer authentication, multipart fields, Request subject/description,
  `tipo=E`, `prioridade=2`, payload fingerprints, provider response parsing,
  `SolID` confirmation, uncertain-outcome reconciliation, and durable
  one-operation-per-cycle semantics remain unchanged.
- No public route, response, event, CLI interface, database schema, persisted
  state, authorization/security policy, retention policy, or compatibility
  surface changes.
- Limiter state and derived keys contain only the existing safe endpoint/rate
  identity and timestamps. They never contain tokens, headers, payloads,
  classification content, contact values, PII, or raw provider responses.

## Tests

- **Focused adapters:**
  `PYTHONPATH=/app python -m pytest -q tests/test_acessorias_requests.py tests/test_acessorias_directory.py`
  — retain cross-instance, concurrent, expiry, endpoint-isolation,
  `Retry-After`, bounded-retry, credential, and directory publication coverage.
- **Structural/static:**
  `python -m compileall -q src tests alembic scripts` and
  `npx --yes pyright`; inspect that neither adapter imports a private limiter
  from the other domain module.
- **Canonical disposable verification:**
  `PYTHONPATH=/app python scripts/verify.py` when its PostgreSQL/Compose
  prerequisites are available; report unavailable external prerequisites
  separately from passes and skips.
- **Hygiene:** `git diff --check`.
- **Graph:** `graphify update .` after implementation changes.

## Acceptance Criteria

- [x] One neutral internal provider-coordination boundary owns the generic
  sliding-window state and admission operation; the Directory module no longer
  owns the helper solely because Request code uses it.
- [x] `acessorias_requests.py` no longer imports a private symbol from
  `acessorias_directory.py`, and both adapters depend directly on the neutral
  boundary without a reverse or cross-domain adapter dependency.
- [x] Request adapter instances with identical endpoint/configuration keys
  preserve issue-0019's shared, concurrency-safe one-minute admission,
  endpoint isolation, expiry, and injected-clock behavior; Directory adapter
  instances retain their prior local scope.
- [x] Admission still occurs before every existing provider attempt, and all
  existing retry, `Retry-After`, pre-send, ambiguous transport, provider
  outcome, and durable Request state semantics remain unchanged.
- [x] Directory synchronization retains its current authentication,
  pagination, validation, transaction, advisory-lock, snapshot, and failure
  preservation behavior, with no new provider or database side effect.
- [x] No public API/CLI/event contract, persistence schema/semantics, security
  or privacy policy, retry/idempotency/concurrency policy, dependency, config,
  Redis state, credential, payload, PII, or raw provider data changes.
- [x] Focused tests, compileall, strict Pyright, `git diff --check`, and the
  canonical disposable runner where available pass, with local/disposable
  evidence distinguished from provider, Redis, deployment, or production
  evidence.
- [x] The active README/PRD/ARCHITECTURE/IMPLEMENTATION_PLAN and SPEC-0011
  references remain internally consistent; any affected implementation source
  map is synchronized, Graphify is updated, and no completed behavior contract
  is rewritten to describe a new policy.
- [x] The issue is closed only after validation and one focused commit.

## References

- **Primary contract:** `specs/0011-durable-acessorias-request-creation.md`
  v1.4, especially the provider boundary, shared endpoint/configuration
  Sliding Window, bounded retry, and uncertain POST outcome rules.
- **Directory contract:** `specs/0007-acessorias-external-directory-foundation.md`
  v1.1, especially the dedicated adapter, transient in-process throttling,
  complete-snapshot publication, and sanitized state requirements.
- **Product/architecture:** `PRD.md` §§5.5 and 8; `ARCHITECTURE.md` §§2.1,
  12, and 14. PostgreSQL remains the durable authority and provider admission
  remains transient coordination.
- **Plan:** `IMPLEMENTATION_PLAN.md` Milestone E, including the completed
  issue-0019 shared Request-admission evidence at lines 256-259; this is
  structural maintenance, not a new milestone or policy.
- **Current source evidence:** `src/core/provider_coordination.py:1-61`
  owns the generic Sliding Window primitive; both adapters import it directly
  at `src/core/acessorias_directory.py:28` and
  `src/core/acessorias_requests.py:30`, constructing it at `:291` and `:281`.
- **Current tests:** `tests/test_provider_coordination.py:8-18` verifies the
  neutral owner for both adapters; `tests/test_acessorias_requests.py:380-451`
  verifies cross-instance sharing, expiry, endpoint isolation, and concurrent
  admission; `tests/test_acessorias_directory.py:172-192` verifies Directory
  `Retry-After` handling and its adapter-local path.
- **Related implementation:** issue `0017` established the Request provider
  boundary; closed issue `0019` established the shared Request limiter and is
  the behavior-preserving prerequisite.
- **Non-duplicate rationale:** issue `0019` changed and verified the externally
  observable cross-instance throttling behavior. This issue does not change
  that outcome, rate policy, or test contract; it only removes the private
  cross-domain dependency and gives the already-shared primitive a neutral
  structural owner. Issues `0028`–`0033` concern database-facade persistence
  slices and do not cover provider-coordination module ownership.

---

## Resolution

Implemented and closed issue 0034.

- Added the dependency-free `src/core/provider_coordination.py` boundary with
  the existing validated, lock-protected 60-second Sliding Window state and
  admission behavior.
- Migrated `AcessoriasDirectoryAdapter` and `AcessoriasRequestAdapter` to the
  neutral boundary. Directory instances remain local; Request instances still
  share only identical sanitized endpoint/rate keys. The private cross-domain
  import and Directory-owned helper were removed.
- Added a structural regression test for both adapter ownership and retained
  all existing adapter coverage for expiry, endpoint isolation, concurrent
  admission, Retry-After, bounded retry, credentials, payload, and uncertain
  provider outcomes.

Migrations: none. Public APIs, provider payloads, retry/reconciliation
semantics, configuration, Redis state, and durable PostgreSQL state are
unchanged.

Documentation: synchronized README.md, PRD.md, ARCHITECTURE.md,
IMPLEMENTATION_PLAN.md, SPEC-0007, SPEC-0011, and `specs/README.md`; refreshed
Graphify metadata.

Validation:

- `PYTHONPATH=/app python -m pytest -q tests/test_provider_coordination.py tests/test_acessorias_requests.py tests/test_acessorias_directory.py` — **36 passed, 10 skipped**.
- `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py` — **221 passed, 69 skipped**.
- `python -m compileall -q src tests alembic scripts` — passed.
- `npx --yes pyright` — **0 errors, 0 warnings, 0 informations**.
- `PYTHONPATH=/app python scripts/verify.py` — all stages passed; PostgreSQL pytest **69 passed, 221 deselected**.
- `git diff --check` on issue files — passed.
- `graphify update .` — passed; graph rebuilt with **2,094 nodes and 4,391 edges**.
