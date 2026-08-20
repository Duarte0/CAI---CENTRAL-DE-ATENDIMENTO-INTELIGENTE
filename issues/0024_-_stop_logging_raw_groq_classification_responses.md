---
id: 0024
title: "Stop logging raw Groq classification responses"
type: bug
status: closed
priority: high
phase: 3
created_at: 2026-08-17
updated_at: 2026-08-17
closed_at: 2026-08-17
related_issues:
  - "0006"
  - "0010"
blocked_by: []
affects:
  - src/workers/ia_worker.py
  - tests/test_ia_worker_intent.py
  - specs/0001-shared-data-and-analysis-contract.md
  - README.md
  - PRD.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
---

## Description

The IA worker writes customer-derived Groq output into application logs while
parsing classification responses. This is a confirmed privacy defect: logs can
contain the complete model response, including reasoning, title, description,
or copied conversation content, even though the classification parser is meant
to retain only the validated structured result.

**Plan/spec references:** `IMPLEMENTATION_PLAN.md`, completed Persistent
conversation analysis and recovery; SPEC-0001 v1.3 (baseline v1.2), §§3–4; PRD §§6 and 8; and
ARCHITECTURE §10. The repository contract requires logs to contain sanitized
reasons and safe IDs/metadata without sensitive content, and the supported
operational surfaces must not expose raw bodies or customer material.

**Dependencies:** the existing Groq classification worker and parser from the
completed analysis baseline, the parser tests in `tests/test_ia_worker_intent.py`,
and the privacy/logging rules already established by issue 0006. No product,
provider, migration, or authorization decision is required.

**Root cause:** `_parse_result()` logs `result_text` with
`raw_response=%r` when it recovers JSON from a wrapped response
(`src/workers/ia_worker.py:1025-1031`) and logs `result_text[:500]` with
`response_preview=%r` when no valid JSON is found (`:1033-1037`). The parser
receives the complete model response from `_analyze_with_groq()`, so these
values are not sanitized before reaching the logger.

**Reproduction:**

1. Instantiate the worker with the existing test helper.
2. Call `_parse_result()` with a wrapped response containing a sentinel such
   as `CLIENT_PRIVATE_SENTINEL`, a customer-derived title, and a description.
3. Capture warning logs.
4. Observe the sentinel and the full wrapped response in the
   `raw_response` log field. Repeat with malformed output and observe the first
   500 characters in `response_preview`.

The current focused IA-worker tests pass (`tests/test_ia_worker_intent.py`),
including wrapped and malformed-response cases, but they assert only that the
recovery warning exists; they do not assert that response content is absent
from captured logs. The source inspection above provides direct evidence of
the unbounded and preview logging paths.

**Actual behaviour:** valid wrapped responses log the complete raw Groq output,
and invalid responses log a raw 500-character prefix. The output may include
customer content and model reasoning, and the log record is retained outside
the parser's validated classification boundary.

**Expected behaviour:** parser diagnostics expose only safe operational
metadata, such as a sanitized category and bounded structural counters or
offsets. No complete or partial model response, title, description, reasoning,
conversation text, token, secret, or other customer-derived content may be
written to logs. Valid wrapped-response recovery, invalid-response rejection,
retry/dead-letter behavior, and classification persistence semantics remain
unchanged.

## Scope

### In scope

- Remove raw and preview model-response values from the `_parse_result()` log
  records while retaining enough non-sensitive metadata to distinguish wrapped
  recovery from invalid output.
- Add deterministic regression coverage for both logging branches using unique
  sensitive sentinels, including a wrapped valid response and malformed output.
- Audit the parser's adjacent exception/logging boundary for the same response
  leakage, without broadening the change into unrelated provider or logging
  refactors.

### Out of scope

- Changing the four-field IA contract, intent taxonomy, prompt, model choice,
  response recovery algorithm, validation rules, or persisted classification
  data.
- Reintroducing raw-payload diagnostic routes, changing webhook handling, or
  modifying the already-removed diagnostic surfaces from issue 0006.
- Historical log deletion, retention-policy changes, provider credentials,
  deployment configuration, or production log-system changes.
- Acessórias Request retry/reconciliation, identity resolution, department
  mapping, media extraction, or unrelated exception sanitization.

## Implementation Plan

1. Reconfirm SPEC-0001's privacy/observability contract and enumerate every
   `_parse_result()` log argument. Preserve the existing distinction between
   direct JSON, wrapped JSON recovery, and invalid/truncated output.
2. Replace raw response and preview arguments at the parser boundary with only
   safe metadata. Ensure any retained metadata is bounded, non-content-bearing,
   and cannot reconstruct the model output; preserve the existing exception
   and retry/dead-letter behavior for invalid responses.
3. Add focused tests that capture logs for wrapped and malformed responses and
   assert that unique title, description, reasoning, and sentinel content do
   not appear, while the safe recovery/error category remains observable. Keep
   the existing parser acceptance and rejection tests.
4. Run the focused IA-worker tests, the applicable offline suite, compileall,
   strict Pyright, `git diff --check`, and the disposable PostgreSQL stage when
   available. Synchronize only implementation-derived privacy/observability
   references and run `graphify update .` after implementation.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migrations:** no migration or durable-data rewrite is expected. The
  change prevents new log leakage and does not alter valid classifications,
  retries, dead letters, or stored context.
- **Compatibility:** preserve direct and wrapped JSON parsing, rejection of
  incomplete/truncated responses, the four-field output contract, provider
  retry classification, and all existing worker interfaces.
- **Security/privacy:** no complete or partial model response, customer text,
  title, description, reasoning, token, secret, URL, or media content may enter
  logs, exceptions, metrics, fixtures, or durable operational state as a side
  effect of parser diagnostics.
- **Observability:** retain only safe parser outcome/category and bounded
  structural metadata needed to diagnose malformed or wrapped responses. Do
  not replace the leak with another content preview or encoded copy.
- **Rollout:** local tests and static checks establish the repository evidence;
  they do not prove historical log cleanup or production log-system behavior.

## Tests

- **Unit:** `tests/test_ia_worker_intent.py` — wrapped-response recovery,
  malformed/truncated response rejection, and captured-log assertions that
  sentinel customer content is absent.

Required validation commands:

- `PYTHONPATH=/app python -m pytest -q tests/test_ia_worker_intent.py`
- `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`
- `python -m compileall -q src tests alembic scripts`
- `npx --yes pyright`
- `PYTHONPATH=/app python scripts/verify.py` when disposable PostgreSQL and
  Docker prerequisites are available; report unavailable prerequisites
  separately.
- `git diff --check`

## Acceptance Criteria

- [x] Wrapped valid-response recovery does not log the complete model response
  or any unique title, description, reasoning, or sentinel content.
- [x] Invalid, incomplete, and truncated-response diagnostics do not log a raw
  response prefix or any other customer-derived content.
- [x] Parser logs retain only sanitized, bounded structural metadata and a safe
  outcome/category sufficient for operational diagnosis.
- [x] Existing direct JSON parsing, wrapped recovery, validation, rejection,
  retry/dead-letter behavior, and four-field classification persistence remain
  unchanged.
- [x] Tests prove sensitive-content absence from captured logs for both parser
  branches and continue to cover valid, malformed, and truncated responses.
- [x] No token, secret, URL, media content, raw webhook body, or classification
  content is added to logs, exceptions, metrics, fixtures, or durable state.
- [x] Focused tests, applicable offline/PostgreSQL verification, compileall,
  strict Pyright, and `git diff --check` pass, with unavailable prerequisites
  reported separately from skips and passes.
- [x] SPEC-0001, `README.md`, `PRD.md`, `ARCHITECTURE.md`, and
  `IMPLEMENTATION_PLAN.md` remain consistent with the corrected logging
  boundary; Graphify metadata is updated according to repository workflow.
- [x] The issue is closed only after validation and one focused commit.

## References

- **Primary contract:** `specs/0001-shared-data-and-analysis-contract.md`
  v1.3, §§3–4, especially the prohibition on sensitive content in logs and
  the requirement for sanitized operational reasons.
- **Product/architecture:** `PRD.md` §§6 and 8; `ARCHITECTURE.md` §10; and
  `IMPLEMENTATION_PLAN.md`, completed Persistent conversation analysis and
  recovery.
- **Related implementation:** `src/workers/ia_worker.py:1002-1041` and
  `tests/test_ia_worker_intent.py:135-170`.
- **Related security baseline:** closed issue `0006`, which removed raw-payload
  diagnostic surfaces but does not cover raw Groq-response logging inside the
  classification worker.
- **Non-duplicates:** open issues `0018`–`0023` cover Acessórias transport,
  rate-limit, mapping, Request state, and directory defects; none covers
  customer-derived Groq response content emitted by IA parser logs.

---

## Resolution

<!-- Filled by the agent on close. DO NOT edit manually. -->

Implemented and closed issue 0024.

- Removed `raw_response` and `response_preview` from `_parse_result()` logs.
  Wrapped recovery and invalid output now expose only safe `outcome` values and
  capped structural offset/length metadata. Parsing, validation, retry/dead-letter
  handling, and classification persistence remain unchanged.
- Added focused log-capture regression coverage with separate reasoning, title,
  description, and malformed-response sentinels. The sentinels are absent from
  both parser branches while safe diagnostics remain observable.
- No migration, configuration, provider call, persisted-data, or production-log
  cleanup was added.

Validation performed:

- `PYTHONPATH=/app python -m pytest -q tests/test_ia_worker_intent.py` — **17 passed**.
- `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py` — **193 passed, 61 skipped**; skips are the expected absent disposable PostgreSQL prerequisite in the offline stage.
- `python -m compileall -q src tests alembic scripts` — PASS.
- `npx --yes pyright` — **0 errors, 0 warnings, 0 informations**.
- `PYTHONPATH=/app python scripts/verify.py` — compileall, Pyright, offline **193 passed, 61 skipped**, disposable PostgreSQL/Alembic `0019_acessorias_request_creation`, and PostgreSQL **61 passed, 193 deselected** — PASS.
- Focused `git diff --check` — PASS.
- `graphify update .` — PASS; retained the repository's existing warnings for missing `tree_sitter_sql` and `pyrightconfig.json` graph nodes.

Synchronized SPEC-0001 v1.3, `specs/README.md`, `README.md`, `PRD.md`,
`ARCHITECTURE.md`, and `IMPLEMENTATION_PLAN.md` with the corrected privacy
boundary and current local validation evidence. No provider, Redis, deployment,
production, secret, or PII claim was made.
