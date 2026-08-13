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
  PostgreSQL-marked family. The last recorded full execution (issue 0008,
  2026-08-13) passed compileall, strict Pyright, offline pytest (**127 passed,
  33 skipped**), Alembic `0014_retry_scheduling`, and PostgreSQL pytest
  (**33 passed, 127 deselected**). The 33 offline skips are expected missing
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
- All eight implementation issues are `closed`. Earlier Phase 0/1 delivery
  work is complete and must not be reopened. Two concrete P1 follow-ups remain:
  reconcile stale active-document traceability/evidence, then correct the
  `financial` taxonomy omission in the model prompt. Broader classification
  policy changes remain blocked on product decisions.

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
   reconciliation and is implemented as v1.1. SPEC-0006 was subsequently
   implemented as v1.1 by issue 0008. This item is complete; no application
   behavior changed.

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

### Phase 2 — Documentation reconciliation (ready to specify)

3. **[P1 | pending | documentation/specification] Reconcile stale
   traceability, status language, and verification evidence** (PRD §9;
   ARCHITECTURE §13; SPEC-0001–0006).

   Outcome: active documentation refers to stable specifications, issues, and
   delivery outcomes rather than obsolete plan-item numbers, and accurately
   marks the implemented OpenAPI and latest recorded verification baseline.
   Historical issue references and their original evidence remain intact.

   Completion criteria:

   - [ ] replace obsolete plan-item references in the README, PRD, architecture,
     and active SPEC-0001–0006 with stable SPEC/issue references or descriptive
     completed outcomes;
   - [ ] update SPEC-0004, SPEC-0006, and the specification index so their
     status and narrative distinguish completed runner/OpenAPI delivery from
     future work; remove SPEC-0006's now-stale claim that generated OpenAPI
     lacks `servers`, security schemes, and response schemas;
   - [ ] reconcile active-document verification counts to the issue-0008
     baseline (**127 passed, 33 skipped** offline; **33 passed, 127
     deselected** PostgreSQL), retaining **122/33** and **33/122** only as
     explicitly dated issue-0007 historical evidence; and
   - [ ] recheck active-document cross-references and the spec index, preserving
     the distinction between completed delivery, local-only verification, and
     future work.

   Dependencies: none. Risk: stale links misstate ownership and can reopen
   completed work. This is documentation-only; it does not change requirements,
   code, tests, migrations, infrastructure, or historical issue records.

### Phase 3 — Contract parity and decision-gated evolution

4. **[P1 | pending | implementation] Restore canonical `intent_type` parity
   in the model prompt** (PRD §6; SPEC-0001; `src/core/intents.py`;
   `src/workers/ia_worker.py`; `tests/test_ia_worker_intent.py`).

   Outcome: every taxonomy value accepted by normalization/persistence and
   published in the PRD can be produced deliberately by the model. The current
   direct mismatch is that `financial` is canonical but absent from both the
   prompt's allowed-value list and its classification guidance.

   Completion criteria:

   - [ ] update the prompt's allowed-value list and guidance to include
     `financial`, without changing the four-field output shape, persistence
     schema, HTTP projection, or existing precedence rules;
   - [ ] add focused prompt/normalization tests that detect divergence between
     the prompt and `VALID_INTENT_TYPES`, including `financial`; and
   - [ ] run the applicable offline suite, strict Pyright, and canonical runner
     when the issue changes the test matrix; record external-provider evidence
     separately if it is not run.

   Dependencies: the existing PRD/SPEC-0001 canonical taxonomy; schedule after
   Phase 2 so the updated contract is referenced consistently. Risk: no
   provider-backed quality evidence exists, so this item restores contract
   parity but does not claim classification accuracy. No migration or
   infrastructure change is expected.

5. **[P2 | blocked | product decision] Define any broader AI classification
   quality contract before changing behavior** (PRD §6; SPEC-0001;
   `src/workers/ia_worker.py`).

   Outcome: a dedicated specification can make future quality work testable
   without silently changing business classification behavior.

   Decisions required: whether to expand or rank taxonomy precedence beyond
   the current prompt, whether confidence needs a different semantic or format,
   which summary facts and speaker distinctions are mandatory, and whether
   `description` remains model-formatted text or becomes application-formatted
   or structured output. The current four-field output contract remains in
   force until these decisions are recorded.

   Dependencies: product owner decision and a dedicated specification before an
   implementation issue. This item does not authorize prompt, schema, API, or
   persisted-output changes.

### Phase 4 — Conditional release/production evidence (not ready to build)

6. **[P2 | blocked | decision/operations] Define and authorize a production
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
- **[superseded]** Any prior plan item proposing PRD/architecture/spec work
  already completed by the existing artifacts, legacy-finalization removal,
  diagnostic-route removal, fixed-port test Compose work, or broader database
  recovery coverage. The Phase 2 documentation repair and Phase 3 taxonomy
  parity correction remain required before their respective follow-on work.
- **[non-work]** Automatic retention/archival, query authentication/rate
  limiting, mounted `/v1/` aliases, hosted CI, provider/model replacement, and
  Acessórias routing are not implied by the current requirements. They require
  a future approved product/spec increment.

## Dependencies, risks, and recorded discrepancies

- **Documentation inconsistency (Phase 1, resolved by issue 0007):** README,
  PRD, architecture, SPEC-0002, SPEC-0004, the index, and this plan now state
  persistent-only finalization, unversioned mounted queries, and future-only
  `/v1/`/`/v2/` policy.
- **Documentation drift (Phase 2):** PRD §9, ARCHITECTURE §13, SPEC-0001–0006,
  and the index still contain obsolete plan-item references; some retain the
  issue-0007 **122/33** and **33/122** counts or SPEC-0006 future-tense gap
  language despite issue 0008. Item 3 repairs those records without changing
  historical issue files. All such evidence remains local and does not prove
  Redis, provider, replica, deployment, or production readiness.
- **Taxonomy parity defect (Phase 3):** `financial` appears in PRD §6,
  `VALID_INTENT_TYPES`, validation, persistence, and OpenAPI, but is omitted
  from the model prompt's allowed list and guidance. Item 4 is a bounded
  implementation correction; it does not decide broader taxonomy semantics.
- **Classification-policy boundary (Phase 3):** precedence expansion,
  confidence semantics, summary invariants, and structured-description choices
  are not approved product requirements. Item 5 is blocked until they are
  decided; preserve the four-field output contract in the meantime.
- **Traceability drift (Phase 2):** active PRD/architecture/specification text
  still cites obsolete Phase/item numbers from the prior plan structure. This is
  stale documentation, not an implementation gap; historical issue references
  must remain as evidence.
- **External-runtime boundary (Phase 4):** local disposable verification is
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

The next pass should be **specs**: complete the bounded documentation
reconciliation in item 3, preserving historical issue evidence. Then use the
**issues** pass to create the focused Phase 3 taxonomy-parity issue. Do not
create an issue for broader classification-policy changes until item 5's
product decisions are recorded. Phase 4 remains blocked on separately
authorized operational scope.
