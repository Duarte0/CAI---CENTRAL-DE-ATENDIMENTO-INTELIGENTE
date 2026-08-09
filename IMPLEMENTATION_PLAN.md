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
  src tests alembic`, `npx --yes pyright` (0 diagnostics), and `docker compose
  config -q` for both Compose files passed.
- **[completed] Isolated offline behavioral suite.** Both
  `PYTHONPATH=/app DIGISAC_HISTORY_FINALIZATION_ENABLED=true pytest -q
  --ignore=tests/test_webhook_local.py` and the same command with `false`
  produced **120 passed, 28 skipped** on 2026-08-09. The skipped tests require
  `CAI_TEST_DATABASE_URL`; the live webhook test remains opt-in. The test-owned
  fixture selects persistent finalization and restores environment/settings
  state after each test.

### Implemented but not reproducibly release-verified

- **[completed] Canonical offline test suite is tracked and isolated.** 148
  tests are collected locally (excluding the opt-in live webhook test), and the
  persistent close/reopen, duplicate-cycle, bot, negative-webhook, and
  publication-recovery coverage is versioned with the fixture boundary. A clean
  checkout can reproduce the offline evidence without a personal `.env` value.
- **[pending verification] PostgreSQL migration/integration baseline.** The
  28 database tests did not run without `CAI_TEST_DATABASE_URL`. The supplied
  Compose test service validates syntactically but fixes host port 5433, which
  is occupied by another local project. An isolated PostgreSQL 16 container on
  5434 was reachable only inside the container; tests received connection
  refusal from this execution environment. No migration/head or worker-runtime
  conclusion should be drawn from those attempts.

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
   either externally supplied flag value. The skipped PostgreSQL families remain
   unverified until item 2 supplies a reachable disposable database.

2. **[P0 | pending] Establish an executable PostgreSQL verification runner**
   (SPEC-0004 §§4–5; SPEC-0001–0003 acceptance).

   Outcome: CI or an equivalent versioned runner starts an isolated PostgreSQL
   16 service, applies Alembic head, supplies `CAI_TEST_DATABASE_URL`, and runs
   the database families without contacting a developer or production database.

   Completion criteria:

   - parameterize or otherwise avoid the fixed host-port collision in
     `docker-compose.test.yml`; document host and in-container connection forms;
   - prove that the runner can connect from the test process, not merely that
     PostgreSQL is healthy inside its container;
   - run compileall, strict Pyright, offline tests, and all PostgreSQL tests in
     the runner; preserve separate results for pass, skip, and prerequisite
     failure; and
   - fail automation on any canonical stage while continuing to exclude the
     opt-in live webhook test.

   Dependency: item 1. Risk: the test fixture truncates its target database,
   so its URL must be demonstrably disposable.

3. **[P0 | pending] Reconcile and version the documentation/spec baseline.**

   Outcome: README, PRD, architecture, and `specs/` are a single,
  implementation-derived baseline with product decisions explicitly recorded,
  and are versioned with their referenced tests.

   Completion criteria:

   - review and version the existing working-tree documentation after verifying
     variables, routes, Compose behavior, migrations, recovery commands, and
     test commands against source;
   - the 2026-08-09 specification pass reconciled those contracts: SPEC-0003
     v1.2 records the approved removal of legacy mode, and SPEC-0002 v1.2 records
     the mounted `/webhook/debug` raw-payload response as an internal diagnostic
     behavior rather than a sanitized or public API contract. The associated
  authorization and retention decisions are recorded, while diagnostic
  redaction and exposure remain limited to internal use under item 5;
   - retain implementation-derived status until product approval rather than
     claiming approved policy, and update README/API wording to state the
     resulting debug-payload contract explicitly; and
   - update documentation to distinguish the locally verified offline baseline
     from the unverified PostgreSQL/runtime baseline; and
   - keep PostgreSQL as durable source of truth and Redis as coordination while
     removing the legacy worker's single-replica recovery limitation.

   Dependency: item 1 for reproducible test references. This supersedes the
   previous plan item that proposed creating PRD/architecture/spec files: they
   now exist, but remain unversioned and partially stale.

### Phase 1 — Close operational and exposed-surface gaps

4. **[P1 | pending] Verify durable operation on the executable runner**
   (SPEC-0001–0003).

   Outcome: Alembic head and the durable cycle/media paths are proven together
   on a fresh disposable database.

   Completion criteria: the runner in item 2 executes all 28 currently skipped
   database tests and retains coverage for cycle claim/lease, publication
   recovery, due-media wake-up, blocked-image behavior, and idempotent queue
   publication. Investigate failures as implementation defects only after the
   runner has a confirmed reachable target.

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
   Acessórias routing. The internal query API has no rate limiting and uses `/v1/`
   routes; future breaking changes use `/v2/` while `/v1/` remains functional.

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
  isolates their persistent-mode evidence; the PostgreSQL runner remains item 2.
- SPEC-0003, PRD §10, and ARCHITECTURE now record the approved removal of the
  legacy finalization mode; the implementation refactor and replacement tests
  remain delivery work.
- The mounted `/webhook/debug` returns `raw_payload` after HMAC validation;
  the unmounted handler also prints/returns raw headers and body. This conflicts
  with SPEC-0002's general sanitized-response rule, while PRD §8 describes raw
  diagnostic output. Item 5 owns the security/product decision and subsequent
  contract reconciliation.
- The current code search found no TODO/FIXME/stub backlog. Inspected `pass`
  statements are exception or migration control flow, not placeholders.
- There is no CI configuration. `docker-compose.test.yml` is a useful test
  service definition, not an automated or currently portable runner.
- `tests/test_webhook_local.py` is intentionally live/opt-in and remains outside
  the canonical automation unless a local API is deliberately started.
- Item 1 changes test configuration, persistent-cycle coverage, and verification
  documentation only; it changes no application code, migration,
  infrastructure configuration, or production data.

## Recommended next pass

**`build` item 2** — establish the executable disposable PostgreSQL runner and
verify the skipped database families. Product/security decisions in Phase 1–2
need owner input first.
