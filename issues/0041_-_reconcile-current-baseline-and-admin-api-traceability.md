---
id: 0041
title: "Reconcile the current verification baseline and administrative API traceability"
type: spec
status: closed
priority: high
phase: 1
created_at: 2026-08-21
updated_at: 2026-08-21
closed_at: 2026-08-21
related_issues:
  - "0009"
  - "0038"
  - "0039"
  - "0040"
blocked_by: []
affects:
  - README.md
  - PRD.md
  - ARCHITECTURE.md
  - specs/0004-reproducible-verification-baseline.md
  - specs/0005-documentation-baseline-reconciliation.md
  - specs/0006-api-documentation-and-openapi-contract.md
  - specs/README.md
  - IMPLEMENTATION_PLAN.md
---

## Description

Complete **Phase 1, item 1** of `IMPLEMENTATION_PLAN.md`: reconcile the
current implementation evidence and administrative API traceability in the
active documentation. This is the ready documentation delta of **SPEC-0005
v1.4**; it does not reopen the completed baseline work in issue 0009 or the
completed SPEC-0012 implementation issues 0038–0040.

**Verified gap:** the current source, Alembic revisions, `scripts/verify.py`,
SPEC-0004 v1.7, SPEC-0006 v1.2, the specification index, and the plan record
Alembic head `0022_identity_discovery_command`, **238 passed, 76 skipped** in
the offline suite, and **76 passed, 238 deselected** in the disposable
PostgreSQL stage. `PRD.md` §9 and its source traceability still present the
older 2026-08-17 `0020_cycle_contact_provenance` / **203 passed, 68 skipped** /
**68 passed, 203 deselected** evidence. `ARCHITECTURE.md` §13 still
foregrounds the same stale baseline. The active documents must also describe
the complete six-operation authenticated `/admin/acessorias` surface from
SPEC-0012 alongside the eight original operations, without presenting the
administrative API as public or claiming that the quarantined SPEC-0013 UI
exists.

Expected outcome: maintainers can identify the actual local verification
baseline, the source-backed migration head, the complete authenticated
SPEC-0012 read/command/discovery surface, and the boundaries around local,
disposable, provider, Redis, deployment, and production evidence without
inferring a removed feature, a new route, a UI, or a production acceptance.

## Scope

### In scope

- Reconcile `PRD.md` §9 and source traceability, `ARCHITECTURE.md` §13 and
  source map, `README.md`, `specs/README.md`, SPEC-0004, SPEC-0005, and
  SPEC-0006 against the current source, migrations, generated OpenAPI
  contract, tracked tests, `scripts/verify.py`, and the closed issues
  0038–0040.
- Replace active stale `0020`/`203+68` baseline claims with the issue-0040
  evidence `0022`/`238+76`; retain older counts only as dated historical
  evidence when useful, never as the latest baseline.
- Describe SPEC-0012's six authenticated internal operations, Bearer
  `ADMIN_API_TOKEN` boundary, PostgreSQL command idempotency, sanitized
  projections, and provider/Redis-free administrative behavior consistently
  with the implementation. Keep the eight original HTTP operations distinct
  from the six administrative operations.
- Update statuses, links, versions, and traceability so SPEC-0005 v1.4 and
  the plan can record this delta as completed after verification. Preserve
  SPEC-0013 as a non-active, product-authorization-blocked proposal.
- Preserve the existing documentation contracts for persistent-only
  finalization, unversioned query routes, runner credential isolation, and the
  distinction between local/disposable verification and external-runtime or
  production acceptance.

### Explicitly out of scope

- Any application, worker, API handler, OpenAPI runtime, test, migration,
  configuration, Compose, infrastructure, provider, database, Redis,
  deployment, or production-data change.
- Any change to the eight original routes, the six SPEC-0012 routes,
  authentication behavior, identity matching, command idempotency, Request
  creation, cycle resolution, or provider retry/reconciliation behavior.
- Implementing or authorizing SPEC-0013's UI, login, session, BFF, browser,
  accessibility, or new-secret decisions. Do not claim a UI is implemented.
- Product decisions for Request lifecycle integration, broader IA policy, or
  production acceptance; these remain blocked plan items.
- Rewriting or reopening closed issue records, manufacturing provider/Redis/
  deployment evidence, or changing historical counts in closed issues.

## Implementation Plan

1. Inventory the affected claims before editing. Confirm the current route and
   administrative surface in `src/api/routes.py`, `src/api/admin_routes.py`,
   `src/api/openapi.py`, and SPEC-0012; confirm the migration head and schema
   ownership in `alembic/versions/0021_identity_admin_commands.py`,
   `alembic/versions/0022_identity_discovery_command.py`, and the current
   database capability checks; confirm the verification stages and expected
   runner target in `scripts/verify.py`. Treat source, migrations, and observed
   verification as authoritative over stale PRD/architecture text.
2. Update SPEC-0005 and the specification index first, then SPEC-0004 and
   SPEC-0006, followed by README, PRD, architecture, and the plan traceability.
   Keep links and IDs stable. Record `0022` and the issue-0040 counts as the
   current local baseline, label prior counts as dated history, and describe
   only the six mounted administrative operations covered by SPEC-0012.
3. Keep the documentation invariants explicit: persistent DigiSac-history
   finalization is the only supported path; query routes are unversioned and
   `/v1`/`/v2` are future policy only; the offline runner does not select a
   finalization flag; database credentials are isolated before the disposable
   PostgreSQL URL is injected; and local tests do not prove Redis, DigiSac,
   Groq, secret-manager, replica, deployment, or production availability.
4. Preserve administrative correctness in the prose: `ADMIN_API_TOKEN` is
   required for the internal bearer-protected surface; sanitized IDs,
   statuses, counts, and safe timestamps are the only documented projections;
   PostgreSQL is the authority for audit/idempotency state; same-key retries
   converge and incompatible key reuse is a conflict; concurrent commands do
   not duplicate transitions; discovery does not call providers or mutate
   historical cycle resolution; and no administrative action creates a
   Request. Do not invent a UI, provider idempotency contract, or retry policy.
5. Run the targeted documentation, link/reference, secret, and stale-claim
   checks described below. Refresh repository evidence only through the
   existing canonical runner when available, record exactly what ran, execute
   `graphify update .` after documentation changes, inspect the final diff, and
   close through one focused documentation commit after synchronizing
   `IMPLEMENTATION_PLAN.md`.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migrations:** none. This issue changes documentation only; do not
  apply migrations, backfill data, or connect to an active database target.
  Migration references are descriptive and must match the checked-in head.
- **Compatibility:** document the currently mounted unversioned routes and
  the existing eight-plus-six operation split. Do not add aliases, new API
  versions, authentication promises for query routes, or behavior changes.
- **Security:** do not include real credentials, bearer values, raw webhook or
  model payloads, phone/email/evidence values, provider secrets, signed URLs,
  or database targets in documentation. Preserve the internal/admin bearer
  boundary and the absence of a UI or browser-held `ADMIN_API_TOKEN`.
- **Observability:** distinguish offline pytest skips from disposable
  PostgreSQL deselection, and both from unverified Redis, providers,
  replicas, deployment, secret manager, and production. Preserve the
  opt-in-only status of `tests/test_webhook_local.py`.
- **Rollout:** documentation-only. No provider call, Redis operation, live
  webhook, deployment, or production acceptance is authorized or required.

## Tests

- **Targeted documentation consistency:** use `rg` over README, PRD,
  ARCHITECTURE, `specs/`, and the plan to find active `0020`, `203/68`, stale
  migration-head, missing administrative-route, public-API, UI, legacy-flow,
  finalization-flag, versioned-route, and production-readiness claims. Verify
  all changed links, specification IDs/versions, issue references, route
  counts, and commands against source or the cited evidence.
- **Security/privacy:** inspect the changed diff and run repository secret/raw
  payload searches; confirm no real credential, token, raw evidence, contact
  value, webhook body, or provider payload is introduced.
- **Canonical repository evidence:** run
  `PYTHONPATH=/app python scripts/verify.py` when Docker is available. If the
  disposable PostgreSQL stage is unavailable, run the safe static/offline
  subset and report that stage as unavailable rather than claiming it passed.
- **Static/regression:** run `python -m compileall -q src tests alembic scripts`,
  `npx --yes pyright`, and
  `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`
  when refreshed evidence is required; keep the local webhook smoke opt-in.
- **Repository hygiene:** run `git diff --check`, inspect the staged/document
  diff for scope and accidental edits, and run `graphify update .` after the
  documentation changes.

## Acceptance Criteria

- [x] `PRD.md` §9/source traceability, `ARCHITECTURE.md` §13/source map,
  README, SPEC-0004, SPEC-0005, SPEC-0006, and `specs/README.md` consistently
  identify Alembic `0022_identity_discovery_command`, **238 passed, 76
  skipped** offline, and **76 passed, 238 deselected** on disposable
  PostgreSQL as the latest local evidence.
- [x] Older `0020`/`203+68` and other prior results are either removed from
  active-baseline wording or explicitly dated and labeled historical; no
  closed issue record is rewritten.
- [x] The active documentation distinguishes the eight original HTTP
  operations from the six authenticated internal SPEC-0012 operations,
  documents Bearer `ADMIN_API_TOKEN`, and does not present the administrative
  API as public or claim that SPEC-0013's UI is implemented.
- [x] The documentation preserves SPEC-0012's expected, negative, retry,
  idempotency, and concurrency invariants: sanitized projections; PostgreSQL
  command authority; same-key replay convergence; incompatible-key conflict;
  no duplicate transitions under concurrency; no provider/Redis calls during
  admin discovery; and no Request or historical-cycle mutation from admin
  commands.
- [x] The documentation continues to state persistent-only finalization,
  unversioned current query routes, future-only `/v1`/`/v2` policy, offline
  runner credential isolation, and no finalization flag selection.
- [x] No changed document claims that local/offline/disposable evidence proves
  Redis, DigiSac, Groq, secret-manager provisioning, replicas, deployment,
  provider behavior, or production readiness; no real secret or sensitive
  payload/value is present.
- [x] The change contains no application, test, migration, configuration,
  infrastructure, runtime, data, or external-environment modification.
- [x] Targeted stale-claim, link/reference, route-count, version, and secret
  checks pass; the canonical/static commands are run as applicable and their
  actual results are recorded without fabricated evidence.
- [x] `graphify update .` succeeds, the final diff contains only the intended
  documentation and plan-sync changes, and no prohibited file is changed.
- [x] `IMPLEMENTATION_PLAN.md` marks Phase 1 item 1 complete with the observed
  evidence, SPEC-0005/index traceability is synchronized, and this issue is
  closed only after one focused documentation commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — Phase 1, item 1 (selected); Phase 2 item 2
  (SPEC-0013 UI) and Phase 3 items remain blocked and are not this issue.
- Primary specification: `specs/0005-documentation-baseline-reconciliation.md`
  v1.4 — current baseline, documentation invariants, evidence boundaries, and
  acceptance contract.
- Administrative contract: `specs/0012-administrative-contact-company-link-management.md`
  v1.1 — six internal authenticated operations, sanitized projections,
  command idempotency, concurrency, privacy, and no-provider side effects.
- API compatibility: `specs/0006-api-documentation-and-openapi-contract.md`
  v1.2 — eight original operations, six administrative operations, security
  schemes, route/version boundary, and generated OpenAPI contract.
- Verification contract: `specs/0004-reproducible-verification-baseline.md`
  v1.7 — canonical runner stages and local/disposable evidence boundary.
- Completed dependencies: issues `0038`, `0039`, and `0040` implement the
  SPEC-0012 read, confirmation/rejection, and discovery slices; issue `0009`
  is the closed prior documentation reconciliation and supplies historical
  context, not an open duplicate.
- Current evidence sources: `src/api/admin_routes.py`, `src/api/openapi.py`,
  `src/core/identity_admin.py`, `src/core/identity_resolution.py`, Alembic
  revisions `0021_identity_admin_commands` and `0022_identity_discovery_command`,
  `scripts/verify.py`, tracked tests, and the generated OpenAPI output.

---

## Resolution

<!-- Filled by the agent on close. DO NOT edit manually. -->
<!-- Include changed documents, observed evidence, remaining external-runtime
     boundary, Graphify result, and the focused commit. -->

Implementation: reconciled the current documentation baseline to Alembic
`0022_identity_discovery_command`, **238 passed, 76 skipped** offline, and **76
passed, 238 deselected** on disposable PostgreSQL. Updated PRD §9/source map,
ARCHITECTURE §13/source map, README, SPEC-0004, SPEC-0005 v1.4, SPEC-0006 v1.2,
the specification index, and Phase 1 item 1 of `IMPLEMENTATION_PLAN.md`.
Documented the eight original operations separately from the six authenticated
internal SPEC-0012 operations, Bearer `ADMIN_API_TOKEN`, PostgreSQL command
idempotency/concurrency, sanitized projections, and the absence of provider,
Redis, Request, historical-cycle, or SPEC-0013 UI behavior.

Tests and validation: offline pytest passed **238 passed, 76 skipped**;
compileall and strict Pyright passed. `PYTHONPATH=/app python scripts/verify.py`
passed its static/offline/Alembic stages but the default dirty-tree environment
had one pre-existing PostgreSQL timezone failure in
`tests/test_department_mapping.py` from `APP_TIMEZONE=America/Sao_Paulo`.
`APP_TIMEZONE=UTC PYTHONPATH=/app python scripts/verify.py` passed fully,
including Alembic `0022_identity_discovery_command` and **76 passed, 238
deselected**. Targeted stale-claim, route-count, reference, secret, diff-check,
and `graphify update .` checks passed.

Migrations/data/runtime: none; this was documentation-only. Older verification
counts remain dated historical evidence. Local and disposable checks do not
prove Redis, DigiSac, Groq, secret-manager provisioning, replicas, deployment,
provider behavior, or production readiness. The SPEC-0013 UI remains
quarantined and requires product authorization.

The focused documentation commit is reported by the build pass.
