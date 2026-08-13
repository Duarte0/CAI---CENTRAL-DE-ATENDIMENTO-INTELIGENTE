# Implementation Plan

_Planning baseline: 2026-08-13. Code, Alembic migrations, configuration and
tests take precedence over this plan. Status describes this checkout and
recorded local evidence, never production availability._

## Evidence-based current state

### Completed and locally verified

- **[completed] Persistent conversation analysis and recovery**
  (PRD §§5–8; SPEC-0001–0003). FastAPI ingestion, PostgreSQL cycle state,
  Redis coordination, DigiSac-history reconstruction, Groq classification, and
  separate audio/image workers are implemented. PostgreSQL persists before
  publication; leases, `next_attempt_at`, reconciliation, and idempotent
  identity recover interrupted work. Terminal image failure blocks only its
  dependent cycle; terminal audio failure is represented as a warning.
- **[completed] Persistent-only finalization** (PRD §5.4; SPEC-0003;
  issue 0005). The feature flag, Redis buffer/debounce, legacy worker branch,
  API fallbacks, models, and legacy tests were removed. Targeted search finds
  no active legacy code or setting.
- **[completed] Durable schema and migration foundation** (SPEC-0001). Alembic
  owns schema through `0014_durable_retry_scheduling`; migrations, backfills,
  import, and audit utilities are versioned. Application code verifies rather
  than creates schema.
- **[completed] Webhook hardening and supported HTTP surface** (SPEC-0002;
  issue 0006). Production HMAC-before-parse handling remains; raw-payload
  diagnostic routes/modules are removed and focused tests prove both historical
  paths return `404`.
- **[completed] Reproducible local verification** (SPEC-0004; issues 0001,
  0002, 0004). `scripts/verify.py` owns an isolated PostgreSQL 16 Compose
  target, verifies process connectivity and Alembic head, then runs the
  PostgreSQL-marked family. Its latest recorded execution (issue 0006,
  2026-08-09) passed compileall, strict Pyright, offline pytest (**122 passed,
  33 skipped**), Alembic `0014_retry_scheduling`, and PostgreSQL pytest
  (**33 passed, 122 deselected**). The 33 offline skips are expected missing
  `CAI_TEST_DATABASE_URL` prerequisites, not database-runtime evidence.

### Implemented, with bounded verification only

- **[completed | local-only evidence] External integrations and deployment.**
  Redis, DigiSac, Groq, Docker Compose, migrations, and the opt-in live webhook
  test are implemented, but the checked-in runner deliberately substitutes a
  deterministic queue and disposable PostgreSQL. There is no recorded current
  verification against a running Redis deployment, DigiSac/Groq provider,
  replicas, or production target. This is a release-evidence limitation, not a
  code defect or an authorized rollout task.
- **[completed | opt-in] Live webhook test.** `tests/test_webhook_local.py`
  intentionally remains outside canonical automation and requires a separately
  started local API.

### Planning signals

- The current canonical collection contains **160 tests** when the live webhook
  test is excluded; the latest issue-0008 run passed **127 tests** and skipped
  the 33 PostgreSQL-dependent tests without a configured database.
- Targeted TODO/placeholder/stub searches found no implementation backlog.
  Remaining `pass` statements are migration or exception-control flow.
- All eight implementation issues are `closed`; no open issue supplies an
  eligible build item. Earlier Phase 0/1 plan work is complete and must not be
  reopened.

## Priority plan

### Phase 1 — Reconcile stale verification and compatibility documentation

1. **[P1 | completed] Correct the implementation-derived documentation
   baseline** (PRD §§5.4, 7, 9; ARCHITECTURE §§10, 13; SPEC-0002–0006).

   Outcome: documentation and active specifications describe the current
   persistent-only code and the latest recorded verification evidence without
   implying a deployed or versioned API.

   Completion criteria:

   - [x] replace README's obsolete “fluxo legado” status wording and its claim that
     the runner explicitly enables a removed persistent-finalization flag;
   - [x] correct SPEC-0002's status line so it matches its contract and
     `src/api/routes.py`: query routes are unversioned;
   - [x] make the plan, README, PRD, architecture, SPEC-0004, and spec index use
     the latest issue-0006 test evidence (**122/33** offline; **33/122**
     PostgreSQL) or clearly label any older result as historical; and
   - [x] retain `/v1/` and `/v2/` solely as future compatibility policy, with no
     mounted-route claim.

   Specification outcome: SPEC-0005 defines the bounded documentation
   reconciliation and is implemented as v1.1. SPEC-0006 defines the
   implementation-ready OpenAPI/HTTP contract. This item is complete; no
   application behavior changed.

   Evidence: issue 0007 reconciled the affected documents against the current
   source, runner, and issue-0006 result. The canonical offline evidence is
   **122 passed, 33 skipped** and the disposable PostgreSQL evidence is
   **33 passed, 122 deselected**; neither implies external-runtime or
   production readiness.

   Dependencies: none. Risk: stale operational instructions could make an
   operator attempt a removed configuration path or infer nonexistent API
   compatibility. No product decision is required; source already resolves the
   behavior. This item is documentation/spec work, not an application change.

2. **[P1 | completed] Publish the generated OpenAPI HTTP contract**
   (SPEC-0006; issue 0008).

   Outcome: the existing FastAPI application now composes a cached OpenAPI 3.1
   document from its mounted routes and source-backed HTTP projections. Swagger
   UI, ReDoc, and `/openapi.json` remain FastAPI's standard documentation
   endpoints; the eight unversioned business operations, conditional webhook
   HMAC, response variants, identifiers, processing states, and error boundary
   are documented without changing handler behavior.

   Completion evidence:

   - [x] generated document has the effective application metadata, safe local
     server, four tags, eight business paths, request/response schemas, and
     sanitized examples;
   - [x] webhook security is conditional in the description and limited to the
     webhook operation; query operations retain no authentication contract;
   - [x] README, SPEC-0006, and the specification index link the three standard
     documentation URLs and describe the current consumer boundary; and
   - [x] focused documentation tests, offline suite, disposable PostgreSQL
     runner, compileall, strict Pyright, `git diff --check`, and Graphify pass.

   Evidence: focused OpenAPI tests **5 passed**; offline pytest **127 passed,
   33 skipped**; disposable PostgreSQL pytest **33 passed, 127 deselected**;
   compileall, Pyright, runner, and documentation searches passed. No
   migrations, handlers, providers, credentials, or external production
   systems were changed or invoked.

### Phase 2 — Conditional release/production evidence (not ready to build)

3. **[P2 | blocked | decision/operations] Define and authorize a production
   acceptance run only when a deployment is intended** (PRD §§8–10;
   ARCHITECTURE §§11, 13; SPEC-0004).

   Outcome: an approved, non-destructive runbook could establish evidence for
   the currently unverified external boundaries: deployment topology, Redis,
   DigiSac/Groq credentials and provider behavior, and the opt-in live webhook.

   Completion criteria: product/operations identifies the environment, target,
   acceptable test data, backup/rollback ownership, secrets handling, and
   release acceptance threshold; only then write a scoped operational spec and
   issue. The existing disposable runner remains the required precondition.

   Blockers: no production target, credentials, rollout authority, SLA, or
   acceptance threshold is defined in the authoritative documents. Do not
   infer any of them. Hosted CI remains optional under PRD §10 and is not a
   missing implementation item.

## Completed history and superseded work

- **[completed]** Canonical test isolation (issue 0001), disposable PostgreSQL
  runner (0002), documentation baseline (0003), durable recovery coverage
  (0004), legacy finalization removal (0005), raw-payload diagnostic-surface
  removal (0006), and persistent implementation documentation reconciliation
  (0007), and generated OpenAPI HTTP contract publication (0008).
- **[superseded]** Any plan item proposing PRD/architecture/spec creation,
  legacy-finalization removal, diagnostic-route removal, fixed-port test
  Compose work, or broader database recovery coverage. These artifacts and
  their focused evidence already exist.
- **[non-work]** Automatic retention/archival, query authentication/rate
  limiting, mounted `/v1/` aliases, hosted CI, provider/model replacement, and
  Acessórias routing are not implied by the current requirements. They require
  a future approved product/spec increment.

## Dependencies, risks, and recorded discrepancies

- **Documentation inconsistency (Phase 1, resolved by issue 0007):** README,
  PRD, architecture, SPEC-0002, SPEC-0004, the index, and this plan now state
  persistent-only finalization, unversioned mounted queries, and future-only
  `/v1/`/`/v2/` policy.
- **Verification evidence drift (Phase 1, resolved by issue 0007):** active
  documentation now distinguishes **122 passed, 33 skipped** offline from
  **33 passed, 122 deselected** on disposable PostgreSQL. The evidence remains
  local and does not prove Redis, provider, replica, deployment, or production
  readiness.
- **External-runtime boundary (Phase 2):** local disposable verification is
  intentionally insufficient to claim provider, Redis, replica, or production
  readiness. The limitation affects only a future deployment acceptance task.
- **HTTP documentation boundary (Phase 1, resolved by issue 0008):** the
  generated document describes current response projections without adding
  response enforcement. The `processing` source enum discrepancy and unmapped
  Redis failures remain documented limitations rather than new HTTP behavior.
- **No migration or infrastructure work is pending** for the completed
  persistent-only baseline. Any future schema or production operation must be
  additive, Alembic-owned, and separately authorized.

## Recommended next pass

No eligible open build issue remains in the current issue set. Phase 2 remains
blocked on its separately authorized operational decision; it requires a new
approved operational specification before implementation work is scheduled.
