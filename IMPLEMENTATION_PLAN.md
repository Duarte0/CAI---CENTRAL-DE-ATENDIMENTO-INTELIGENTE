# Implementation Plan

_Planning baseline: 2026-08-21; synchronized 2026-08-25. Source, Alembic revisions, configuration, and
tests describe the current checkout; this plan records sequencing and local
evidence, never production availability. The SPEC-0013 Phase 2 delivery is
complete locally; production acceptance remains separate._

## Evidence-based current state

### Completed and verified locally

- **[completed] Core durable CAI workflow** (PRD §§5–8; SPEC-0001–0004).
  FastAPI webhook ingestion, PostgreSQL-backed cycles, persistent DigiSac
  history reconstruction, Groq classification, Redis coordination, and
  separate durable audio/image processing are implemented. PostgreSQL state is
  reserved before queue publication; lease, due-retry, reconciliation, and
  idempotency paths recover interrupted work. Terminal image failure blocks
  only dependent cycles; terminal audio failure becomes a warning.
- **[completed] Persistent-only finalization and privacy hardening**
  (SPEC-0001–0003; issues 0005, 0024, 0025). The legacy Redis
  buffer/debounce mode and raw-payload diagnostic routes are removed. Parser
  and webhook diagnostics retain bounded metadata without raw model output,
  extracted customer values, secrets, or payload bodies.
- **[completed] Durable schema and recovery foundations** (SPEC-0001, 0003;
  issues 0004, 0027–0033, 0037). Alembic owns the schema through
  `0023_manual_reconciliation`; persistence boundaries for contacts,
  cycles, assignments, media, classifications, and DigiSac directory state are
  isolated behind repositories. Audio transient retry parity and bounded Redis
  residue cleanup are implemented with recovery coverage.
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
- **[completed] Manual incremental DigiSac–Acessórias reconciliation**
  (SPEC-0007–0009, 0012; issue 0045). The explicit dry-run/apply CLI acquires
  both validated snapshots before publishing, applies safe PostgreSQL deltas
  under compatible advisory locks, retains historical directory facts, and
  runs deterministic batch identity discovery after commit. Acessórias
  `ListAll` is requested without a status filter, preserving active and
  inactive companies and raw `Status`; no provider write or production
  availability is inferred.
- **[completed | current checkout] Offline verification.** On 2026-08-25,
  `APP_TIMEZONE=UTC PYTHONPATH=/app python -m pytest -q` passed **255** and
  skipped **77**.
  The skips are the intentionally unconfigured `CAI_TEST_DATABASE_URL`
  PostgreSQL family. The most recent recorded disposable runner evidence is
  compileall and strict Pyright clean, Alembic head `0023_manual_reconciliation`,
  and PostgreSQL pytest **77 passed, 255 deselected**. The opt-in local webhook
  smoke remains import-safe and outside canonical automation.

### Implemented with bounded evidence

- **[completed | local-only evidence]** Provider integrations, Redis runtime,
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
- **[completed | local/disposable evidence]** Issue 0045 was validated on
  2026-08-25 with `APP_TIMEZONE=UTC`: compileall, strict Pyright, offline
  pytest **255 passed, 77 skipped**, Alembic
  `0023_manual_reconciliation`, and PostgreSQL pytest **77 passed, 255
  deselected**. This proves the tracked contracts and disposable publication;
  it does not prove live provider availability or production acceptance.

### No current implementation backlog signal

- **[completed]** Targeted searches found no active TODO/FIXME/stub or
  skipped/flaky-test marker that represents approved missing behavior. The
  `pass` occurrences are exception-control flow; the 77 skips are the explicit
  database prerequisite policy.
- **[superseded/deprecated]** Issue 0023 is deprecated: its active/inactive
  directory concern was superseded by the later directory-contract alignment,
  not an open duplicate build item. Issues 0001–0022, 0024–0041 and 0045 are closed.

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

### Phase 3 — Manual source reconciliation

3. **[P1 | completed locally] Manual incremental DigiSac–Acessórias
   reconciliation** (`SPEC-0007`–`0009`, `0012`, issue 0045).

   Outcome: provide an explicit dry-run/apply service and CLI that acquires
   complete Acessórias and DigiSac Contacts views before one atomic local
   publication, computes sanitized deltas, preserves historical facts, and
   rematches local contacts through the existing conservative identity rules.
   Active-only Acessórias evidence fails closed; no scheduler, provider write,
   Request, Redis identity authority, or admin-ledger loop is introduced.

   Completion criteria: additive execution state, compatible advisory locks,
   safe report, stable ordering, replay idempotency, dry-run non-destructiveness,
   focused integration coverage, and canonical disposable verification. All
  criteria are complete locally on 2026-08-25; live provider and production
  acceptance remain separate gates.

### Phase 4 — Product and release gates

4. **[P2 | blocked | product decision] Request lifecycle integration**
   (Milestone F).

   Outcome: define any Request status polling, interactions, comments,
   attachments, responsibility, closure, or reopening only after a product
   decision and a new specification. Completion criteria: approved behavior,
   provider contract, ownership, data/retry/reconciliation rules, and an issue
   decomposition. Current Request creation does not authorize this work.

5. **[P2 | blocked | product decision] Broader IA classification policy.**

   Outcome: decide any change to taxonomy precedence, confidence semantics,
   summary invariants, or structured descriptions before changing the
   four-field contract. Completion criteria: PRD/spec decision plus tests and
   migration assessment. Until then retain the implemented contract.

6. **[P1 | blocked | operational authorization] Production acceptance.**

   Outcome: separately authorized acceptance against the intended deployment.
   Completion criteria: named environment and owner, protected credentials,
   rollout/rollback policy, migration and backup approval, provider/Redis
   checks, and explicit acceptance criteria. This cannot be inferred from the
   local runner and must not be bundled with feature build work.

## Dependencies, discrepancies, and sequencing

| Status | Finding | Planning impact |
| --- | --- | --- |
| completed | SPEC-0007–0012 and issues 0012–0022, 0026, and 0038–0040 have matching source, migrations, and focused test families. | Do not reopen as feature work without a concrete defect. |
| completed | Issue 0041 reconciled PRD §9/source traceability and ARCHITECTURE §13/source map with Alembic `0022`, `238/76`, and the six-operation SPEC-0012 surface. | Keep older counts dated as history and retain the external-runtime boundary. |
| completed locally | SPEC-0013 login/session/security policy and shell/session/BFF/read/action increments are implemented by issues 0042–0044. | Keep secure credential provisioning and production acceptance separate. |
| completed locally | Issue 0045 adds the manual two-source reconciliation boundary, Alembic `0023_manual_reconciliation`, and disposable evidence of dry-run/apply/replay behavior; Acessórias `ListAll` is all-status. | Keep live provider and production acceptance separate. |
| blocked | Production evidence requires environment, credentials, rollout ownership, and acceptance criteria. | Keep local verification and production acceptance separate. |
| blocked | Milestone F and broader IA-policy changes lack product decisions. | Do not create implementation work from inference. |

## Completed history

- **[completed]** Test isolation and disposable PostgreSQL runner (0001–0004),
  legacy finalization and raw diagnostic removal (0005–0006), documentation and
  OpenAPI baseline work (0007–0011), Acessórias Milestones A–E (0012–0022,
  0026), audio retry parity (0027), persistence/provider boundary refactors
  (0028–0036), Redis residue audit/cleanup (0037), SPEC-0012 admin API
  slices (0038–0040), documentation reconciliation (0041), and manual
  DigiSac–Acessórias reconciliation (0045).
- **[superseded/non-work]** Legacy Redis finalization, raw-payload debug
  endpoints, fixed-port test Compose work, automatic retention/archival,
  mounted `/v1`/`/v2` aliases, hosted CI, provider/model replacement, and
  unauthorised public identity management are not current delivery work.

## Recommended next pass

SPEC-0013 is implemented locally by issues 0042–0044, covering its
shell/session/BFF, read, and command-action increments. Issue 0045 is also
complete locally; Request lifecycle, broader IA policy, provider composition,
and production acceptance remain separate blocked plan items.
