---
id: 0009
title: "Reconcile active-document traceability and verification evidence"
type: spec
status: closed
priority: high
phase: 2
created_at: 2026-08-14
updated_at: 2026-08-14
closed_at: 2026-08-14
related_issues:
  - "0007"
  - "0008"
blocked_by: []
affects:
  - README.md
  - PRD.md
  - ARCHITECTURE.md
  - specs/0001-shared-data-and-analysis-contract.md
  - specs/0002-digisac-webhook-and-query-api.md
  - specs/0003-durable-finalization-and-media.md
  - specs/0004-reproducible-verification-baseline.md
  - specs/0005-documentation-baseline-reconciliation.md
  - specs/0006-api-documentation-and-openapi-contract.md
  - specs/README.md
  - IMPLEMENTATION_PLAN.md
---

## Description

Complete the P1 documentation follow-up recorded under **Separate pending
work** in `IMPLEMENTATION_PLAN.md`: reconcile active-document traceability and
verification evidence after the persistent-only baseline and OpenAPI delivery.
This is a documentation/specification slice; it does not reopen or duplicate
the completed outcomes of issues 0007 or 0008.

**Verified gap:** `SPEC-0005` v1.2 and the specification index identified issue
0008's local evidence as **127 passed, 33 skipped** offline and **33 passed,
127 deselected** on disposable PostgreSQL, but `PRD.md` §9,
`ARCHITECTURE.md` §13, and the README validation section still present the
older **122/33** and **33/122** results as current. The active documents also
retain obsolete phase/item traceability, while `SPEC-0005` still says the PRD
and architecture need this delta and `SPEC-0006` retains implementation-stage
future-tense wording despite being implemented. The issue-0007 historical
evidence and all closed issue records must remain unchanged.

The current checkout subsequently closed issue 0012, and its source, runner,
and plan now establish **143 passed, 36 skipped** offline and **36 passed, 143
deselected** on disposable PostgreSQL as the latest local evidence. That
authoritative result supersedes the issue-0008 count for active documents;
issue-0008 and issue-0007 counts remain dated historical evidence.

Expected outcome: active documentation consistently points to stable
SPEC/issue references, describes the implemented OpenAPI publication and
Acessórias directory foundation as complete, records the latest local baseline,
and preserves explicit boundaries around offline skips, disposable PostgreSQL,
Redis, providers, replicas, deployment, and production readiness.

## Scope

### In scope

- Reconcile `README.md`, `PRD.md` §9 and source traceability, `ARCHITECTURE.md`
  §13, `specs/README.md`, and active SPEC-0001 through SPEC-0006 against the
  current source, `scripts/verify.py`, tracked tests, issue 0008, and the
  canonical contracts.
- Replace obsolete plan-phase/item references with stable SPEC/issue
  references or concise completed-outcome descriptions, without rewriting
  historical issue records.
- Make the latest verification counts and their stage meanings consistent:
  **127 passed, 33 skipped** offline and **33 passed, 127 deselected** on the
  disposable PostgreSQL stage; retain **122/33** and **33/122** only as dated
  issue-0007 history.
- Reconcile SPEC-0005's status, acceptance narrative, and notes with the
  completed issue-0008 follow-up, and change SPEC-0006's publication/status
  wording only as needed to describe the implemented contract. Preserve its
  three real implementation limitations as explicit separate gaps rather than
  silently changing API behavior.
- Synchronize `IMPLEMENTATION_PLAN.md` with the verified completion and keep
  the distinction between implemented behavior, approved future Acessórias
  work, and unverified external-runtime/production evidence.

### Out of scope

- Any application, worker, API handler, OpenAPI runtime, test, migration,
  configuration, Compose, infrastructure, provider, database, Redis, or
  deployment change.
- Rewriting or closing issues 0001 through 0008, changing their recorded
  evidence, or reopening persistent-finalization, diagnostic-surface, baseline,
  or OpenAPI work already closed.
- Changing API versioning, authentication, retention, SLA, classification
  policy, Acessórias provider decisions, or the blocked Milestones A–E.
- Running a live webhook, external provider, Redis deployment, replica, or
  production acceptance exercise to manufacture evidence.
- Updating SPEC-0007 through SPEC-0011 beyond a necessary cross-reference
  check; those contracts remain blocked by their own external evidence and
  decisions.

## Implementation Plan

1. Inventory every affected traceability/evidence claim before editing. Use the
   source-of-truth order from `OPERATING_PRINCIPLES.md`: confirm current route,
   configuration, persistent-cycle/media, OpenAPI, and runner behavior in
   source and tests; treat the plan and prior issues as status/evidence
   context. Identify each stale phase/item reference, each active 122/33
   claim, and each statement that still describes OpenAPI publication as future
   work. Do not infer new product policy from a documentation mismatch.
2. Update the active documents in a coherent order: canonical specifications
   and index, then README, PRD, architecture, and finally the plan status. Use
   stable links/IDs (`SPEC-0005`, `SPEC-0006`, issues 0007/0008) and preserve
   the actual contract boundaries: persistent-only finalization, unversioned
   mounted query routes, conditional webhook HMAC, no query authentication, and
   future-only `/v1`/`/v2` policy. Keep the implemented Acessórias directory
   foundation separate from decision-gated identity, department, and Request
   work.
3. Normalize verification language without collapsing stages or changing
   meaning. The offline count must retain its expected PostgreSQL-dependent
   skips; the PostgreSQL count must be identified as disposable and selected by
   the runner. Every active document must continue to state that these results
   do not prove Redis, DigiSac, Groq, replicas, deployment, or production
   readiness. Retain the dated issue-0007 and issue-0008 counts only where
   historical provenance is useful.
4. Recheck negative claims and contract references. Active documentation must
   not describe a removed finalization flag or legacy path as current, a
   `/v1`/`/v2` route as mounted, OpenAPI as unpublished, an external runtime as
   verified, or the decision-gated Acessórias milestones as implemented. It must not add
   secrets, provider credentials, raw webhook material, or unsupported retry,
   idempotency, concurrency, API-error, or SLA guarantees; existing
   SPEC-0001–0004 contracts remain the references for those behaviors.
5. Run targeted reference/count searches and link checks, the applicable
   offline validation, compileall, strict Pyright, and the canonical runner
   when its disposable PostgreSQL prerequisites are available. Record only
   results actually executed, run `graphify update .` for the documentation
   changes, inspect the focused diff, synchronize `IMPLEMENTATION_PLAN.md`, and
   close the issue in one focused commit.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migrations:** none. No schema, backfill, production data, or database
  target may be changed.
- **Compatibility:** preserve the current eight unversioned business routes,
  persistent-only finalization, OpenAPI/Swagger/ReDoc publication, conditional
  webhook HMAC, and the absence of query authentication. Documentation changes
  must not stabilize or alter runtime response shapes.
- **Security:** do not add credentials, authentication claims, raw payloads,
  signed URLs, or sensitive examples. Preserve the distinction between the
  conditional webhook signature and unauthenticated internal query routes.
- **Observability:** report local offline and disposable-PostgreSQL evidence
  separately. Do not infer provider, Redis, deployment, replica, SLA, or
  production guarantees from local documents or tests.
- **Rollout:** documentation-only; no service restart, provider call, live
  webhook, Redis operation, migration, or deployment is required.

## Tests

- **Traceability/count checks:** targeted `rg` searches confirm that obsolete
  phase/item references and active 122/33 claims are gone from the intended
  active documents, while dated issue-0007 history remains in its closed issue
  and relevant historical context.
- **Contract-reference checks:** links and IDs in the spec index, SPEC-0001–
  0006, README, PRD, architecture, and plan resolve to the intended files and
  distinguish completed SPEC-0005/0006 work from future or blocked work.
- **Repository validation:**
  `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`,
  `python -m compileall -q src tests alembic scripts`, `npx --yes pyright`, and
  `PYTHONPATH=/app python scripts/verify.py` when runner prerequisites are
  available. Report unavailable PostgreSQL/runtime stages separately rather
  than converting skips into external evidence.
- **Final checks:** `git diff --check` and `graphify update .` pass, and the
  final diff contains only this issue's implementation plus the documentation
  and plan synchronization authorized by the plan item.

## Acceptance Criteria

- [x] PRD §9, ARCHITECTURE §13, README, SPEC-0004, SPEC-0005, SPEC-0006, and
  the spec index consistently identify **143 passed, 36 skipped** as the latest
  offline evidence and **36 passed, 143 deselected** as the latest disposable-
  PostgreSQL evidence.
- [x] **122/33**, **33/122**, **127/33**, and **33/127** remain only as
  explicitly dated issue-0007 or issue-0008 historical evidence and are not
  presented as the current baseline.
- [x] Obsolete plan-phase/item traceability in active documents is replaced by
  stable SPEC/issue references or a source-backed completed outcome; links and
  specification versions resolve correctly.
- [x] Active documentation describes SPEC-0005 and SPEC-0006 as implemented
  outcomes, removes stale future-tense OpenAPI-publication claims, and retains
  SPEC-0006's three documented runtime limitations without turning them into
  new API behavior.
- [x] No active document presents the removed finalization flag/legacy path,
  `/v1` or `/v2` route, query authentication, Acessórias identity/department/
  Request milestone, provider, Redis, replica, deployment, or production
  acceptance as currently delivered. The implemented directory foundation is
  identified as local-only evidence.
- [x] Documentation continues to point to SPEC-0001–0004 for durable data,
  retry, idempotency, concurrency, media, webhook, and verification contracts
  without inventing a new error, retention, SLA, or compatibility guarantee.
- [x] No application code, tests, migrations, configuration, infrastructure,
  production data, credentials, or closed issue records are changed.
- [x] Targeted searches, applicable test/compile/Pyright checks, and the
  canonical runner (when available) pass with exact results recorded; skipped
  or unavailable stages are explicitly labeled.
- [x] `graphify update .`, `git diff --check`, and the final focused diff pass;
  `IMPLEMENTATION_PLAN.md` records the verified completion and required
  documentation/index status is synchronized.
- [x] The issue is closed only after all criteria are met and the documentation
  and plan synchronization are included in one focused commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — completed history issue 0009 and
  `Dependencies, risks, and recorded discrepancies` for the resolved drift.
- Primary specification: `specs/0005-documentation-baseline-reconciliation.md`
  v1.3 — active-document contract and the current evidence requirement.
- Related specification: `specs/0006-api-documentation-and-openapi-contract.md`
  v1.1 — completed OpenAPI publication and explicitly preserved runtime gaps.
- Supporting specifications: SPEC-0001 through SPEC-0004 for data, webhook,
  durable-cycle/media, and verification invariants.
- Completed related issues: `issues/0007_-_reconcile-persistent-documentation-baseline.md`
  and `issues/0008_-_publish-generated-openapi-http-contract.md`.
- Current evidence sources: `src/api/routes.py`, `src/api/openapi.py`,
  persistent finalization/media modules, `scripts/verify.py`, tracked tests,
  and the active versioned documents.

---

## Resolution

- **Implementation:** reconciled active README, PRD §9/source traceability,
  ARCHITECTURE §13/source map, SPEC-0001 through SPEC-0006, the spec index, and
  the implementation plan. Stable SPEC/issue references replace obsolete plan
  item references; OpenAPI and the SPEC-0007/issue-0012 directory foundation
  are described as implemented, while dependent identity/Request work remains
  gated.
- **Evidence:** the current canonical runner result is 143 passed/36 skipped
  offline and 36 passed/143 deselected on disposable PostgreSQL 16 with
  Alembic head `0015_acessorias_directory`. Issue-0007 and issue-0008 counts
  remain dated historical evidence; no closed issue file was changed.
- **Validation:** targeted reference/count/link searches, offline pytest,
  compileall, strict Pyright, `PYTHONPATH=/app python scripts/verify.py`,
  `git diff --check`, and `graphify update .` passed. The runner used only its
  temporary PostgreSQL target and made no live-provider, Redis, webhook, or
  production request.
- **Docs:** SPEC-0005 is v1.3 and records this reconciliation; the index and
  plan record issue 0009 as complete and preserve the external-runtime and
  production evidence boundaries.
