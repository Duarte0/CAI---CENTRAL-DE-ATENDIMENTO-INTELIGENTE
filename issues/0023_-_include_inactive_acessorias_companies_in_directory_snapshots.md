---
id: 0023
title: "Include inactive Acessórias companies in directory snapshots"
type: bug
status: deprecated
priority: high
phase: 4
created_at: 2026-08-17
updated_at: 2026-08-17
closed_at: ~
related_issues:
  - "0012"
blocked_by: []
affects:
  - src/core/acessorias_directory.py
  - tests/test_acessorias_directory.py
  - specs/0007-acessorias-external-directory-foundation.md
  - specs/README.md
  - README.md
  - PRD.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
---

## Description

The Acessórias directory adapter acquires only the provider's active-company
view, so a successful refresh can publish a directory that omits inactive
companies entirely. This violates the approved directory contract and leaves
the local PostgreSQL authority unable to retain or resolve inactive companies
that are absent from the active-only provider result.

**Plan/spec references:** `IMPLEMENTATION_PLAN.md`, Approved Acessórias
milestones, Milestone A; SPEC-0007 v1.1 sections “Recursos, dados e
integridade”, “Contrato de adaptador e sincronização”, and “Testes, validação
e critérios de aceitação”; PRD §5.3 and §8; and ARCHITECTURE §§2.1 and 9. The
contract requires companies to be retained with raw provider status and
requires a complete snapshot containing both active and inactive companies.

**Dependencies:** the directory migration, typed adapter, transactional
reconciliation, and refresh path delivered by issue 0012. No identity,
department-mapping, Request, schema, or product-policy decision is required.
SPEC-0007 deliberately leaves the provider-specific inactive-selection
mechanism as an implementation detail, but fixes the required complete-snapshot
outcome and forbids inventing undocumented status values or query parameters.

**Root cause:** `AcessoriasDirectoryAdapter.fetch_snapshot()` calls
`GET /companies/ListAll` with `{"contacts": "", "departments": "", "ativa":
"S", "Pagina": page}` on every page (`src/core/acessorias_directory.py:505-508`).
There is no second query or composition path for inactive companies. A
deterministic probe of the current adapter recorded exactly that active-only
parameter set. The adapter tests exercise an active synthetic company and an
empty terminal page, but contain no inactive-provider response or assertion
that the complete acquisition includes one.

**Reproduction:**

1. Configure a deterministic provider double that honors `ativa=S` by returning
   only active companies, while exposing a separate provider-supported result
   containing an inactive company.
2. Run `AcessoriasDirectoryAdapter.fetch_snapshot()` with the current
   adapter.
3. Observe the company requests use `ativa=S` for every page and the returned
   `AcessoriasSnapshot.companies` contains no inactive company.
4. Publish the snapshot through `sync_acessorias_directory_sync()` and inspect
   PostgreSQL: the inactive company is absent if it was never previously
   observed, or a previously observed row is treated as source-absent and
   inactivated rather than retained as a provider-present inactive company.

**Actual behaviour:** the refresh reports success for the active-only result,
and the local directory cannot represent an inactive company that the provider
did not return through the requested view. A later full refresh can therefore
mark such a historical company absent even though it still exists at the
provider with inactive status.

**Expected behaviour:** one validated complete refresh includes active and
inactive companies, preserves each raw `Status`, derives `is_active` only when
the observed value permits it, and publishes the same durable record for an
inactive company with `is_present=true` and `is_active=false`. Only a complete
provider view may mark a company source-absent; an acquisition that cannot
compose the required active/inactive view must fail before publication and
preserve the last successful directory.

## Scope

### In scope

- Restore complete active/inactive company acquisition in the dedicated
  Acessórias adapter while retaining the existing department, contact,
  pagination, validation, retry, and transactional publication boundaries.
- Use only provider-supported selection/composition behavior consistent with
  the evidence and constraints in SPEC-0007; do not guess a new status value,
  endpoint, or parameter.
- Add deterministic adapter and disposable-PostgreSQL regression coverage for
  an inactive company, repeated refresh, absence/inactivation, reappearance,
  invalid/partial composition, and preservation of the last successful view.

### Out of scope

- Automatic identity matching or confirmation, department mapping, Request
  creation/lifecycle, Users synchronization, new HTTP/admin routes, or changes
  to DigiSac directory behavior.
- Changing the raw status contract, inventing a provider cursor or delta sync,
  physically deleting historical rows, changing migration ownership, or
  weakening the complete-snapshot/rollback guard.
- Live-provider synchronization, credentials, deployment, production rollout,
  or unrelated retry/rate-limit changes covered by issues 0018, 0019, and 0022.

## Implementation Plan

1. Reconfirm the complete active/inactive snapshot invariant in SPEC-0007 and
   the current provider evidence. Identify the provider-supported acquisition
   sequence needed to compose active and inactive companies without assuming
   undocumented status strings or query parameters. If an external contract
   fact is genuinely unavailable, stop at that boundary and record the exact
   evidence gap for a specification pass rather than publishing an incomplete
   view.
2. Update the typed adapter's company acquisition so all required provider
   views are fetched, paginated, validated, and merged by the existing opaque
   company identity. Preserve duplicate detection, valid empty-page
   termination, safety limits, parent validation, request attempt accounting,
   raw `Status`, and the conservative failure-before-publication behavior.
3. Preserve the existing PostgreSQL reconciliation semantics: a present
   inactive company remains a durable row with `is_present=true` and
   `is_active=false`; a company is marked source-absent only after the complete
   composed snapshot commits; a later provider return reactivates the same
   external identity. Do not change identity or Request eligibility policy
   beyond consuming the corrected directory state.
4. Add deterministic adapter tests that prove both active and inactive records
   are requested/collected, merged without duplicate identity, and retain raw
   status plus derived activity. Add negative tests for incomplete composition,
   repeated pages/records, and provider failure so no partial view is published.
5. Add or extend disposable-PostgreSQL tests for first publication of an
   inactive company, identical replay, source absence, reappearance, and
   rollback/preservation of the last complete view. Retain existing contact,
   relationship, authentication, retry, sanitization, and concurrency tests.
6. Synchronize SPEC-0007, its index, implementation-derived README/PRD/
   architecture statements, and the Milestone A evidence/status only after the
   corrected behavior is implemented and verified. Run `graphify update .`
   according to the repository workflow, then close this issue only after one
   focused commit.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migration:** no migration is expected. Existing external identities,
  raw statuses, activity/presence flags, contacts, relationships, and sync
  executions remain readable. Do not erase historical inactive or absent rows.
- **Compatibility:** preserve the dedicated adapter, Bearer configuration
  boundary, `/departments/ListAll` and `/companies/ListAll` provider contracts,
  `Pagina=N` semantics, bounded retries, `Retry-After` handling, and the four
  durable directory resource groups.
- **Integrity/concurrency:** a partial active/inactive composition must not
  mark missing companies absent or replace the last successful view. Concurrent
  refreshes remain mutually excluded, and identical complete snapshots remain
  idempotent without duplicate companies, contacts, relationships, or success
  records.
- **Security/privacy:** retain current sanitization. No token, authorization
  header, raw provider body, contact name, phone, email, PII, or secret-bearing
  URL may be added to logs, metrics, fixtures, exceptions, or durable sync
  state.
- **Observability/rollout:** expose only safe execution/page/attempt/count,
  presence/activity, and sanitized failure metadata. Local deterministic and
  disposable-PostgreSQL evidence does not prove live provider coverage or
  production synchronization; report those limits separately.

## Tests

- **Unit:** `tests/test_acessorias_directory.py` — provider-supported
  active/inactive composition, pagination, identity merge, raw status/activity,
  incomplete composition, retry, and sanitization.
- **PostgreSQL:** the directory synchronization tests in
  `tests/test_acessorias_directory.py` — inactive publication, replay,
  source absence, reactivation, rollback, and concurrent refresh exclusion.

Required validation commands:

- `PYTHONPATH=/app python -m pytest -q tests/test_acessorias_directory.py`
- `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`
- `python -m compileall -q src tests alembic scripts`
- `npx --yes pyright`
- `PYTHONPATH=/app python scripts/verify.py` when disposable PostgreSQL and
  Docker prerequisites are available; report unavailable prerequisites
  separately.
- `git diff --check`

## Acceptance Criteria

- [ ] A complete successful adapter refresh contains every provider-supported
  active and inactive company exactly once, including the inactive-company
  regression fixture, without relying on undocumented status values or
  parameters.
- [ ] An inactive company is persisted with its raw provider `Status`,
  `is_present=true`, and `is_active=false` when the observed status supports
  that derivation; unknown status remains explicitly unknown.
- [ ] An incomplete or failed active/inactive composition never publishes a
  partial snapshot, never marks omitted companies source-absent, and preserves
  the last successful directory and success marker.
- [ ] A repeated complete snapshot is idempotent; source absence in a later
  validated complete view inactivates without physical deletion, and provider
  reappearance restores the same external identity and activity state.
- [ ] Pagination, duplicate identity/parent validation, bounded retry,
  `Retry-After`, authentication failure, missing credentials, refresh locking,
  and sanitized failure state remain covered and unchanged.
- [ ] No Request, identity, department-mapping, HTTP, migration, or Redis
  authority behavior changes as a side effect of directory acquisition.
- [ ] No token, header, raw provider payload, PII, or secret-bearing URL appears
  in logs, fixtures, exceptions, or durable state.
- [ ] Focused tests, the applicable offline/PostgreSQL verification,
  compileall, strict Pyright, and `git diff --check` pass, with unavailable
  prerequisites reported separately from skips and passes.
- [ ] SPEC-0007, `specs/README.md`, `README.md`, `PRD.md`,
  `ARCHITECTURE.md`, and `IMPLEMENTATION_PLAN.md` remain consistent with the
  corrected active/inactive snapshot evidence; Graphify metadata is updated
  according to the repository workflow.
- [ ] The issue is closed only after validation and one focused commit.

## References

- **Primary contract:** `specs/0007-acessorias-external-directory-foundation.md`
  v1.1, sections “Recursos, dados e integridade”, “Contrato de adaptador e
  sincronização”, and “Testes, validação e critérios de aceitação”; inactive
  companies must be retained and a complete snapshot must compose
  active/inactive provider views.
- **Index:** `specs/README.md`, SPEC-0007 active-specification entry and
  implementation workflow.
- **Plan:** `IMPLEMENTATION_PLAN.md`, Approved Acessórias milestones, Milestone
  A — External Directory Foundation.
- **Product/architecture:** `PRD.md` §§5.3 and 8; `ARCHITECTURE.md` §§2.1 and
  9; both require inactive companies in the local authoritative directory.
- **Related implementation:** `issues/0012_-_implement-acessorias-directory-foundation.md`.
- **Current source evidence:** `src/core/acessorias_directory.py:485-540`
  requests only `ativa=S`; the adapter probe recorded
  `{'contacts': '', 'departments': '', 'ativa': 'S', 'Pagina': 1}`.
- **Current test evidence:** `tests/test_acessorias_directory.py:92-129`
  covers active pagination only; the current canonical runner passed
  compileall, Pyright, 189 offline tests, Alembic head, and 60 PostgreSQL
  tests, none of which prove inactive-provider acquisition.
- **Non-duplicates:** open issues 0018–0022 cover Acessórias Request transport,
  rate-limit sharing, cycle-scoped assignment, pre-POST payload-load state,
  and unproven 429 retries; none covers omission of inactive directory
  companies.

---

## Resolution

Deprecated by product decision on 2026-08-17. This issue will not be
implemented. The original description, evidence, acceptance criteria, and
references are preserved for historical traceability.
