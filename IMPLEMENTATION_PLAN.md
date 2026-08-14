# Implementation Plan

_Planning baseline: 2026-08-14. Code, Alembic migrations, configuration and
tests take precedence over this plan. Status describes this checkout and
recorded local evidence, never production availability. No milestone is
currently in progress._

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
  owns schema through `0015_acessorias_directory`; migrations, backfills,
  import, and audit utilities are versioned. Application code verifies rather
  than creates schema.
- **[completed] Webhook hardening and supported HTTP surface** (SPEC-0002;
  issue 0006). Production HMAC-before-parse handling remains; raw-payload
  diagnostic routes/modules are removed and focused tests prove both historical
  paths return `404`.
- **[completed] Reproducible local verification** (SPEC-0004; issues 0001,
  0002, 0004, 0011). `scripts/verify.py` owns an isolated PostgreSQL 16 Compose
  target, verifies process connectivity and Alembic head, then runs the
  PostgreSQL-marked family. The last recorded full execution (issue 0011,
  2026-08-14) passed compileall, strict Pyright, offline pytest (**151 passed,
  36 skipped**), Alembic `0015_acessorias_directory`, and PostgreSQL pytest
  (**36 passed, 151 deselected**). The 36 offline skips are expected missing
  `CAI_TEST_DATABASE_URL` prerequisites, not database-runtime evidence.

### Implemented, with bounded verification only

- **[completed | local-only evidence] External integrations and deployment.**
  Redis, DigiSac, Groq, Docker Compose, migrations, and the opt-in live webhook
  test are implemented, but the checked-in runner deliberately substitutes a
  deterministic queue and disposable PostgreSQL. There is no recorded current
  verification against a running Redis deployment, DigiSac/Groq provider,
  replicas, or production target. This is a release-evidence limitation, not a
  code defect or an authorized rollout task.
- **[completed | opt-in] Live webhook test.** `tests/test_webhook_local.py` is
  import-safe, remains outside canonical automation, and requires a separately
  started local API when invoked directly.

### Planning signals

- The current canonical collection contains **187 tests**; the latest issue-0011
  run passed **151 tests** and skipped the 36 PostgreSQL-dependent tests without
  a configured database.
- Targeted TODO/placeholder/stub searches found no implementation backlog.
  Remaining `pass` statements are migration or exception-control flow.
- The canonical runner does not invoke the opt-in live webhook action. A bare
  `PYTHONPATH=/app python -m pytest -q` imports `tests/test_webhook_local.py`
  without opening a socket; direct execution remains the only way to send the
  smoke request.
- Issues 0001–0012 are `closed`. Earlier baseline delivery work is complete and
  must not be reopened.
  Broader classification policy changes remain blocked on product decisions.

## Priority plan

### Approved Acessórias milestones

1. **[P0 | completed locally | implemented] Milestone A —
   Acessórias Directory Foundation** (SPEC-0007).
   Outcome: a separately specified, Alembic-owned local directory of all
   Acessórias companies (active and inactive), their contacts, departments, and
   current company-department relationships, populated only through an explicit
   provider adapter. External IDs remain external identifiers, not CAI business
   enums; PostgreSQL, never Redis, is authoritative.

   Specification outcome: SPEC-0007 defines paging/full and periodic sync,
   operational refresh, raw/normalized identifiers, idempotent upsert/replay,
   retry/timeout/`429`/`5xx` recovery, sanitized observability, deterministic
   fixtures, provider removals/reactivation, and additive migration/backfill
  verification. Its build issue proved those behaviours on disposable
   PostgreSQL. Users sync, public/admin APIs, DigiSac contacts, identity
   matching, and Request creation are excluded.

   Readiness: authorized provider evidence is now recorded in SPEC-0007:
   `https://api.acessorias.com`, HTTP Bearer from secure configuration,
   Departments/Companies read endpoints and observed payloads, `Pagina=N`, and
   the documented 100 requests/minute limit. The contract supplies conservative
   handling for active/inactive composition, no provider `updated_at`, partial
   failure, repeated pages and `429` without `Retry-After`. Credentials and any
   production sync target remain outside this plan; exposed exploration tokens
   must not be used or recorded.

   Build evidence (2026-08-14): migration `0015_acessorias_directory`, typed
   Acessórias adapter, complete-snapshot validation, transactional PostgreSQL
   reconciliation, execution deduplication, bounded retry/throttle handling,
   explicit module CLI, deterministic adapter tests, and disposable PostgreSQL
   tests are implemented. The canonical runner passed compileall, strict
   Pyright, offline pytest (**143 passed, 36 skipped**), Alembic head
   verification, and PostgreSQL pytest (**36 passed, 143 deselected**). This is
   local synthetic/disposable evidence only; no provider credential, Redis,
   deployment, or production synchronization was used.

2. **[P0 | blocked | specified] Milestone B —
   DigiSac Contact Identity Foundation** (SPEC-0008). Outcome: persist the minimal contact representation keyed by
   `contact.id`, with paginated backfill, ticket-webhook upsert, and need-based
   Contacts API hydration. Preserve raw/normalized number, group status,
   relevant provider metadata, and sync state; do not query Contacts for every
   message or use `idFromService` as a matching key.

   Specification outcome: SPEC-0008 defines the provider-evidence gate,
   hydration triggers, replay/idempotency, privacy-safe storage, stale-record
   handling, and disposable-PostgreSQL validation. Its implementation issue is
   blocked until the Contacts evidence and SPEC-0007 dependency are satisfied.
   No automatic company resolution is introduced here.

3. **[P1 | blocked | specified] Milestone C —
   DigiSac ↔ Acessórias Identity Resolution** (SPEC-0009). Outcome: persist technical evidence, contact-company
   links, and conversation/cycle resolution separately, with candidate,
   confirmed, ambiguous, unresolved, and rejected states and many-to-many
   cardinality. Exact unique phone/email creates a candidate only; manual
   database confirmation is the initial operation and groups never participate
   in automatic phone/name matching.

   Specification outcome: SPEC-0009 defines evidence provenance,
   transitions/auditability, manual confirmation, ambiguity/rejection and
   regression validation. Its implementation issue remains blocked until
   SPEC-0007–0008 are delivered and the Brazilian mobile-variant transformation
   is recorded as a testable product decision.

4. **[P1 | blocked | specified] Milestone D — DigiSac Department →
   Acessórias Department Mapping** (SPEC-0010). Outcome: add persistent/configurable
   mapping from the current DigiSac department, validated against the resolved
   company's current departments; `intent_type` is not a mapping input.

   Specification outcome: SPEC-0010 defines durable rules, validation,
   snapshots, concurrency, compatibility and verification. Its issue remains
   blocked until the mapping owner, scope, lifecycle, precedence, definition of
   “current” DigiSac department and authorized operator procedure are recorded.

5. **[P1 | blocked | specified] Milestone E — Durable Acessórias Request
   Creation** (SPEC-0011). Outcome: create a Request only after a persisted valid final
   classification and successful company/department resolution, retaining
   `SolID`, company, department, and originating cycle/classification. A
   separate recoverable idempotency/reconciliation state ensures provider
   failure never invalidates a completed classification.

   Specification outcome: SPEC-0011 defines durable delivery, preconditions,
   retry/reconciliation and provider-double verification. Its issue remains
   blocked until the Request API, authorization, safe idempotency/reconciliation
   mechanism, failure/replay policy and operator workflow are recorded.

6. **[P2 | pending | optional] Milestone F — Request lifecycle
   integration.** Outcome: future status, interactions, comments, attachments,
   responsible users, closure, and reopening work. Completion requires a new
   product decision and specification after Request creation is proven; do not
   specify or implement it before then.

### Specification boundary and next gate

SPEC-0007–SPEC-0011 supply the independently verifiable contracts for
Milestones A–E. Milestone A is implemented locally under issue 0012; the next
evidence gate applies to
Milestone B: it must settle the DigiSac Contacts details listed in SPEC-0008.
Milestone C also needs the testable Brazilian
mobile-variant decision in SPEC-0009; Milestones D and E retain their own
governance and Request-contract blocks. No specification in this sequence
authorizes application changes until its own issues/build pass.

### Separate pending work

- **[P1 | completed locally | implementation] Restore `financial` taxonomy
  parity in the IA prompt** (PRD §6; SPEC-0001; `src/core/intents.py`;
  `src/workers/ia_worker.py`; `tests/test_ia_worker_intent.py`). The prompt now
  names and guides every canonical intent while preserving the four-field
  output contract and current persistence/API semantics. Issue 0010 validation
  passed focused pytest (**16 passed**), offline pytest (**146 passed, 36
  skipped**), compileall, strict Pyright, disposable PostgreSQL/Alembic head,
  and PostgreSQL pytest (**36 passed, 146 deselected**). No migration or
  provider-backed quality claim was made.
- **[P2 | completed locally | implementation] Make the live webhook check safe
  for default test collection** (SPEC-0004; `tests/test_webhook_local.py`;
  `tests/test_webhook_local_boundary.py`; `scripts/verify.py`). Issue 0011
  added a main-guarded direct smoke command, regression coverage, and removed
  stale runner exclusions. Collection is side-effect-free, the live request
  remains opt-in, and the canonical runner stays network-independent. Evidence:
  **187 collected**, **151 passed, 36 skipped** offline, and **36 passed, 151
  deselected** against disposable PostgreSQL 16; the no-API smoke invocation
  exited `1` with a visible connection error. No production endpoint or live
  check was added to CI.
- **[P2 | blocked]** Broader IA classification-policy changes require their own
  product decisions.
- **[P2 | blocked]** Production acceptance requires separately authorized
  environment, credentials, rollout ownership, and acceptance criteria.

## Completed history and superseded work

- **[completed]** Canonical test isolation (issue 0001), disposable PostgreSQL
  runner (0002), documentation baseline (0003), durable recovery coverage
  (0004), legacy finalization removal (0005), raw-payload diagnostic-surface
  removal (0006), and persistent implementation documentation reconciliation
  (0007), generated OpenAPI HTTP contract publication (0008), active document
  traceability/evidence reconciliation (0009), and financial taxonomy prompt
  parity (0010).
- **[superseded]** Any prior plan item proposing PRD/architecture/spec work
  already completed by the existing artifacts, legacy-finalization removal,
  diagnostic-route removal, fixed-port test Compose work, or broader database
  recovery coverage.
- **[non-work]** Automatic retention/archival, query authentication/rate
  limiting, mounted `/v1/` aliases, hosted CI, and provider/model replacement
  are not implied by the current requirements. Acessórias directory,
  resolution, routing, and Request creation are approved work with the
  dependency order recorded above.

## Dependencies, risks, and recorded discrepancies

- **Documentation inconsistency (resolved by issue 0007):** README,
  PRD, architecture, SPEC-0002, SPEC-0004, the index, and this plan now state
  persistent-only finalization, unversioned mounted queries, and future-only
  `/v1/`/`/v2/` policy.
- **Documentation drift (resolved by issue 0009):** active PRD, architecture,
  README, SPEC-0001–0006, the index, and this plan now use stable
  SPEC/issue references, distinguish the implemented OpenAPI and Acessórias
  foundation, and record the issue-0012 baseline (**143/36** offline and
  **36/143** disposable PostgreSQL). The issue-0007 (**122/33**, **33/122**)
  and issue-0008 (**127/33**, **33/127**) results remain dated historical
  evidence. All results remain local and do not prove Redis, providers,
  replicas, deployment, or production readiness.
- **Taxonomy parity defect (resolved by issue 0010):** `financial` appears in
  PRD §6, `VALID_INTENT_TYPES`, validation, persistence, and OpenAPI, and is
  now present in the model prompt's allowed list and bounded guidance. The
  correction does not decide broader taxonomy semantics.
- **Classification-policy boundary:** precedence expansion,
  confidence semantics, summary invariants, and structured-description choices
  are not approved product requirements. This work is blocked until they are
  decided; preserve the four-field output contract in the meantime.
- **Traceability drift (resolved by issue 0009):** active PRD/architecture/
  README text no longer relies on obsolete Phase/item numbers from the prior
  plan structure; historical issue references remain as evidence.
- **External-runtime boundary:** local disposable verification is
  intentionally insufficient to claim provider, Redis, replica, or production
  readiness. The limitation affects only a future deployment acceptance task.
- **HTTP documentation boundary (resolved by issue 0008):** the
  generated document describes current response projections without adding
  response enforcement. The `processing` source enum discrepancy and unmapped
  Redis failures remain documented limitations rather than new HTTP behavior.
- **Acessórias foundation boundary:** the initial directory sync is implemented
  with an additive Alembic-owned schema, secure runtime configuration, a
  dedicated provider boundary, and disposable PostgreSQL evidence. No real
  provider credential or production synchronization was authorized; Redis
  cannot become the directory authority.
- **Test-entrypoint discrepancy (resolved by issue 0011):** SPEC-0004 defines
  `scripts/verify.py` as canonical and keeps the live webhook action opt-in.
  `tests/test_webhook_local.py` is now safe to import and the runner no longer
  needs a path exclusion; direct smoke output remains separate from canonical
  offline/PostgreSQL evidence.

## Recommended next pass

No open issue remains in the current issue set. Future work is gated by the
listed Milestone B–E dependencies and decisions.
