# Implementation Plan

_Planning baseline: 2026-08-09. Source code, Alembic migrations, configuration,
and executed tests take precedence over this plan and the implementation-derived
documentation. Status describes this checkout, not production availability._

## Evidence-based current state

### Completed implementation

- **[completed] Durable conversation analysis.** The API, PostgreSQL cycle
  state, Redis coordination, Groq classification, and separate audio/image
  workers implement the persistent DigiSac-history flow and the feature-flagged
  legacy Redis-buffer flow pending its approved removal. Persistent cycles record history snapshot and
  ordered membership, reconcile scheduled media, and block terminal image
  failures rather than classify incomplete context. References: PRD §§5–8,
  ARCHITECTURE §§3–9, SPEC-0001–0003.
- **[completed] Schema and recovery foundation.** Alembic owns the schema
  through `0014_retry_scheduling`; the additive identity, message, cycle, and
  media-scheduling migrations plus audit/backfill/import utilities are present.
  Compose runs migration before application services. The legacy SQLite SQL in
  `migrations/` is import/documentation support, not the live schema authority.
- **[completed] Image documents.** Commit `878a464` normalizes `document`
  messages whose MIME type is `image/*` as images, reserves visual extraction,
  and renders successful output as image content. The seven focused tests in
  `test_media_detection.py` pass.

### Completed verification (local, non-production)

- **[completed] Static and configuration checks.** `python -m compileall -q
  src tests alembic scripts`, `npx --yes pyright` (0 diagnostics), and
  `docker compose config -q` for both Compose files passed.
- **[completed] Isolated offline behavioral suite.** Both
  `PYTHONPATH=/app DIGISAC_HISTORY_FINALIZATION_ENABLED=true pytest -q
  --ignore=tests/test_webhook_local.py` and the same command with `false`
  produced **120 passed, 28 skipped** on 2026-08-09. The skipped tests require
  `CAI_TEST_DATABASE_URL`; the live webhook test remains opt-in. The test-owned
  fixture selects persistent finalization and restores environment/settings
  state after each test.
- **[completed] Disposable PostgreSQL verification runner.** `PYTHONPATH=/app
  python scripts/verify.py` creates a uniquely named Compose project with
  PostgreSQL 16 and a dynamically published host port, or uses the explicit
  `postgres-test:5432` Docker-network form when the runner is containerized.
  The observed run passed compileall, Pyright, offline pytest (**128 passed,
  28 skipped**), process connectivity, Alembic
  `0014_retry_scheduling`, and PostgreSQL pytest (**28 passed, 128
  deselected**). The 28 offline skips are not PostgreSQL runtime evidence; the
  dedicated PostgreSQL stage had no prerequisite skips.

### Implemented but not reproducibly release-verified

- **[completed] Canonical offline test suite is tracked and isolated.** 148
  tests are collected locally (excluding the opt-in live webhook test), and the
  persistent close/reopen, duplicate-cycle, bot, negative-webhook, and
  publication-recovery coverage is versioned with the fixture boundary. A clean
  checkout can reproduce the offline evidence without a personal `.env` value.
- **[completed] PostgreSQL migration/integration baseline.** The versioned
  runner proves the exact disposable target from the test process, migrates it
  to Alembic head, and executes all 28 PostgreSQL tests. No production or
  developer database was used. Phase 1 item 4 remains responsible for broader
  operational verification beyond this baseline.

## Priority plan

### Phase 0 — Make the existing baseline deliverable and reproducible

1. **[P0 | completed] Version and isolate the canonical test suite**
   (SPEC-0004 §§1–3).

   Outcome: a clean checkout contains the offline and PostgreSQL test families
   that currently provide evidence, and each family selects its finalization
   mode rather than inheriting a developer `.env`.

   Completion criteria:

   - replace the blanket `tests/*` ignore policy with an explicit tracked-suite
     policy; retain only justified local artifacts such as
     `test_webhook_local.py` as opt-in;
   - make fixtures/environment reset settings deterministically, with the
     persistent mode explicitly selected;
   - remove legacy-mode fixtures and replace `test_ticket_closure.py` with
     tracked persistent-cycle coverage; and
   - the clean-checkout offline command passes without personal `.env` input.

   Evidence: the canonical command produces **120 passed, 28 skipped** with
   either externally supplied flag value. The additional runner tests are
   included in the full runner's **128 passed, 28 skipped** offline stage.

2. **[P0 | completed] Establish an executable PostgreSQL verification runner**
   (SPEC-0004 §§4–5; SPEC-0001–0003 acceptance).

   Outcome: the versioned local runner starts an isolated PostgreSQL 16
   service, applies Alembic head, supplies `CAI_TEST_DATABASE_URL`, and runs
   the database families without contacting a developer or production database.

   Completion criteria:

   - [x] avoid the fixed host-port collision in `docker-compose.test.yml` and
     document host and in-container connection forms;
   - [x] prove connection from the test process, not merely PostgreSQL health
     inside its container;
   - [x] run compileall, strict Pyright, offline tests, Alembic verification,
     and all PostgreSQL tests with separate stage results; and
   - [x] fail automation on any canonical stage while continuing to exclude the
     opt-in live webhook test.

   Dependency: item 1 satisfied. Risk addressed: the runner owns the unique
   Compose project, temporary database, network form, and cleanup before the
   fixture truncates its target.

3. **[P0 | completed] Reconcile and version the documentation/spec baseline.**

   Outcome: README, PRD, architecture, and `specs/` are a single,
  implementation-derived baseline with product decisions explicitly recorded,
  and are versioned with their referenced tests.

   Completion criteria:

   - [x] review and version the existing working-tree documentation after verifying
     variables, routes, Compose behavior, migrations, recovery commands, and
     test commands against source;
   - [x] the 2026-08-09 specification pass reconciled those contracts: SPEC-0003
     v1.2 records the approved removal of legacy mode, and SPEC-0002 v1.3 records
     the mounted `/webhook/debug` raw-payload response as an internal diagnostic
     behavior rather than a sanitized or public API contract. The associated
     authorization and retention decisions are recorded, while diagnostic
     redaction and exposure remain limited to internal use under item 5;
   - [x] retain implementation-derived status until product approval rather than
     claiming approved policy, and update README/API wording to state the
     resulting debug-payload contract explicitly; and
   - [x] update documentation to distinguish the locally verified offline baseline
     from the unverified PostgreSQL/runtime baseline; and
   - [x] keep PostgreSQL as durable source of truth and Redis as coordination,
     while documenting the still-implemented legacy worker limitation and its
     approved future removal.

   Evidence: the documentation-only reconciliation was validated against the
   source, migrations, tests, and the observed `scripts/verify.py` run on
   2026-08-09. That run passed compileall, Pyright, offline pytest (128 passed,
   28 skipped), Alembic `0014_retry_scheduling`, and PostgreSQL pytest (28
   passed, 128 deselected). It does not claim production verification.

   Dependency: item 1 for reproducible test references. This supersedes the
   previous plan item that proposed creating PRD/architecture/spec files: they
   now exist and this item reconciles their implementation status and evidence.

### Phase 1 — Close operational and exposed-surface gaps

4. **[P1 | pending] Verify broader durable operation on the executable runner**
   (SPEC-0001–0003).

   Outcome: Alembic head and the durable cycle/media paths are proven together
   on a fresh disposable database.

   Completion criteria: extend the already successful runner baseline with
   broader operational checks for cycle claim/lease, publication recovery,
   due-media wake-up, blocked-image behavior, and idempotent queue publication.
   The current runner has already executed all 28 PostgreSQL tests against its
   disposable target; investigate failures as implementation defects only after
   the runner has a confirmed reachable target.

   Dependency: item 2. Do not run backfills or migrations against the active
   deployment under this item; production rollout still requires separately
   approved backup and target-state audit.

5. **[P1 | blocked] Resolve raw-payload diagnostic surfaces before any
   exposure or contract expansion.**

   `src/api/debug_routes.py` defines an unmounted `/debug/webhook` handler that
   prints headers and raw request bodies. Separately, the mounted
   `/webhook/debug` route validates HMAC but returns `raw_payload` to its caller.
   PRD §8 explicitly describes parsed raw-payload diagnostics, while
   SPEC-0002 says operational responses must not expose raw bodies and does not
   define a raw-payload public contract.

   Outcome: one approved, least-privilege diagnostic contract that states
   whether any raw payload may be returned, logged, retained, or routed, and
   how access is authenticated and audited.

   Completion criteria:

   - security/operations approves the intended diagnostic audience, redaction
     rules, and retention/logging behavior;
   - code, PRD, architecture, README, and SPEC-0002 agree on the mounted route;
     and
   - the unmounted handler is either removed or has an approved authenticated,
     redacted use case with tests. It must not be mounted or advertised before
     that decision.

   Blocker: product/security authorization and diagnostic-data policy. This
   does not block internal webhook ingestion, but blocks debug-surface exposure
   or any claim that it is safe for third-party consumers.

### Phase 2 — Product and data-policy decisions

6. **[completed] Record retention, archival/deletion, access-control, and legal
   privacy policy** (PRD §10; SPEC-0001).

   Impact: data is retained indefinitely with no cleanup job, retention schema,
   or LGPD-driven automation; manual direct-PostgreSQL deletion is case by case.
   Query access is internal-only without a reader authorization layer, and
   production webhook validation requires `WEBHOOK_SECRET`.

7. **[completed] Record historical-assignment interpretation and API consumer
   policy** (PRD §10; SPEC-0001–0002).

   Impact: all observed assignment transfers remain chronological for future
   Acessórias routing. The internal query API has no rate limiting and is
   currently mounted without a version prefix; `/v1/` and `/v2/` remain future
   compatibility policy, not implemented route claims.

8. **[completed] Record the approved removal of legacy finalization**
   (PRD §10; ARCHITECTURE §5; SPEC-0003).

   Impact: remove the flag, Redis-buffer keys, debounce, legacy IA worker handling,
   single-replica recovery limitation, and legacy test matrix; retain only the
   persistent DigiSac-history mode. The compatibility/legacy section of SPEC-0003
   is removed as part of the refactor.

## Discrepancies, dependencies, and non-work

- The prior plan's claim that PRD, architecture, and `specs/` were absent is
  obsolete. Those implementation-derived workspace artifacts are now referenced
  by SPEC-0001–0004 v1.1. Item 1 now versions the canonical test modules and
  isolates their persistent-mode evidence; item 2 now supplies the executable
  PostgreSQL runner.
- SPEC-0003, PRD §10, and ARCHITECTURE now record the approved removal of the
  legacy finalization mode; the implementation refactor and replacement tests
  remain delivery work.
- The mounted `/webhook/debug` returns `raw_payload` after HMAC validation when
  configured; the unmounted handler also prints/returns raw headers and body.
  The mounted response is now documented as an internal diagnostic exception to
  the general sanitized-response rule, while item 5 owns least-privilege,
  redaction, retention, and audience policy.
- Earlier SPEC-0002/PRD wording described query routes as `/v1/`, but
  `src/api/routes.py` currently mounts them without that prefix. The baseline
  now follows the source and records `/v1/`/`/v2/` as future policy only; adding
  versioned aliases is outside this documentation issue.
- The current code search found no TODO/FIXME/stub backlog. Inspected `pass`
  statements are exception or migration control flow, not placeholders.
- There is no hosted CI configuration; `scripts/verify.py` is the canonical
  local runner. `docker-compose.test.yml` remains the disposable service
  definition used by that runner.
- `tests/test_webhook_local.py` is intentionally live/opt-in and remains outside
  the canonical automation unless a local API is deliberately started.
- Item 2 changes only the disposable test Compose publication, verification
  tooling/tests, and versioned verification documentation; it changes no
  application behavior, Alembic migration, production data, or active Compose
  project.

## Recommended next pass

**Phase 1 item 4** — extend the verified baseline into broader durable-operation
checks as needed. Product/security decisions in Phase 1–2 need owner input
first.
