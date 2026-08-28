---
id: 0045
title: "Implement manual incremental DigiSac–Acessórias directory reconciliation"
type: feature
status: closed
priority: high
phase: 4
created_at: 2026-08-25
updated_at: 2026-08-26
closed_at: 2026-08-25
related_issues:
  - "0012"
  - "0013"
  - "0014"
  - "0015"
  - "0023"
  - "0038"
  - "0039"
  - "0040"
  - "0044"
blocked_by: []
affects:
  - alembic/versions/
  - src/core/acessorias_directory.py
  - src/core/digisac_client.py
  - src/core/digisac_contact_backfill.py
  - src/core/digisac_contact_repository.py
  - src/core/identity_resolution.py
  - src/utils/
  - tests/
  - README.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
  - specs/0007-acessorias-external-directory-foundation.md
  - specs/0008-digisac-contact-identity-foundation.md
  - specs/0009-digisac-acessorias-identity-resolution.md
  - specs/0012-administrative-contact-company-link-management.md

---

## Description

The checkout already has separate foundations for the Acessórias directory,
DigiSac Contacts full backfill, and conservative DigiSac–Acessórias identity
resolution. It does not yet have one safe, manually invoked operation that
refreshes both providers, applies only new or changed facts, and then reruns
identity discovery while preserving existing confirmed company links and
history.

This is required because the Acessórias company payload embeds the current
company contacts (`ContatosNaEmpresa`). When a responsible person changes, the
new contact must reach CAI and participate in discovery, but the existing
DigiSac-contact-to-company confirmation must not be lost or re-ranked merely
because the old Acessórias contact row is no longer current.

### Current implementation gap

- `src/core/acessorias_directory.py::fetch_snapshot()` reads
  `/departments/ListAll` and paginates `/companies/ListAll` with the observed
  `contacts`, `departments`, and `Pagina=N` parameters. It validates the
  complete in-memory snapshot and computes one global snapshot hash; the
  returned company list is treated as including every status.
- The current Acessórias publisher first sets all directory rows that are
  currently present/active to `is_present=FALSE, is_active=FALSE`, then
  upserts the acquired snapshot. That is a full-snapshot reconciliation, not a
  safe manual delta application. The provider list is now requested without a
  status filter, so a returned inactive company is retained with its raw
  status and derived activity state.
- Acessórias company contacts have no observed stable provider contact ID.
  `external_key` is derived from company identity, name, email, and mobile.
  A changed contact payload therefore must be treated conservatively as a new
  observed directory contact while preserving the old row/evidence as history;
  the implementation must not infer that two people are the same through name,
  phone, email, or fuzzy similarity.
- `src/core/digisac_contact_backfill.py` already acquires every validated page,
  deduplicates by opaque `contact.id`, and publishes through the
  timestamp-aware contact repository. It has no unified reconciliation run,
  diff report, or batch identity-discovery boundary.
- `src/core/identity_resolution.py` currently discovers one local DigiSac
  contact at a time. Its `identity_company_links` entity links a DigiSac
  contact to an Acessórias company, while match evidence separately references
  the Acessórias company-contact row. Existing `confirmed` links must remain
  authoritative when new evidence or a new company-contact row appears.
- The authenticated `/admin/acessorias/.../identity-discovery` command is a
  one-contact administrative operation over local facts. It is not a provider
  sync, a bulk operation, or a replacement for this manual reconciliation
  command.
- `src/core/digisac_directory.py::directory_sync_loop()` periodically refreshes
  DigiSac departments/users for assignment-name resolution. This issue must not
  extend that loop or introduce automatic synchronization of Contacts,
  Acessórias, or identity discovery.

### Expected outcome

Add one explicit manual reconciliation command/service with a preview/apply
boundary:

1. Acquire and validate complete DigiSac Contacts and Acessórias directory
   views without writing provider data.
2. Produce a sanitized diff of new, changed, unchanged, and safely retained
   records.
3. In `apply` mode, publish only the validated differences to PostgreSQL,
   preserving durable IDs, historical rows, match evidence, link transitions,
   confirmed links, and terminal cycle resolutions.
4. Rerun deterministic identity discovery for all local DigiSac contacts in a
   stable order so a new Acessórias contact can match an existing DigiSac
   contact. Persist only new evidence/candidate facts; never automatically
   confirm, reject, downgrade, or rewrite a historical cycle resolution.
5. Return and durably record a sanitized execution report that can be reviewed
   after the manually invoked run.

The operation is manual-only for this issue. No scheduler, cron entry, startup
hook, FastAPI lifespan task, worker loop, or periodic configuration is to be
added.

## Scope

### In scope

- A manually invoked internal CLI/service with an explicit `--dry-run` and
  explicit `--apply` boundary. Dry-run must be the safe default when practical;
  apply must never be implicit.
- Proper standalone PostgreSQL lifecycle for the command:
  `initialize_database()`, schema-capability/head verification, execution, and
  `close_database()`. It must not rely on an already-running API process.
- A top-level PostgreSQL advisory lock for the reconciliation operation, with
  compatible locking against the existing Acessórias and DigiSac contact
  publication boundaries. A second manual run must fail or report
  `reconciliation_in_progress` without modifying state.
- Complete provider acquisition before business-data publication. If either
  provider fails authentication, pagination, validation, retry exhaustion, or
  complete-view composition, the apply phase must not publish a partial
  two-source result.
- Reuse the existing typed provider boundaries, Bearer configuration, bounded
  retry behavior, `Retry-After` handling, rate limiting, pagination checks, and
  sanitized errors. Do not add direct HTTP calls to the matching layer or CLI
  handlers.
- Acessórias directory diff/application keyed by the existing stable provider
  identities:
  - company by opaque `Identificador`/`external_id`;
  - department by provider `ID`/`external_id`;
  - company-department relationship by the stable company/department pair;
  - embedded company contact by the existing `external_key` contract.
- Acessórias company fields must be compared field-by-field. A returned company
  with the same `external_id` may update only fields whose provider values are
  actually different; its local identity, PostgreSQL row, links, and audit
  history must remain stable.
- A returned company contact with a new `external_key` must be inserted and
  made available to discovery. The previous contact row and all evidence that
  references it must remain recoverable. No contact replacement may be
  inferred from names, phone, email, CNPJ, or fuzzy similarity.
- For a valid returned company payload whose child lists are authoritative,
  current child presence/activity may be reconciled without physical deletion:
  old Acessórias contact and relationship rows remain historical, while only
  current rows participate in future discovery/mapping. The full list contains
  active and inactive companies, and absence is applied only after that
  complete validated list is acquired.
- The Acessórias acquisition must not send an undocumented status filter. The
  observed `Status` value is retained as provider metadata and activity is
  derived only by the existing normalization rule; all returned statuses are
  accepted equally.
- DigiSac Contacts acquisition must reuse the validated `perPage`/`page=N`
  contract, validate `total`, `limit`, `currentPage`, and `lastPage`, globally
  deduplicate by `contact.id`, and preserve `deletedAt`/metadata semantics.
  Existing DigiSac rows must not be deleted or inactivated because they are
  absent from a list response.
- DigiSac contact application must preserve the existing timestamp-aware
  precedence: a demonstrably older provider snapshot cannot overwrite newer
  metadata; absent incoming fields do not erase known fields; unordered
  observations are merged conservatively; stable `contact.id` remains the
  canonical identity.
- A durable manual reconciliation execution record or equivalent PostgreSQL
  state containing only safe operational data: execution ID, mode, status,
  source snapshot hashes, page/attempt counts, new/changed/unchanged counts,
  discovery counts, timestamps, and sanitized failure category/message. It must
  not contain raw provider payloads, tokens, headers, phone numbers, email
  values, names, or message text.
- Deterministic full rematch over the local DigiSac contact set after the
  directory/contact deltas are committed. The rematch must use only the
  existing SPEC-0009 rules:
  exact normalized phone, exact normalized email, and the approved Brazilian
  8↔9 mobile variant. Groups, names, aliases, `idFromService`, department,
  previous Request, and fuzzy/ranking fallbacks remain excluded.
- Existing identity semantics must remain intact:
  - one company candidate remains `candidate` and is not auto-confirmed;
  - multiple candidate companies remain `ambiguous`;
  - no applicable candidate remains `unresolved`;
  - one existing applicable confirmed link has precedence over new discovery;
  - competing confirmed links remain `conflict`;
  - new evidence may be added for a newly observed Acessórias contact without
    deleting old evidence or changing the confirmed link;
  - `identity_company_links`, transitions, and
    `conversation_cycle_identity_resolutions` are not rewritten by directory
    synchronization.
- Manual reconciliation must not create or send Acessórias Requests, evaluate
  department mappings for cycles, mutate classifications, reopen/rewrite
  terminal cycle results, call Redis for identity authority, or invoke the
  authenticated admin command ledger once per contact as a substitute for a
  domain batch operation.
- A sanitized operator report suitable for the manual run must distinguish at
  least: source acquisition success/failure, companies/departments/contacts
  acquired, new/changed/unchanged facts, retained historical facts, newly
  discovered evidence, new candidate links, preserved confirmed links,
  ambiguous/unresolved results, and any contacts left for a later retry.

### Out of scope

- Any automatic or periodic trigger: cron, scheduler, background task, API
  startup sync, FastAPI lifespan task, worker supervisor task, or new interval
  setting.
- Provider writes, updates to DigiSac/Acessórias, Request creation,
  department mapping administration, ticket/cycle reprocessing, classification
  changes, or message/history backfill beyond the Contacts directory.
- Fuzzy/name/CNPJ matching, contact-person deduplication based on similarity,
  use of `idFromService`, group participant selection, or automatic confirmation.
- Changing the canonical `contact.id`, Acessórias company `external_id`,
  identity-link cardinality, confirmation/rejection policy, or terminal-cycle
  immutability.
- Treating provider absence as deletion without a validated, provider-supported
  complete-view decision. Historical PostgreSQL rows and evidence must not be
  hard-deleted.
- Exposing raw phone/email values or a new public HTTP endpoint. The existing
  authenticated administrative discovery route remains a separate per-contact
  operation.
- Reopening or modifying deprecated issue 0023. Its active/inactive contract
  concern is addressed only insofar as this issue needs a safe complete source
  view for manual reconciliation.
- Production synchronization, credential provisioning, deployment, cron
  installation, or provider-volume acceptance. Those require separate
  operational authorization after local/disposable validation.

## Implementation Plan

1. **Reconfirm the provider and persistence contracts.**
   - Verify the current Alembic head, schema columns, existing Acessórias
     execution table, DigiSac contact repository precedence, identity foreign
     keys, and confirmation/link transitions before changing code.
   - Document that Acessórias has no reliable `updated_at`/delta cursor in the
     observed contract. The operation is therefore a complete read followed by
     a local delta application, not a fabricated provider delta request.
   - Determine and test that the provider-supported `ListAll` composition
     returns all companies without a status filter, retaining both raw status
     and derived activity.

2. **Create the manual orchestration boundary.**
   - Add a focused utility, for example
     `src/utils/reconcile_digisac_acessorias.py`, backed by typed core service
     functions rather than embedding SQL/provider logic in the CLI.
   - Support a safe preview mode and an explicit apply mode. The command must
     initialize/close the PostgreSQL pool, verify the schema, acquire one
     top-level lock, and return a nonzero exit code for failed acquisition,
     validation, publication, or matching stages.
   - Keep the operation out of `src/api/routes.py` lifespan and
     `src/core/digisac_directory.py::directory_sync_loop()`.

3. **Acquire both complete snapshots before applying either source.**
   - Reuse `AcessoriasDirectoryAdapter.fetch_snapshot()` after correcting the
     source-view contract and removing any assumption that a blanket
     pre-inactivation is safe for this manual mode.
   - Reuse `DigisacClient.get_contacts_page()` and
     `acquire_contact_backfill()` for all validated Contacts pages.
   - Record only safe acquisition metadata and compute source/global hashes plus
     deterministic per-resource comparison keys. Do not log payloads or PII.
   - If either source is incomplete or fails, leave existing directory/contact
     rows, identity facts, and the prior successful execution unchanged.

4. **Build a deterministic, inspectable delta plan.**
   - Compare Acessórias companies/departments/relationships/embedded contacts
     to PostgreSQL and classify `new`, `changed`, `unchanged`, `current`, and
     `historical-retained` facts.
   - Treat a changed Acessórias contact key as a new observed contact unless a
     stable provider identity is later evidenced. Never merge old/new contact
     rows from names or identifiers used only as matching evidence.
   - Compare DigiSac Contacts by `contact.id` and apply the existing timestamp
     precedence to classify incoming data as newer, older, unordered, or equal.
   - In dry-run, render the safe plan without changing business tables. If
     execution state is persisted for audit, it must be clearly marked
     `dry_run` and contain no PII.

5. **Publish only the approved delta transactionally.**
   - Replace the current manual-path blanket update of all Acessórias rows with
     explicit per-resource upserts/updates and non-destructive historical
     retention.
   - Preserve PostgreSQL primary keys and all identity foreign keys. A valid
     company update must not create a new company identity or alter an existing
     `identity_company_links` row.
   - Preserve old Acessórias contact/evidence rows when a responsible contact
     changes. Mark a prior child row non-current only when the validated
     returned parent payload establishes the current child set; never infer
     global absence from a partial or invalid source view.
   - Publish DigiSac contacts through the existing repository boundary, keeping
     the full-backfill lock and merge semantics.
   - Commit the directory/contact data only after both snapshots and the delta
     plan pass validation. A database failure must roll back the complete
     source-delta publication and mark the run failed without erasing the
     previous good state.

6. **Run local deterministic rematch after publication.**
   - Iterate all local DigiSac contacts in stable primary-key/external-ID order,
     calling a reusable domain batch boundary around the existing discovery
     logic rather than issuing authenticated HTTP commands per contact.
   - Make evidence/link upserts idempotent and retain the run's counts. A
     contact with an existing confirmed company must remain confirmed even when
     the new Acessórias contact produces additional evidence or a competing
     candidate; report the competing fact instead of changing confirmation.
   - Do not call providers, Redis, Request preparation, department mapping, or
     cycle resolution from discovery.
   - If matching fails after directory publication, record a resumable
     `matching_failed`/equivalent execution outcome; rerunning the same manual
     operation must converge without deleting the committed directory delta.

7. **Add durable observability and operator documentation.**
   - Prefer an additive Alembic execution record after `0022_identity_discovery_command`
     if the existing tables cannot represent a two-source manual run and its
     resumable stages. Any downgrade must preserve/refuse destructive data loss
     according to SPEC-0001.
   - Update the README/runbook and architecture/source map to state that this
     is a manual command only, how dry-run/apply are separated, what the report
     means, and which existing automatic DigiSac department/user loop is
     unrelated.
   - Update implementation-derived plan/spec status only after the behavior and
     validation evidence are complete. Do not claim provider or production
     acceptance from local tests.

## Data, migration, compatibility, security, and rollout

- **Data/migrations:** preserve all existing Acessórias directory rows,
  DigiSac contacts, identity evidence, links, transitions, and cycle outcomes.
  If a new execution table is required, add it through Alembic after the
  current head with an explicit data-preserving downgrade refusal. Do not create
  or mutate schema at application startup.
- **Compatibility:** retain the existing provider endpoints, Bearer settings,
  retry/rate-limit behavior, Contacts pagination, webhook contact upsert,
  DigiSac department/user loop, six authenticated `/admin/acessorias` routes,
  Request boundary, and Redis authority boundary. The manual command is an
  additive internal operation.
- **Concurrency/idempotency:** serialize one manual reconciliation at a time;
  repeated runs with the same provider state must yield zero new deltas and no
  duplicate rows/evidence/transitions. Concurrent rematch attempts must
  converge under the existing contact locks.
- **Failure safety:** no partial provider snapshot may mark absence or replace
  the last valid state. A failed provider acquisition or PostgreSQL transaction
  must leave the previous directory/contact/identity state usable and the
  failure report sanitized.
- **Security/privacy:** read tokens only from existing settings. Logs, reports,
  metrics, and execution state may contain run IDs, stable opaque external IDs,
  hashes, counts, timestamps, rule/state names, and sanitized categories; they
  must not contain provider tokens, Authorization headers, raw payloads, names,
  phone numbers, email values, message text, or secret-bearing URLs.
- **Rollout:** local deterministic tests and disposable PostgreSQL validation
  come first. The first real manual run requires separate authorization,
  protected credentials, a named operator, a reviewed dry-run report, and
  explicit approval of the apply step. No scheduler is part of rollout.

## Tests

- **Acessórias acquisition:** deterministic provider doubles for departments,
  complete active/inactive composition, paginated companies, embedded contacts,
  empty contact lists, repeated pages/records, invalid parent references,
  bounded retries, `Retry-After`, missing credentials, and all-status company
  acquisition before publication.
- **DigiSac acquisition:** valid one-page and multi-page Contacts responses,
  metadata validation, page mismatch/non-advancement, repeated contacts,
  invalid contact shapes, transient/permanent provider failures, timestamp
  precedence, absent-field preservation, and no deletion/inactivation from
  list absence.
- **Delta planner:** new/changed/unchanged companies; new and changed embedded
  contacts; changed department relationships; stable company identity; old
  contact history retention; no name/phone/fuzzy contact merge; safe
  all-status behavior; deterministic counts and hashes.
- **PostgreSQL publication:** dry-run leaves business tables unchanged; apply
  is atomic for both acquired source snapshots; replay is idempotent; a
  failed acquisition/commit preserves the previous state; advisory lock blocks
  a concurrent run; historical rows/evidence remain queryable; confirmed
  `identity_company_links` survive a changed Acessórias responsible contact.
- **Identity rematch:** new Acessórias contact matching an old DigiSac contact;
  exact phone/email and approved Brazilian variant; groups excluded; one
  candidate, ambiguity, unresolved, existing confirmed-link precedence,
  competing candidate reporting, evidence/link idempotency, concurrent
  discovery, no historical cycle rewrite, and no automatic confirmation.
- **CLI/orchestration:** explicit manual invocation, dry-run/apply exit status,
  PostgreSQL initialize/close lifecycle, sanitized report, no provider writes,
  no Request or Redis identity calls, no startup/lifespan/scheduler trigger,
  resumable matching failure, and stable ordering.
- **Repository validation:**
  `PYTHONPATH=/app python -m pytest -q tests/test_manual_directory_reconciliation.py tests/test_acessorias_directory.py tests/test_digisac_contact_backfill.py tests/test_identity_resolution.py`
  (with the applicable PostgreSQL marker/doubles),
  `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`,
  `python -m compileall -q src tests alembic scripts`,
  `npx --yes pyright`, `git diff --check`, and `graphify update .`.

## Acceptance Criteria

- [x] The reconciliation is exposed only as an explicitly manually invoked
  internal command/service; no scheduler, cron, startup hook, FastAPI lifespan
  task, worker loop, or periodic setting is added.
- [x] Dry-run is non-destructive and reports the validated two-source plan
  without changing directory, contact, evidence, link, cycle, Request, Redis,
  or configuration state.
- [x] Apply cannot begin until both provider snapshots are complete, valid,
  and internally consistent; a provider/auth/pagination/rate-limit failure
  preserves the last successful local state.
- [x] Acessórias acquisition requests `ListAll` without a status filter, includes
  companies regardless of provider status, preserves raw `Status`, and only
  applies absence/inactivation after the complete list is validated.
- [x] New Acessórias companies, departments, relationships, and embedded
  contacts are inserted once using their stable/deterministic local keys.
- [x] Existing companies and departments retain their durable PostgreSQL
  identities and are updated only when incoming provider fields differ.
- [x] A changed Acessórias responsible-contact payload is retained as a new
  observed contact when its `external_key` changes; old rows and evidence are
  preserved, and no name/phone/email/fuzzy inference merges the two contacts.
- [x] Explicit current child-list changes may update current presence/activity
  without physically deleting historical contact or relationship rows; global
  absence is inferred only from the complete all-status list.
- [x] DigiSac Contacts are acquired through validated `page=N` pagination,
  deduplicated by canonical `contact.id`, merged with timestamp-aware
  precedence, and never deleted/inactivated by list absence.
- [x] Repeating an identical manual run produces no duplicate directory rows,
  evidence, candidate links, transitions, or confirmed links.
- [x] After apply, all local DigiSac contacts are rematched using only the
  existing conservative SPEC-0009 rules, in deterministic order, with a
  resumable report if the matching stage fails.
- [x] A new Acessórias contact can create new evidence/candidate data for an
  existing DigiSac contact without requiring a new DigiSac contact record.
- [x] Existing `confirmed` company links remain confirmed and authoritative;
  discovery cannot auto-confirm, auto-reject, downgrade, replace, or delete
  them. New divergent evidence is reported as a competing candidate/fact.
- [x] `identity_match_evidence`, `identity_company_links`, link transitions,
  and `conversation_cycle_identity_resolutions` retain their historical data;
  no terminal cycle result is rewritten.
- [x] The command does not create Requests, evaluate mappings, call providers
  from the identity layer, use Redis as identity authority, or invoke the
  authenticated one-contact discovery command as a bulk shortcut.
- [x] Execution state/report contains only safe IDs, hashes, counts, timestamps,
  and sanitized categories; no PII, raw payload, token, header, or message text
  is emitted or persisted.
- [x] Focused tests, the offline suite, disposable PostgreSQL verification when
  available, compileall, strict Pyright, `git diff --check`, and `graphify
  update .` pass; unavailable external prerequisites are reported separately.
- [x] README, ARCHITECTURE, IMPLEMENTATION_PLAN, and the affected specs remain
  consistent with the manual-only reconciliation boundary and do not claim
  automatic scheduling or production/provider acceptance.

## References

- `PRD.md` §§5.5, 8, 9, and 10 — Acessórias directory, canonical DigiSac
  contact, PostgreSQL authority, privacy, and operational boundaries.
- `ARCHITECTURE.md` §§2, 2.1, 8, 9, 13 — provider/directory boundaries,
  persistence model, locks, source map, and current full-backfill paths.
- `IMPLEMENTATION_PLAN.md` — completed Milestones A–E, current Alembic head
  `0023_manual_reconciliation`, and separation of local evidence from
  provider/production acceptance.
- `specs/0007-acessorias-external-directory-foundation.md` — complete
  Acessórias resource snapshot, pagination, retention, retry, and validation.
- `specs/0008-digisac-contact-identity-foundation.md` — canonical `contact.id`,
  full Contacts backfill, timestamp precedence, and absence preservation.
- `specs/0009-digisac-acessorias-identity-resolution.md` — exact evidence,
  candidate/confirmed/ambiguous/unresolved/conflict rules and audit semantics.
- `specs/0012-administrative-contact-company-link-management.md` — existing
  one-contact administrative discovery/confirmation boundary and its explicit
  exclusion of provider synchronization.
- `issues/0012_-_implement-acessorias-directory-foundation.md` — current
  Acessórias adapter/publication implementation.
- `issues/0014_-_implement-digisac-contacts-full-backfill.md` — current
  validated DigiSac Contacts acquisition and publication contract.
- `issues/0015_-_implement-digisac-acessorias-identity-resolution.md` — current
  durable identity evidence/link implementation.
- `issues/0023_-_include_inactive_acessorias_companies_in_directory_snapshots.md`
  — deprecated active/inactive concern that must not be duplicated or silently
  treated as solved by a partial manual run.
- `issues/0038_-_implement-authenticated-read-only-identity-link-triage-api.md`,
  `issues/0039_-_implement-authenticated-identity-link-confirmation-and-rejection.md`,
  and `issues/0040_-_implement-authenticated-identity-discovery-command.md` —
  existing administrative boundaries that this command must not bypass.

---

## Resolution

<!-- Filled by the agent on close. DO NOT edit manually. -->
<!-- What was done, decisions made, and why. -->
<!-- Include: files modified, tests added, edge cases handled. -->

Implemented on 2026-08-25. Added Alembic
`0023_manual_reconciliation`, the PostgreSQL-authoritative
service `src/core/digisac_acessorias_reconciliation.py`, and the explicit
manual CLI `src/utils/reconcile_digisac_acessorias.py`. The service acquires
both validated snapshots before publication, computes safe per-resource deltas,
uses the shared reconciliation/Acessórias/DigiSac advisory locks, publishes the
two sources atomically, retains replaced contacts and evidence, and records
only sanitized execution counters, hashes, statuses, and failure categories.

The Acessórias adapter now requests `ListAll` without a status filter, so the manual
path accepts active and inactive companies in the same complete snapshot. Raw
`Status` is preserved and only the existing activity normalization derives
`is_active`; no status filter or undocumented provider parameter is invented.
The complete snapshot applies child-list and global absence only
non-destructively, preserving durable IDs and historical rows. DigiSac contacts reuse the validated full-backfill and timestamp-aware
repository boundary. A new `discover_all_identities()` domain batch rematches
all local contacts in stable order after commit, without the admin ledger,
Redis, Requests, mappings, or cycle mutation; a failure is reported as
`matching_failed` for a later manual retry.

Contract correction on 2026-08-26: removed the `ativa=S` request parameter and
the `complete_view`/`incomplete_source_view` gate. `ListAll` now imports every
returned company regardless of status while retaining raw `Status` and the
derived `is_active` value.

Added focused coverage for all-status Acessórias acquisition and sanitized
manual orchestration, updated the shared contact duplicate merge to preserve
email metadata, updated the PostgreSQL test fixture for the new execution
table, and synchronized SPEC-0007/0008/0009/0012, README, ARCHITECTURE, and
IMPLEMENTATION_PLAN. The canonical runner, executed as
`APP_TIMEZONE=UTC PYTHONPATH=/app python scripts/verify.py`, passed compileall,
Pyright, offline pytest (**255 passed, 77 skipped**), Alembic head
`0023_manual_reconciliation`, and PostgreSQL pytest (**77 passed, 255
deselected**) on 2026-08-25. Local validation evidence is recorded separately
from provider/production acceptance; live provider acceptance remains a
separate operational step.
