---
id: 0010
title: "Restore financial taxonomy parity in the IA prompt"
type: bug
status: closed
priority: high
phase: 3
created_at: 2026-08-14
updated_at: 2026-08-14
closed_at: 2026-08-14
related_issues: []
blocked_by: []
affects:
  - src/workers/ia_worker.py
  - src/core/intents.py
  - tests/test_ia_worker_intent.py
  - IMPLEMENTATION_PLAN.md
---

## Description

Restore the approved `intent_type` taxonomy contract at the IA prompt boundary
so the model is instructed about every canonical value already accepted by the
application, without changing classification parsing, persistence, HTTP
projections, or business precedence.

**Verified gap:** `financial` is present in `VALID_INTENT_TYPES`, the
`IAAnalysisResult` persistence model, the generated HTTP taxonomy, PRD §6, and
SPEC-0001 v1.2, but the prompt built by `IAWorker._build_prompt()` omits it
from both the allowed-value JSON example and the classification guidance. The
current focused tests cover prompt author/context invariants and parser
normalization, but do not detect drift between the shared taxonomy and the
prompt. This is a contract-parity defect, not a provider-quality claim.

Expected outcome: the prompt names `financial` wherever it defines the allowed
taxonomy and gives the model bounded guidance for that canonical value, while
the four-field model output contract, existing `payment`/`billing` precedence,
normalization fallback, persistence schema, and API behavior remain unchanged.

## Implementation Plan

1. Reconfirm the canonical taxonomy from `src/core/intents.py`, the four-field
   output contract and taxonomy requirements in SPEC-0001 v1.2, and the
   published list in PRD §6. Treat `VALID_INTENT_TYPES` as the code-level
   source for prompt-parity checks; do not add aliases, labels, precedence
   rules, confidence semantics, or broader classification policy.
2. Update the classification prompt in `src/workers/ia_worker.py` so
   `financial` appears in the allowed `intent_type` value list and in the
   relevant guidance, using the canonical English value and bounded wording
   consistent with the existing taxonomy. Preserve the four JSON keys, the
   existing payment-versus-billing examples and precedence, author/context
   instructions, and all provider request settings.
3. Extend `tests/test_ia_worker_intent.py` with a prompt-parity assertion that
   derives the expected canonical values from `VALID_INTENT_TYPES` and fails
   if any value is missing or an undocumented value is introduced. Add focused
   checks that `financial` is guided in the prompt, that a parsed canonical
   `financial` result remains `financial`, and that invalid output still
   normalizes to `other` with the existing contract validation.
4. Run the focused tests and repository checks, inspect the prompt diff for
   accidental policy or secret/PII additions, run `graphify update .`, and
   synchronize the completed item and exact validation evidence in
   `IMPLEMENTATION_PLAN.md` before closing this issue in one focused commit.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migrations:** none. Do not change tables, migrations, persisted
  classifications, historical records, or backfills.
- **Compatibility:** preserve the exact four model fields
  (`intent_type`, `confidence`, `title`, `description`), current normalization
  of unknown labels to `other`, `IAAnalysisResult`, HTTP schemas, and all
  existing payment/billing/protocol/document behavior. No new intent value is
  authorized by this issue.
- **Retry/concurrency/idempotency:** unchanged. The prompt edit must not alter
  provider retry windows, worker claims/leases, cycle transitions, queue
  publication, or classification idempotency; existing tests remain the
  authority for those behaviors.
- **Security/configuration:** do not add credentials, provider-specific
  promises, raw customer content, PII, or new configuration. Prompt examples
  and assertions must remain synthetic and safe.
- **Observability/rollout:** no new metrics, logs, endpoint, deployment, or
  provider-backed accuracy assertion is required. This is a local contract
  correction; external model behavior is not verified by doubles.

## Tests

- **Focused unit/prompt:** `PYTHONPATH=/app python -m pytest -q tests/test_ia_worker_intent.py`
- **Offline suite:**
  `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`
- **Static/repository validation:**
  `python -m compileall -q src tests alembic scripts`, `npx --yes pyright`,
  `PYTHONPATH=/app python scripts/verify.py` when disposable PostgreSQL
  prerequisites are available, and `git diff --check`.
- **Graph/documentation:** `graphify update .`; verify targeted references to
  the plan item, SPEC-0001 v1.2, and the canonical taxonomy remain consistent.

## Acceptance Criteria

- [x] The prompt's allowed `intent_type` list contains exactly every value in
  `VALID_INTENT_TYPES`, including `financial`, with no undocumented value.
- [x] The prompt contains bounded guidance for `financial` and preserves the
  existing four-field JSON output shape, author/context rules, and
  payment-versus-billing precedence examples.
- [x] A valid parsed result with `intent_type: "financial"` remains
  `financial`; malformed, incomplete, or unknown output retains the existing
  rejection/normalization behavior and does not create a new fallback.
- [x] Focused tests derive prompt parity from `VALID_INTENT_TYPES` and cover
  both the positive `financial` case and negative drift/invalid-output cases.
- [x] No migration, persisted-data shape, HTTP response, API taxonomy,
  provider configuration, retry, lease, concurrency, idempotency, cycle-state,
  or classification-precedence behavior changes beyond the prompt text and
  its focused tests.
- [x] The prompt and tests contain no secret, token, raw webhook material,
  customer PII, or unsupported provider/accuracy guarantee.
- [x] The focused test, applicable offline suite, compileall, strict Pyright,
  and canonical runner (when available) pass with exact results recorded;
  unavailable PostgreSQL/runtime stages are explicitly labeled.
- [x] `graphify update .`, targeted contract/reference checks, and
  `git diff --check` pass, and the focused diff contains no unrelated cleanup.
- [x] `IMPLEMENTATION_PLAN.md` marks only this taxonomy item complete and
  records the observed validation evidence without claiming provider-backed
  quality.
- [x] The issue is closed only after the implementation, required tests and
  plan synchronization are included in one focused commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — **Separate pending work**, P1
  “Restore `financial` taxonomy parity in the IA prompt”; see the taxonomy
  parity defect under **Dependencies, risks, and recorded discrepancies**.
- Primary specification: `specs/0001-shared-data-and-analysis-contract.md`
  v1.2 — four-field IA output, taxonomy, normalization, persistence, and
  worker-test contract.
- Product contract: `PRD.md` §6 — supported `intent_type` values and model
  output boundary.
- Current code: `src/core/intents.py`, `src/workers/ia_worker.py`,
  `src/core/models.py`, and `src/api/openapi.py`.
- Focused tests: `tests/test_ia_worker_intent.py`.

---

## Resolution

Implemented and closed issue 0010.

- Updated `IAWorker._build_prompt()` so the allowed `intent_type` list exactly
  matches `VALID_INTENT_TYPES`, including `financial`, and added bounded
  financial guidance without changing payment/billing precedence or the
  four-field model output contract.
- Added focused tests for canonical prompt parity, financial parsing/guidance,
  and the existing unknown/incomplete-output normalization and rejection
  behavior. No migration or persisted-data/API/provider configuration change
  was made.
- Synchronized SPEC-0001 v1.2, `specs/README.md`, and
  `IMPLEMENTATION_PLAN.md`; the broader classification-policy boundary and
  issue 0011 remain unchanged.

Validation performed:

- `PYTHONPATH=/app python -m pytest -q tests/test_ia_worker_intent.py` — **16
  passed**.
- `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py` —
  **146 passed, 36 skipped**.
- `python -m compileall -q src tests alembic scripts` — PASS.
- `npx --yes pyright` — **0 errors, 0 warnings, 0 informations**.
- `PYTHONPATH=/app python scripts/verify.py` — compileall, Pyright, offline
  pytest, disposable PostgreSQL 16, Alembic head `0015_acessorias_directory`,
  and PostgreSQL pytest **36 passed, 146 deselected** — PASS.
- `graphify update .` and `git diff --check` — PASS. Graphify retained its
  existing SQL parser and `pyrightconfig.json` coverage warnings.

No provider-backed quality, Redis, deployment, production, secret, or PII
claim was made.
