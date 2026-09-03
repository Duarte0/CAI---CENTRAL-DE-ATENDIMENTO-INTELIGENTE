# Implementation Plan

_Planning baseline: 2026-09-03. Source, Alembic revisions, configuration, and
tests describe the current checkout; this plan records sequencing and local
evidence, never production availability. The SPEC-0013 Phase 2 delivery is
complete locally; production acceptance remains separate._

## Evidence-based current state

### Completed and verified locally

- **[completed] Core durable CAI workflow** (PRD §§5–8; SPEC-0001–0004).
  FastAPI webhook ingestion, PostgreSQL-backed cycles, persistent DigiSac
  history reconstruction, Groq classification, PostgreSQL polling/lease for IA
  finalization, with historical Redis access isolated to maintenance, and
  separate durable audio/image processing are implemented. PostgreSQL state is
  reserved before media transport; IA finalization, audio transcription and
  image extraction are claimed by PostgreSQL polling/lease. Lease, due-retry,
  reconciliation, and idempotency paths recover interrupted work. Terminal image
  failure blocks only dependent cycles; terminal audio failure also remains
  blocked by the media gate.
- **[completed] Persistent-only finalization and privacy hardening**
  (SPEC-0001–0003; issues 0005, 0024, 0025, 0048). The legacy Redis
  buffer/debounce mode and raw-payload diagnostic routes are removed. Parser
  and webhook diagnostics retain bounded metadata without raw model output,
  extracted customer values, secrets, or payload bodies. Issue 0048 also
  removed `ia_queue`/`ia_dead_letter` from the active IA path: due work is
  claimed directly in PostgreSQL, while a bounded manual inventory retires only
  validated legacy queue entries.
- **[completed] Durable schema and recovery foundations** (SPEC-0001, 0003;
  issues 0004, 0027–0033, 0037, 0049–0053). Alembic owns the schema through
  `0025_webhook_event_keys`; persistence boundaries for contacts, cycles,
  assignments, media, classifications, and DigiSac directory state are isolated
  behind repositories. Audio transient retry parity, PostgreSQL polling/lease,
  and bounded legacy-list cutover for audio and image are implemented with
  recovery coverage. Revision `0024_durable_media_leases` supplies the shared
  media lease columns and indexes; issues 0050 and 0051 required no additional
  migration.
- **[completed] Acessórias directory through Request creation**
  (SPEC-0007–0011; issues 0012–0022, 0026, 0034, 0036). Directory sync,
  canonical ticket `contact.id`, conservative identity candidates/manual
  confirmation, cycle-scoped department mapping, and one durable Request
  operation per eligible cycle are implemented. Identity and mapping prepare
  before a provider call; missing or ambiguous facts fail closed. A second POST
  is allowed only after durable proof of pre-send failure; uncertain transport
  outcomes, including unproven `429`, remain in reconciliation.
- **[completed] Authenticated identity administration** (SPEC-0012; issues
  0038–0040). The internal `/admin/acessorias` API provides sanitized triage,
  contact detail, company search, idempotent confirm/reject, and deterministic
  rediscovery. It uses `ADMIN_API_TOKEN`, PostgreSQL command idempotency, and
  Alembic `0021`/`0022`; it neither calls providers nor changes historical
  cycle resolution or Request state.
- **[historical | 2026-08-21] Offline verification.**
  `PYTHONPATH=/app python -m pytest -q` passed **238** and skipped **76**.
  The skips were the intentionally unconfigured `CAI_TEST_DATABASE_URL`
  PostgreSQL family. The opt-in local webhook smoke remains import-safe and
  outside canonical automation.
- **[completed | current checkout | 2026-09-02] Issue 0049 verification.** The
  full offline suite passed **273 passed, 82 skipped**. A disposable PostgreSQL
  database applied Alembic head `0024_durable_media_leases` and passed **19
  focused tests** for atomic audio claims, due scheduling, stale leases,
  lease-owner completion and existing image recovery. Compileall and diff
  checks also passed; this is local evidence, not provider or production
  acceptance.
- **[completed | current checkout | 2026-09-02] Issue 0050 verification.** The
  full offline suite passed **280 passed, 84 skipped**. A disposable PostgreSQL
  database applied Alembic head `0024_durable_media_leases` and passed **34
  focused tests** covering atomic image claims, due scheduling, lease ownership,
  retry persistence, media gating, webhook/IA admission and the audio/image
  regression families. Compileall and diff checks also passed. Legacy image
  lists were not deleted or replayed; the bounded inventory/apply script is
  explicit and dry-run by default. This is local evidence, not provider or
  production acceptance.
- **[completed | current checkout | 2026-09-03] Issue 0052 maintenance
  implementation.** The dedicated Docker `maintenance` target/profile now
  contains the bounded scripts without expanding the `api` image. The
  coordinator requires a pinned revision, operator, protected connection
  context, archived dry-run report and approved PostgreSQL recovery point;
  apply is one family at a time, rechecks runtime/durable invariants and exact
  list-value digests, and performs only validated one-at-a-time `LREM`. Local
  focused script/coordinator coverage passed **15 tests**. The controlled `cai`
  runtime procedure then removed 17,164 IA entries, 71 image entries and 0
  audio entries with a reviewed recovery point; its final dry-run found all
  six lists empty while durable totals remained intact. This is acceptance
  evidence for that named runtime, not a claim about other deployments.
- **[completed | current checkout | 2026-09-03] Issue 0053 verification and
  cutover.** Generic webhook idempotency now uses the atomic PostgreSQL ledger
  `webhook_event_keys` from Alembic `0025_webhook_event_keys`; the digest and
  one-hour expiry contract are unchanged, and the active service contains no
  Redis dependency. Concurrent acceptance, expiry replacement, bounded cleanup,
  fail-closed database behavior, media ordering, route compatibility and
  report-bound legacy-marker handoff are covered by focused tests. The named
  `cai` runtime was stopped at the old/new boundary, backed up, migrated,
  imported live `processed:*` markers without source deletion, and restarted
  with the PostgreSQL-only decision path. Exact counts and recovery-point
  references are recorded in issue 0053. The canonical runner passed **290
  passed, 90 skipped** offline and **90 passed, 290 deselected** on PostgreSQL
  16 with head `0025_webhook_event_keys`; the named runtime imported 171 live
  markers and retained all 171 Redis source keys. This is named-runtime
  evidence, not a production-wide claim.
- **[implemented | observation pending | current checkout | 2026-09-03] Issue
  0054 IA Redis compatibility sunset.** `ia_worker` now persists and exposes
  status/result only through PostgreSQL, with no Redis client, `SET`, or
  `RESULT_TTL_SECONDS` wiring. Public routes remain unchanged; OpenAPI and
  worker regression coverage make the dependency boundary explicit. The
  maintenance-only `scripts.retire_ia_redis_compatibility` command inventories
  only `ia_status:*`/`ia_result:*`, records sanitized TTL/value digests and
  durable matches, and requires a full 86400-second observation window plus an
  explicit historical decision before deletion. In the named `cai` runtime,
  the dry-run found 80 keys in each family and 80 durable result matches; both
  counts stayed at 80 after 30 seconds. The implementation is deployed, but
  the issue remains open until the full window and bounded apply are verified.
- **[completed | current checkout | 2026-09-03] Issue 0055 Redis-free
  application runtime.** API, webhook admission, health, durable queue metrics
  and IA worker no longer import, initialize, ping or require Redis. The six
  legacy `/queues` fields were removed explicitly instead of fabricated as
  zero. Runtime requirements and `.env.example` no longer carry Redis settings;
  the client and historical commands use only the separate `maintenance`
  image/profile with explicit `MAINTENANCE_REDIS_URL`. Compose no longer
  defines the Redis service/volume or API/worker dependency, while the retained
  Docker container/storage was not deleted. Focused source, route, OpenAPI,
  dependency and Compose guards plus the Redis-free named runtime smoke prove
  the boundary locally; this is not production-wide acceptance.

### Implemented with bounded evidence

- **[completed | local-only evidence]** Provider integrations, historical Redis
  maintenance boundary,
  Docker deployment, secret-manager provisioning, and production acceptance.
  Source and local/disposable tests prove contracts, not current provider
  behavior, multi-process rate limits, deployed Redis, credentials, replicas,
  or production data.
- **[completed | documentation synchronized]** Issue 0041 reconciled PRD §9
  and source traceability, ARCHITECTURE §13 and source map, README, the active
  specifications, and this plan with the 2026-08-21 Alembic `0022` / `238+76`
  local baseline and the complete six-operation SPEC-0012 internal surface.
  Older `0020`/`203+68` results remain dated historical evidence only; no local
  or disposable result is production/provider/Redis acceptance.

- **[completed | current checkout | 2026-09-03] Issue 0051 verification.**
  Normal repeated contact references now return a sanitized decision under the
  PostgreSQL contact-row lock. Pending/running/succeeded rows remain no-op;
  failed rows with a future `next_attempt_at` retain the exact schedule, and
  due failures remain for the hydration poller. The existing boolean request
  facade remains compatible, no Redis state or migration was added, and the
  focused identity tests cover concurrent preservation and due handoff. This
  is local evidence, not provider or production acceptance. The canonical
  runner passed compileall, Pyright, **280 passed, 86 skipped** offline and
  **86 passed, 280 deselected** in PostgreSQL with head
  `0024_durable_media_leases` under `APP_TIMEZONE=UTC`; the override isolates
  the known timezone discrepancy in `test_department_mapping.py`.

### Approved follow-up backlog

- **[open | staged operational decommission]** Redis cleanup and final storage
  disposal (issues 0054 and 0056; issues 0052–0053 and 0055 completed). Issue 0052 retired only fully
  inventoried legacy IA, audio and image queue entries; issue 0053 moved generic webhook idempotency
  from Redis to a PostgreSQL ledger; issue 0054 stops IA status/result
  compatibility writes and starts their required sunset observation; issue 0055 removed Redis from the application runtime
  and Compose; issue 0056 disposes the retained Redis volume only after an
  explicit observation window and backup review. The sequence preserves
  retained `processed:*`, durable PostgreSQL state, historical recovery tooling and
  rollback boundaries until each dedicated issue closes.
- **[completed]** Targeted searches found no active TODO/FIXME/stub or
  skipped/flaky-test marker that represents approved missing behavior. The
  `pass` occurrences are exception-control flow; the current 86 skips are the
  explicit database prerequisite policy.
- **[completed]** Issue 0050 migrated image extraction to PostgreSQL polling and
  left only bounded, manual visibility of legacy Redis lists. Issue 0051
  preserves contact hydration backoff; it is already DB-only and is not part
  of queue removal.
- **[superseded/deprecated]** Issue 0023 is deprecated: its active/inactive
  directory concern was superseded by the later directory-contract alignment,
  not an open duplicate build item. Issues 0001–0022, 0024–0041 and 0048–0051
  are closed.

## Priority plan

### Phase 1 — Traceability reconciliation

1. **[P1 | completed | documentation] Reconcile current implementation evidence
   across active documents.**

   Outcome: PRD, architecture, README, specifications index, and this plan
   distinguish the current `0022`/`238+76` local baseline from historical
   `0020`/`203+68` evidence, and consistently describe the complete
   SPEC-0012 read/command/discovery surface.

   Completion criteria: inspect the current source, Alembic head, OpenAPI, and
   canonical runner; update only statements that are stale; retain older counts
   as dated history where useful; do not claim production verification.

   Dependencies and risks: no code or migration change. The reconciliation
   must not silently promote local/disposable evidence into provider or
   production acceptance. Related: SPEC-0004, SPEC-0005 v1.4, SPEC-0006,
   SPEC-0012, issues 0038–0040. This specification delta is complete in issue
   0041.

   Completed by issue 0041 on 2026-08-21. No application, test, migration,
   configuration, infrastructure, provider, Redis, or production state changed.

### Phase 2 — Decompose the approved administrative UI

2. **[P1 | completed locally | shell/session/read/action increments completed]
   Identity-review UI** (`SPEC-0013`, Milestone C.2).

   Outcome: issue 0042 implements the local shell, login/logout, fixed signed
   session, and same-process FastAPI BFF boundary. Issue 0043 implements the
   queue, detail, company-search read model, and session-authenticated read
   bridge. Issue 0044 implements explicit confirmation, rejection, and
   deterministic discovery actions through the same BFF and existing durable
   SPEC-0012 command boundary; no second identity authority is introduced.

   The approved contract is:

   - HTML served by FastAPI, local CSS, modular JavaScript without a required
     bundler, and `fetch` for SPEC-0012 routes; Jinja2 only if server rendering
     is needed, with no React/Vite at this stage;
   - an in-process BFF in the same FastAPI process with a signed `HttpOnly`
     cookie session, `Secure` in production, `SameSite=Strict`, fixed expiry of
     60 minutes, and no sliding window; signing via Starlette
     `SessionMiddleware` or `itsdangerous`;
   - `SameSite=Strict` as the complete CSRF protection for this version, with
     no additional CSRF token;
   - secure provisioning of `ADMIN_API_TOKEN`, `ADMIN_UI_PASSWORD`, and
     `ADMIN_SESSION_SECRET` with the same level of care for all three, and no
     exposure to the browser, logs, metrics, or cache;
   - a single operator-password login compared securely with
     `ADMIN_UI_PASSWORD`, with no user registration, RBAC, or IdP.

   Issue 0042 delivered login/logout, no-store responses, the protected local
   shell, API-only data flow, and the reusable session/BFF context. Issue 0043
   delivered the local responsive read view, opaque queue pagination, sanitized
   contact detail, active-company search, guarded stale responses, and safe
   read error states. Issue 0044 delivered fixed-reason action paths,
   explicit confirmation, transient idempotency-key retry, safe command error
   states, and post-command queue/detail refresh.

   Dependencies and risks: SPEC-0012 is implemented. Secure provisioning of the
   three administrative credentials remains an operational prerequisite, and
   production acceptance remains a separate blocked gate.

### Phase 3 — Product and release gates

3. **[P2 | blocked | product decision] Request lifecycle integration**
   (Milestone F).

   Outcome: define any Request status polling, interactions, comments,
   attachments, responsibility, closure, or reopening only after a product
   decision and a new specification. Completion criteria: approved behavior,
   provider contract, ownership, data/retry/reconciliation rules, and an issue
   decomposition. Current Request creation does not authorize this work.

4. **[P2 | blocked | product decision] Broader IA classification policy.**

   Outcome: decide any change to taxonomy precedence, confidence semantics,
   summary invariants, or structured descriptions before changing the
   four-field contract. Completion criteria: PRD/spec decision plus tests and
   migration assessment. Until then retain the implemented contract.

5. **[P1 | blocked | operational authorization] Production acceptance.**

   Outcome: separately authorized acceptance against the intended deployment.
   Completion criteria: named environment and owner, protected credentials,
   rollout/rollback policy, migration and backup approval, provider/Redis
   checks, and explicit acceptance criteria. This cannot be inferred from the
   local runner and must not be bundled with feature build work.

### Phase 6 — Redis cleanup and decommission

6. **[P1 | in progress | issues 0054–0056] Remove legacy Redis work residue, migrate
   the remaining transient contracts, and dispose storage only after a
   controlled observation window.**

   Sequence:

   - issue 0052 is completed: its `maintenance` image/profile and coordinator
     retired the fully inventoried IA, audio and image queue/dead-letter
     entries without replay or PostgreSQL mutation. Apply was report-bound,
     digest-checked, family-scoped and confirmation-gated;
   - issue 0053 is completed: it replaces Redis webhook idempotency with an
     expiring, concurrency-safe PostgreSQL ledger and coordinates the
     `processed:*` handoff before the new API starts;
   - issue 0054 is implemented: the IA worker no longer depends on Redis or
     writes `ia_status:*`/`ia_result:*`; the maintenance report, historical
     disposition and full TTL observation must finish before its bounded apply;
   - issue 0055 is completed: API/IA runtime, health, queue observability,
     dependencies and Compose are Redis-free while the retained storage remains
     outside the application topology for rollback;
   - issue 0056 permanently disposes the exact Redis container/storage target
     only after Redis-free deployment, backup validation and explicit approval.

   Dependencies and risks: 0053–0055 must not assume a mixed old/new
   deployment is safe; PostgreSQL is the durable authority, but idempotency and
   compatibility cutovers need explicit handoff. Unknown Redis consumers,
   queue growth, unmatched entries, invalid historical results or an
   unvalidated backup block the sequence. No issue permits `FLUSHDB`,
   `FLUSHALL`, broad Docker volume cleanup, provider replay or deletion of
   PostgreSQL business data.

   Current runtime evidence is recorded in issues 0052–0055: the final dry-run found
   zero entries in all six retired lists after removing 17,164 IA and 71 image
   entries. The protected Redis families and PostgreSQL durable totals remain
   outside this issue's deletion boundary; issue 0053 retains legacy
   `processed:*` markers for their natural TTL and does not delete them. Issue
   0054 likewise retains both compatibility families until its observation gate
   is complete. Issue 0055's named `cai` rebuild verified API health and worker
   startup without Redis; the old Redis container/storage remains retained.

## Dependencies, discrepancies, and sequencing

| Status | Finding | Planning impact |
| --- | --- | --- |
| completed | SPEC-0007–0012 and issues 0012–0022, 0026, and 0038–0040 have matching source, migrations, and focused test families. | Do not reopen as feature work without a concrete defect. |
| completed | Issue 0041 reconciled PRD §9/source traceability and ARCHITECTURE §13/source map with Alembic `0022`, `238/76`, and the six-operation SPEC-0012 surface. | Keep older counts dated as history and retain the external-runtime boundary. |
| completed locally | SPEC-0013 login/session/security policy and shell/session/BFF/read/action increments are implemented by issues 0042–0044. | Keep secure credential provisioning and production acceptance separate. |
| blocked | Production evidence requires environment, credentials, rollout ownership, and acceptance criteria. | Keep local verification and production acceptance separate. |
| blocked | Milestone F and broader IA-policy changes lack product decisions. | Do not create implementation work from inference. |

## Completed history

- **[completed]** Test isolation and disposable PostgreSQL runner (0001–0004),
  legacy finalization and raw diagnostic removal (0005–0006), documentation and
  OpenAPI baseline work (0007–0011), Acessórias Milestones A–E (0012–0022,
  0026), audio retry parity (0027), persistence/provider boundary refactors
  (0028–0036), Redis residue audit/cleanup (0037), and SPEC-0012 admin API
  slices (0038–0040), the documentation reconciliation (0041), PostgreSQL
  polling/lease for persistent IA finalization (0048), PostgreSQL polling for
  audio transcription and image extraction (0049–0050), contact hydration
  backoff preservation (0051), validated legacy queue retirement (0052),
  PostgreSQL webhook idempotency (0053), IA compatibility retirement boundary
  (0054), and Redis-free application runtime (0055).
- **[superseded/non-work]** Legacy Redis finalization, raw-payload debug
  endpoints, fixed-port test Compose work, automatic retention/archival,
  mounted `/v1`/`/v2` aliases, hosted CI, provider/model replacement, and
  unauthorised public identity management are not current delivery work.

## Recommended next pass

SPEC-0013 is implemented locally by issues 0042–0044, covering its
shell/session/BFF, read, and command-action increments. Issues 0048–0053 are
complete locally, with issues 0052–0053 also accepted in the named `cai`
runtime. Issue 0054 is implemented locally and ready for the named runtime
handoff, but its destructive compatibility-key apply remains gated by the
complete observation window. Issue 0054 remains gated by its complete TTL
observation and bounded apply; issue 0056 defines the remaining destructive
storage disposal. Issue
0053 is complete locally and in the named `cai` runtime, with its legacy marker
source retained for the following compatibility/decommission stages.
The remaining items are product, operational authorization, or
production-acceptance gates.
Request
lifecycle, broader IA policy, and production acceptance remain separate blocked
plan items.
