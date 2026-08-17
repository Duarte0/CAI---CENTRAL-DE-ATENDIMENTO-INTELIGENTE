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
- **[completed] Groq parser logging privacy** (SPEC-0001; issue 0024). Wrapped
  and invalid classification-response diagnostics retain only safe outcome
  categories and bounded structural metadata; raw/partial model output is not
  logged. Parser recovery, validation, retry/dead-letter behavior, and
  classification persistence remain unchanged.
- **[completed] Webhook extraction logging privacy** (SPEC-0001/0002; issue
  0025). Normal parser diagnostics retain only safe event, presence/type, and
  source metadata; extracted message/contact values, URLs, secrets, and raw
  bodies are not logged. Payload extraction, HMAC ordering, ignored-event
  behavior, and downstream processing remain unchanged.
- **[completed] Persistent-only finalization** (PRD §5.4; SPEC-0003;
  issue 0005). The feature flag, Redis buffer/debounce, legacy worker branch,
  API fallbacks, models, and legacy tests were removed. Targeted search finds
  no active legacy code or setting.
- **[completed] Durable schema and migration foundation** (SPEC-0001). Alembic
  owns schema through `0019_acessorias_request_creation`; migrations, backfills,
  import, and audit utilities are versioned. Application code verifies rather
  than creates schema.
- **[completed] Webhook hardening and supported HTTP surface** (SPEC-0002;
  issue 0006). Production HMAC-before-parse handling remains; raw-payload
  diagnostic routes/modules are removed and focused tests prove both historical
  paths return `404`.
- **[completed] Reproducible local verification** (SPEC-0004; issues 0001,
  0002, 0004, 0011, 0013, 0014, 0015, 0016, 0017, 0018). `scripts/verify.py` owns an isolated PostgreSQL 16 Compose
  target, verifies process connectivity and Alembic head, then runs the
  PostgreSQL-marked family. The latest recorded full execution (issue 0018,
  2026-08-17) passed compileall, strict Pyright, offline pytest (**193 passed,
  61 skipped**), Alembic `0019_acessorias_request_creation`, and PostgreSQL pytest
  (**61 passed, 193 deselected**). The 61 offline skips are expected missing
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

- The current canonical collection contains **233 tests**; the latest issue-0016
  run passed **177 tests** and skipped the 56 PostgreSQL-dependent tests without
  a configured database.
- Targeted TODO/placeholder/stub searches found no implementation backlog.
  Remaining `pass` statements are migration or exception-control flow.
- The canonical runner does not invoke the opt-in live webhook action. A bare
  `PYTHONPATH=/app python -m pytest -q` imports `tests/test_webhook_local.py`
  without opening a socket; direct execution remains the only way to send the
  smoke request.
- Issues 0001–0016 are `closed`. Earlier baseline delivery work is complete and
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

2. **[P0 | completed locally | implemented] Milestone B —
   DigiSac Contact Identity Foundation** (SPEC-0008). Outcome: persist the minimal contact representation keyed by
   `contact.id`, with ticket-webhook upsert, need-based Contacts API hydration
   and full backfill with single-page optimization and paginated fallback.
   Preserve
   raw/normalized number, group status,
   relevant provider metadata, and sync state; do not query Contacts for every
   message or use `idFromService` as a matching key.

   Specification outcome: SPEC-0008 records the observed `/api/v1` Contacts
   surface, configured Bearer authority, contact identity/payloads, ticket
   snapshot source, hydration strategy, source precedence, groups and safe
   observability. Issue 0013 implements the migration/model, ticket-webhook
   upsert and deduplicated individual hydration. Authorized provider evidence
   validates high `perPage`, advancement by `page=N`, and termination from
   `currentPage`/`lastPage`; it permits a one-page fetch for the current tenant
   but requires a paginated fallback, global `contact.id` deduplication, and
   failure on invalid/non-advancing pages. Issue 0014 implements the remaining
   full-backfill execution without automatic company resolution.

   Build evidence (2026-08-14): migration `0016_digisac_contact_identity`,
   timestamp-aware contact upsert, individual Contacts retry boundary,
   durable hydration claims/recovery, ticket/message webhook integration, and
   deterministic unit/PostgreSQL coverage are implemented. Issue 0014 adds the
   typed Contacts page boundary, global `contact.id` deduplication, atomic
   publication with transaction locking, and an internal CLI. The canonical
   runner passed compileall, strict Pyright, offline pytest (**169 passed, 42
   skipped**), Alembic head verification, and PostgreSQL pytest (**42 passed,
   169 deselected**). This is local synthetic/disposable evidence only; no
   DigiSac credential, provider synchronization, Redis runtime, deployment, or
   production claim was used.

3. **[P1 | completed locally | implemented] Milestone C —
   DigiSac ↔ Acessórias Identity Resolution** (SPEC-0009). Outcome: persist technical evidence, contact-company
   links, and conversation/cycle resolution separately, with candidate,
   confirmed, ambiguous, unresolved, and rejected states and many-to-many
   cardinality. Exact unique phone/email creates a candidate only; manual
   database confirmation is the initial operation and groups never participate
   in automatic phone/name matching.

   Specification outcome: SPEC-0009 v1.1 defines evidence provenance,
   conservative exact phone/email and Brazilian mobile-variant candidates,
   transitions/auditability, manual database confirmation, ambiguity/conflict
   handling and regression validation. A phone/email evidence or combination of
   evidence never confirms automatically. The approved `manual_db` procedure
   requires a confirmation timestamp and makes the actor optional until a
   trustworthy administrative identity exists. Its implementation issue is
   eligible once the declared dependencies are implemented; the Brazilian
   mobile-variant and manual-actor decisions were implemented as specified.

   Build evidence (2026-08-14): migration `0017_digisac_acessorias_identity`,
   typed deterministic matcher, PostgreSQL evidence/link/transition
   persistence, immutable cycle resolution, manual confirmation/correction,
   and deterministic unit/PostgreSQL coverage are implemented. The canonical
   runner passed compileall, strict Pyright, offline pytest (**175 passed,
   48 skipped**), Alembic head verification, and PostgreSQL pytest (**48
   passed, 175 deselected**). This is local synthetic/disposable evidence only;
   no provider, Redis, deployment, or production claim was made.

4. **[P1 | completed locally | implemented] Milestone D — DigiSac Department →
   Acessórias Department Mapping** (SPEC-0010). Outcome: add persistent/configurable
   mapping from the current DigiSac department, validated against the resolved
   company's current departments; `intent_type` is not a mapping input.

   Specification outcome: SPEC-0010 v1.1 records approved global mapping by
   stable provider IDs, a single active mapping per DigiSac department, explicit
   many-to-one mappings to Acessórias departments, active/inactive lifecycle,
   and initial `manual_db` administration with no required actor, UI, or HTTP
   endpoint. It validates only the mapped department against the resolved
   company's current `company_departments`; no name, IA, `intent_type`, owner,
   historical Request, or default-department fallback is allowed. Issue 0016
   implements the Alembic `0018_department_mapping` state, transactional
   `manual_db` rule administration, stable-ID evaluation, and append-only cycle
   snapshots.

   Build evidence (2026-08-14): the disposable runner passed compileall, strict
   Pyright, offline pytest (**177 passed, 56 skipped**), Alembic head
   verification, and PostgreSQL pytest (**56 passed, 177 deselected**). Tests
   cover stable-ID lifecycle/audit, many-to-one mappings, replay/concurrency,
   confirmed identity and current relationship validation, rollback, privacy,
   rename stability, and immutable/later cycle evaluations. This is local
   synthetic/disposable evidence only; no provider, Redis, deployment, or
   production claim was made.

5. **[P1 | completed locally | implemented] Milestone E — Durable Acessórias Request
   Creation** (SPEC-0011). Outcome: create a Request only after a persisted valid final
   classification and successful company/department resolution, retaining
   `SolID`, company, department, and originating cycle/classification. A
   separate recoverable idempotency/reconciliation state ensures provider
   failure never invalidates a completed classification.

   Specification outcome: SPEC-0011 v1.1 records the authorized multipart
   `POST https://api.acessorias.com/requests`, Bearer boundary, external
   `tipo=E`, provider fields and success `id`/`SolID` contract. It centralizes
   priority `2`, preserves a single durable operation per cycle, and requires
   conservative retry: no invented idempotency key and no automatic retry when
   the POST outcome is uncertain. Only an explicit provider-boundary proof of
   pre-send failure can enter the bounded retry path; ordinary connection,
   timeout, and protocol failures require `manual_db` reconciliation. Issue
   0017 implements the operation and issue 0018 closes this transport gap after
   Milestones A–D supply the declared classification, company and department
   facts; lifecycle work remains Milestone F.

   Build evidence (2026-08-17): the disposable runner passed compileall, strict
   Pyright, offline pytest (**192 passed, 61 skipped**), Alembic head
   `0019_acessorias_request_creation`, and PostgreSQL pytest (**61 passed,
   192 deselected**). Tests cover exact multipart fields, durable operation
   uniqueness, terminal eligibility, safe retry, uncertain outcomes, claims,
   manual reconciliation/release, concurrency, rollback guards, and privacy.
   This is local synthetic/disposable evidence only; no provider credential,
   Redis, deployment, or production claim was made.
   Issue 0018 additionally verifies the explicit pre-send retry marker,
   ambiguous connection/timeout/protocol outcomes with one provider call, and
   durable replay/concurrency without a second POST; its focused offline result
   is recorded in the issue and SPEC-0011.

6. **[P2 | pending | optional] Milestone F — Request lifecycle
   integration.** Outcome: future status, interactions, comments, attachments,
   responsible users, closure, and reopening work. Completion requires a new
   product decision and specification after Request creation is proven; do not
   specify or implement it before then.

### Specification boundary and next gate

SPEC-0007–SPEC-0011 supply the independently verifiable contracts for
Milestones A–E. Milestone A is implemented locally under issue 0012. Milestone
B's incremental slice is implemented locally under issue 0013, and its
full Contacts-backfill slice is implemented locally under issue 0014. Authorized
provider evidence covers single-page execution and the `page=N` fallback; local
tests cover the typed boundary, validation, deduplication, and failure-safe
publication.
Milestone C has its testable Brazilian mobile-variant rule and controlled
manual-confirmation procedure recorded in SPEC-0009 and was implemented under
issue 0015. Milestone D is implemented under issue 0016; Milestone E is
implemented under issues 0017–0018 with its Request contract and A–D facts
available. No specification in this sequence authorizes application changes
until its own issues/build pass.

The documentation drift around the issue-0014 backfill and verification counts
was reconciled during the issue-0015 build sync, and the issue-0016/0017/0018
build sync records department mapping and the corrected Request transport
boundary: README, PRD, architecture, SPEC-0008,
SPEC-0009, SPEC-0010, the specs index, and this plan record the implemented
backfill, identity resolution, mapping, Request creation, Alembic `0019`, and the latest local
runner evidence. These results remain disposable/local and do not imply provider,
Redis, deployment, or production readiness.

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
  traceability/evidence reconciliation (0009), financial taxonomy prompt
  parity (0010), Groq parser logging privacy (0024), and webhook extraction
  logging privacy (0025).
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
- **Groq parser privacy defect (resolved by issue 0024):** `_parse_result()` no
  longer logs raw or preview response content when recovering wrapped JSON or
  rejecting invalid output. Tests assert that title, description, reasoning,
  and unique malformed-response sentinels remain absent while safe outcome
  metadata remains observable.
- **Webhook extraction privacy defect (resolved by issue 0025):**
  `WebhookPayload.extraction_debug()` now returns only bounded presence/type/
  source metadata, and adjacent parser logs sanitize event, origin, and
  message-type values. Direct and nested customer-content sentinels remain
  absent from captured logs while ignored-event and HMAC behavior remain
  unchanged.
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

Milestones C and D are complete under issues 0015 and 0016, and Milestone E is
implemented under issues 0017–0018. Milestone F remains the next optional increment
and requires a new product decision and specification.
