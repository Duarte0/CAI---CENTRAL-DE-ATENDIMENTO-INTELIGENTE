---
id: 0015
title: "Implement DigiSac–Acessórias identity resolution"
type: feature
status: closed
priority: high
phase: 4
created_at: 2026-08-14
updated_at: 2026-08-14
closed_at: 2026-08-14
related_issues:
  - "0012"
  - "0013"
  - "0014"
blocked_by:
  - "0012"
  - "0013"
  - "0014"
affects:
  - alembic/versions/
  - src/core/
  - tests/
  - scripts/verify.py
  - IMPLEMENTATION_PLAN.md
  - specs/0009-digisac-acessorias-identity-resolution.md
  - specs/README.md
---

## Description

Implement the first eligible P1 Acessórias follow-on: a PostgreSQL-authoritative,
conservative identity-resolution layer between the completed DigiSac contact
foundation and the completed Acessórias directory. The layer must preserve
technical evidence, contact-company links, and conversation/cycle resolution as
separate durable records, while requiring explicit confirmation before any
company is considered resolved for downstream automation.

**Plan/spec references:** `IMPLEMENTATION_PLAN.md`, **Approved Acessórias
milestones**, item 3, **P1 | ready for issue | specified** Milestone C —
DigiSac ↔ Acessórias Identity Resolution; primary contract `SPEC-0009` v1.1;
cross-cutting contracts `SPEC-0001` v1.2 and `SPEC-0004` v1.6.

**Dependencies:** closed issues `0012`, `0013`, and `0014`; Alembic head
`0016_digisac_contact_identity`; the durable tables from
`0015_acessorias_directory` and `0016_digisac_contact_identity`; the existing
PostgreSQL transaction/pool and conversation-cycle persistence boundaries; and
the disposable PostgreSQL verification runner. SPEC-0009 v1.1 records the
approved Brazilian mobile-variant rule and the initial `manual_db` confirmation
procedure, so no additional product decision is required for this slice.

**Verified gap:** the current checkout has durable DigiSac contacts and
Acessórias companies/contacts, but no match-evidence store, contact-company
link entity, conversation/cycle resolution state, conservative matcher, or
auditable manual confirmation operation. The current Alembic head is still
`0016_digisac_contact_identity`, and targeted source searches find no existing
implementation of SPEC-0009 states or transitions. Existing directory and
contact normalizers are foundations only; neither currently resolves identities.

Expected outcome: repeated matching and replay converge on durable evidence,
candidate links, and per-cycle resolution without automatic confirmation;
explicit database confirmation can produce one valid confirmed company for a
contact; ambiguity, missing data, group contacts, conflicts, invalid directory
state, and failed transactions remain recoverable/auditable and block any
future automation requiring a unique company.

## Scope

### In scope

- Add an additive Alembic revision after `0016_digisac_contact_identity` for
  separate durable match evidence, DigiSac-contact/Acessórias-company links,
  and conversation/cycle resolution records. Enforce evidence foreign keys,
  one link per `(digisac_contact_id, acessorias_company_id)`, allowed states,
  nonblank safe fields, timestamps, transition/audit references, and indexes
  needed by the matching and resolution queries. Do not impose
  `UNIQUE(digisac_contact_id)`.
- Implement typed internal records and PostgreSQL persistence using the existing
  pool/transaction boundary. Keep PostgreSQL authoritative; Redis must not
  decide, confirm, or hold the only copy of evidence, links, or resolution.
- Implement deterministic discovery from non-group DigiSac contacts and
  present Acessórias company contacts with nonblank normalized identifiers:
  exact normalized phone, exact normalized email, and only the approved
  Brazilian mobile 8↔9 variant with the same valid `55` country code and DDD.
  Aggregate matches by distinct company, retain all supporting evidence, and
  create or converge candidate links without promoting any result to
  `confirmed`.
- Implement the required resolution precedence and states: one applicable
  confirmed link resolves a cycle as `confirmed`; competing confirmed links
  produce `conflict`; otherwise discovery yields `candidate`, `ambiguous`, or
  `unresolved` according to the distinct-company result. Group contacts do not
  participate in automatic matching and remain unresolved unless an explicit
  confirmed link applies.
- Implement controlled direct-PostgreSQL confirmation for an explicit DigiSac
  contact/company pair. It must validate both records, use
  `confirmation_source = manual_db` (or the spec-equivalent source), require
  `confirmed_at`, leave `confirmed_by` absent/null without a trustworthy actor,
  reject or preserve a conflicting confirmation safely, and record corrective
  transitions instead of silently deleting prior evidence or links.
- Make replay, concurrent discovery, confirmation, and resolution idempotent
  and convergent. A valid manual confirmation must not be silently re-ranked or
  replaced by later discovery, and a terminal cycle resolution must not be
  rewritten merely because directory or link state changes later.
- Add deterministic unit tests and disposable-PostgreSQL tests for the complete
  SPEC-0009 contract, and extend the repository's schema reset/verification
  registration only as needed for the new PostgreSQL-backed test module.
- On completion, update implementation-derived documentation and Graphify
  metadata through the established workflow, and synchronize the Milestone C
  status/evidence in `IMPLEMENTATION_PLAN.md` as part of the build issue close.

### Out of scope

- Acessórias provider synchronization or changes to the completed directory
  adapter/schema; DigiSac Contacts synchronization or changes to the completed
  contact backfill/hydration contract.
- Fuzzy/name matching, `idFromService`, group numbers, names, alternative
  names, or any other undocumented identifier as automatic evidence; automatic
  confirmation from phone, email, a combination of evidence, ranking, or score.
- Department mapping, Request creation or lifecycle, classification/IA
  changes, assignment-history changes, webhook contract changes, new HTTP
  routes, public APIs, UI, or an administrative identity system.
- Redis-backed identity state, provider writes, real-provider acceptance,
  production synchronization, hosted CI, deployment/rollout changes, retention
  policy, or unrelated cleanup.
- Hard-deleting evidence, links, transitions, or historical cycle resolution;
  changing a terminal resolution silently; inventing an actor for
  `confirmed_by`; or weakening the data-loss protection of existing migration
  downgrades.

## Implementation Plan

1. Reconfirm the current Alembic head (`0016_digisac_contact_identity`), the
   existing Acessórias and DigiSac contact columns/normalization semantics, the
   `conversation_processing_cycles` identity and terminal-state guards, the
   PostgreSQL transaction helpers in `src/core/db.py`, and the SPEC-0004 runner
   boundary. Define typed internal inputs/outputs so provider-shaped JSON does
   not leak into matching or persistence. Keep Acessórias `external_id` and
   DigiSac `external_id` as opaque keys and use only the normalized fields
   already defined by SPEC-0007/0008.
2. Add one additive Alembic migration after the current head. Model evidence,
   contact-company links, and cycle resolution separately; reference existing
   contact/company rows; enforce the link-pair uniqueness, allowed state
   transitions/values, required timestamps, safe reason/source fields, and
   indexes needed to query normalized phone/email candidates and one cycle's
   resolution. Make downgrade refuse before data loss when any new state is
   populated, following the existing migration pattern. Advance runtime schema
   capability/head checks without creating schema at application startup.
3. Implement discovery as a deterministic read/compare/persist flow. Exclude
   group DigiSac contacts and blank identifiers. Apply exact phone/email first;
   apply the Brazilian variant only when both numbers are structurally valid
   Brazilian numbers with the same DDD and the only difference is the leading
   mobile `9`. Do not generalize the transformation to foreign numbers,
   different DDDs, other edits, or fuzzy comparisons. Collapse multiple
   Acessórias contacts from one company into one candidate company while
   preserving each supporting evidence row and its rule/version/provenance.
4. Persist evidence and candidate/rejected links with conflict-safe upserts.
   Replays must not duplicate evidence or audit transitions. Resolve a contact
   or cycle under row/advisory locking as appropriate: exactly one applicable
   confirmed link has precedence over discovery, multiple confirmed links
   yield `conflict`, and no confirmed link yields `ambiguous` for multiple
   candidate companies or `unresolved` for no applicable candidate. Never
   choose a company from a ranking or downgrade a manual confirmation.
5. Add the cycle-resolution boundary using the existing cycle identity and
   terminal semantics. Persist the selected state, origin, timestamp, and
   confirmed-link reference when applicable. Ensure directory incompleteness,
   invalid identifiers, lock/transaction failure, and matching failure leave a
   recoverable/auditable outcome and do not change a terminal classification or
   cycle result.
6. Add the controlled `manual_db` operation. It must receive explicit local/
   external IDs, validate the contact/company pair and current state in one
   transaction, confirm only the requested link, require `confirmed_at`, keep
   `confirmed_by` unset without a trustworthy administrative identity, and
   reject a resulting competing-confirmation conflict without committing a
   misleading resolution. Corrections must be new audited rejection or
   replacement transitions; replaying the same confirmation must be harmless.
7. Add deterministic tests for exact unique/shared phone and email, same-company
   duplicate contacts, the valid Brazilian 8↔9 variant, invalid variant cases,
   groups, explicit confirmation precedence, competing confirmations,
   ambiguity/unresolved/conflict, evidence/link idempotency, correction audit,
   replay, concurrent execution, transaction failure, terminal-cycle
   preservation, and sanitized observability. Add `postgres`-marked coverage
   for migration head, foreign keys, many-to-many cardinality, uniqueness,
   state constraints, and concurrent persistence.
8. Run focused tests, the applicable offline suite, disposable PostgreSQL
   verification, compileall, strict Pyright, `git diff --check`, and
   `graphify update .`. Record unavailable prerequisites separately from passes.
   Then synchronize only implementation-derived docs/spec status and exact
   Milestone C evidence, review the focused diff, and close the issue through
   `IMPLEMENTATION_PLAN.md` sync plus one focused commit containing the
   implementation, tests, documentation, and Graphify changes.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migrations:** use a new Alembic revision after `0016_digisac_contact_identity`;
  do not mutate existing directory/contact tables unless a demonstrated
  contract gap requires an additive constraint/index. Preserve all evidence,
  links, transition history, and cycle results. Downgrade must refuse while new
  identity state exists.
- **Compatibility:** preserve the existing eight HTTP routes, HMAC webhook and
  event behavior, IA/classification contract, persistent finalization, ticket
  assignment history, Acessórias refresh behavior, DigiSac contact backfill/
  hydration behavior, and Redis authority boundary. The identity operation is
  an internal durable capability with no public HTTP surface.
- **Integrity/concurrency:** foreign keys must prevent orphan evidence/links;
  the link pair must be unique while allowing one contact to link to multiple
  companies. Locking and conflict-safe upserts must prevent duplicate evidence,
  duplicate transitions, lost confirmations, arbitrary company selection, and
  multiple confirmed links from being treated as a unique resolution.
- **Security/privacy:** logs, metrics, exceptions, fixtures, and durable
  operational state may expose only safe IDs, rule/state names, counts, and
  sanitized categories. Do not emit names, full phone numbers, email values,
  raw provider payloads, tokens, Authorization headers, or message text.
- **Observability:** record rule/version, source, state, safe counts, timestamps,
  and sanitized failure/conflict categories sufficient to audit discovery and
  confirmation without retaining secret-bearing or unnecessary PII in logs.
- **Rollout:** validation is local deterministic/disposable-PostgreSQL evidence
  only. Scheduling, operator ownership beyond the controlled `manual_db`
  procedure, real credentials, provider traffic, and production acceptance are
  not established by this issue.

## Tests

- **Matching contract:** exact phone/email unique-company candidates, shared
  identifiers across companies, repeated Acessórias contacts within one
  company, valid Brazilian mobile 8↔9 candidate, invalid/different DDD/
  foreign/additional-change rejection, blank identifiers, and group behavior.
- **Persistence and transitions:** evidence provenance/version, candidate-link
  convergence, many-to-many links, explicit confirmation, rejection/correction
  audit, confirmed-link precedence, ambiguous/unresolved/conflict resolution,
  terminal-cycle preservation, replay, and concurrent discovery/confirmation.
- **PostgreSQL:** migration to head, foreign keys, nonblank/state/timestamp
  constraints, unique contact-company pair without single-company cardinality,
  rollback/data-preserving downgrade refusal, and transactional failure
  preservation.
- **Privacy/operations:** sanitized logs and failure state, invalid/incomplete
  directory handling, lock conflict handling, and no Redis-only resolution.
- **Repository validation:** focused tests; `PYTHONPATH=/app python scripts/verify.py`;
  strict Pyright; compileall; `git diff --check`; and `graphify update .`.

## Acceptance Criteria

- [x] An additive Alembic revision after `0016_digisac_contact_identity`
  creates separate evidence, contact-company-link, and cycle-resolution state,
  with valid foreign keys, safe state/source fields, required timestamps,
  indexes, and a data-preserving downgrade guard.
- [x] A DigiSac contact may link to multiple Acessórias companies, while each
  `(digisac_contact_id, acessorias_company_id)` pair is unique and repeat
  discovery cannot duplicate the pair or its evidence/audit transitions.
- [x] Exact normalized phone and exact normalized email produce evidence and a
  `candidate` for one distinct company, `ambiguous` for multiple distinct
  companies, and `unresolved` for none; no automatic path produces
  `confirmed`.
- [x] Multiple Acessórias contacts from the same company collapse to one
  candidate company while all supporting evidence remains attributable and
  independently typed/versioned.
- [x] The Brazilian mobile variant is accepted only for valid `55` numbers with
  the same DDD and an exact leading-mobile-`9` 8↔9 difference; different DDDs,
  foreign numbers, invalid structure, and any other edit do not create variant
  evidence.
- [x] Names, alternative names, company/contact names, `idFromService`, group
  numbers, and fuzzy/score-based comparisons never create automatic evidence,
  candidates, or confirmations; group contacts remain unresolved without an
  explicit confirmed link.
- [x] The controlled confirmation operation validates the explicit contact and
  company, records `manual_db` (or equivalent), requires `confirmed_at`, leaves
  `confirmed_by` unset without a trustworthy actor, and preserves prior
  evidence/transitions.
- [x] One applicable confirmed link takes precedence over divergent discovery;
  competing confirmed links yield `conflict` and never select a company
  arbitrarily. `candidate`, `ambiguous`, `unresolved`, `conflict`, and
  `rejected` states block downstream automation requiring a unique company.
- [x] Replay and concurrent discovery, confirmation, and resolution converge
  without duplicate rows, lost manual confirmation, impossible transitions, or
  silent hard deletion; correcting a confirmation is an auditable transition.
- [x] A failed match, invalid/incomplete directory reference, lock conflict, or
  transaction failure leaves recoverable/auditable state and does not modify a
  terminal classification or terminal cycle resolution.
- [x] Logs, metrics, exceptions, fixtures, and durable operational state contain
  no names, full phone numbers, email values, raw payloads, message text,
  tokens, or Authorization headers; safe IDs, rules, states, counts, and
  sanitized categories remain available for diagnosis.
- [x] Deterministic unit tests and disposable-PostgreSQL tests cover all
  positive/negative matching, transition, integrity, idempotency, concurrency,
  rollback, terminal-preservation, and privacy cases above, and the migration
  reaches the repository's expected head.
- [x] Focused tests, applicable offline and PostgreSQL verification,
  compileall, strict Pyright, `git diff --check`, and `graphify update .` pass;
  unavailable prerequisites are reported separately from skips and passes.
- [x] Implementation-derived documentation, SPEC-0009/spec-index status,
  exact local evidence, and Graphify metadata are synchronized; the issue is
  closed only after `IMPLEMENTATION_PLAN.md` records Milestone C completion and
  all changes are included in one focused commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — **Approved Acessórias milestones**, item 3,
  **P1 | completed locally | implemented** Milestone C; see **Specification
  boundary and next gate**.
- Primary specification: `specs/0009-digisac-acessorias-identity-resolution.md`
  v1.1 — canonical evidence, conservative matching, states, manual
  confirmation, precedence, auditability, privacy, and verification contract.
- Cross-cutting contracts: `specs/0001-shared-data-and-analysis-contract.md`
  v1.2 and `specs/0004-reproducible-verification-baseline.md` v1.6.
- Completed prerequisites: issues `0012`, `0013`, and `0014`; Alembic revisions
  `0015_acessorias_directory` and `0016_digisac_contact_identity`.
- Current implementation boundaries: `src/core/acessorias_directory.py`,
  `src/core/digisac_client.py`, `src/core/digisac_contact_backfill.py`,
  `src/core/db.py`, `src/core/config.py`, conversation-cycle persistence, and
  `scripts/verify.py`.

---

## Resolution

Implemented the SPEC-0009 v1.1 identity slice under Alembic revision
`0017_digisac_acessorias_identity`. Added PostgreSQL-authoritative evidence,
many-to-many contact/company links, audited transitions, immutable cycle
resolution rows, exact phone/email and bounded Brazilian mobile matching, and
controlled local/external-ID `manual_db` confirmation with auditable correction.
The DigiSac contact foundation received additive raw/normalized email fields so
the approved exact-email rule has a durable input; no HTTP route, Redis state,
fuzzy matching, provider write, department mapping, or Request creation was
added.

### Tests and validation

- `PYTHONPATH=/app python -m pytest -q tests/test_identity_matching.py`: 6 passed.
- `PYTHONPATH=/app python scripts/verify.py`: compileall PASS; project Pyright
  PASS with 0 diagnostics; offline pytest **175 passed, 48 skipped**; Alembic
  head `0017_digisac_acessorias_identity` PASS; PostgreSQL pytest **48 passed,
  175 deselected**.
- `npx --yes pyright src/core/identity_resolution.py`: 0 errors, warnings, or
  informations.
- `git diff --check`: passed after final documentation synchronization.

### Documentation and migration

Synchronized SPEC-0009, SPEC-0008, SPEC-0004, `specs/README.md`,
`IMPLEMENTATION_PLAN.md`, `README.md`, `PRD.md`, and `ARCHITECTURE.md` with the
implemented boundary, Alembic head, and disposable verification evidence.
`graphify update .` completed successfully; the focused commit is the remaining
close-out step.

---
