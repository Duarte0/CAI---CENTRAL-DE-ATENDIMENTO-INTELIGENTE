---
id: 0025
title: "Stop logging raw DigiSac webhook extracted values"
type: bug
status: closed
priority: high
phase: 3
created_at: 2026-08-17
updated_at: 2026-08-17
closed_at: 2026-08-17
related_issues:
  - "0006"
  - "0024"
blocked_by: []
affects:
  - src/api/routes.py
  - src/core/models.py
  - tests/test_webhook_diagnostic_surfaces.py
  - tests/test_webhook_payload.py
  - specs/0001-shared-data-and-analysis-contract.md
  - specs/0002-digisac-webhook-and-query-api.md
  - README.md
  - PRD.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
---

## Description

The production DigiSac webhook parser logs the values extracted from the
incoming envelope, including customer message content. This violates the
approved privacy boundary even though the raw-payload diagnostic endpoints
were removed: a normal `POST /webhook/digisac` processing path can still place
customer text in application logs.

**Plan/spec references:** the completed persistent webhook baseline in
`IMPLEMENTATION_PLAN.md`, `SPEC-0001` v1.4 §§3–4, `SPEC-0002` v1.6 §§3 and 5,
`PRD.md` §8, and `ARCHITECTURE.md` §10. These contracts allow safe IDs, event
and field metadata, and sanitized reasons, but prohibit raw webhook bodies and
sensitive content in normal logs and operational responses.

**Dependencies:** the existing webhook parser/model and its focused route and
payload tests. No product, provider, migration, authorization, or data
retention decision is required. Closed issue `0006` removed the dedicated raw
payload routes; this issue covers the separate normal-ingestion logging path
that remained in `parse_webhook_payload()`. Open issue `0024` covers raw Groq
classification-response logging and does not cover webhook extraction logs.

**Root cause:** `WebhookPayload.extraction_debug()` returns extracted values,
including `content`, at `src/core/models.py:142-180`, and
`parse_webhook_payload()` logs the complete returned mapping with
`logger.info(..., payload.extraction_debug())` at
`src/api/routes.py:424-425`. The method is called after HMAC validation, so a
valid customer message reaches this log path. The source currently makes no
distinction between safe source/path metadata and customer-derived values.

**Reproduction:**

1. Build a valid `message.created` JSON envelope with `data.content` set to a
   unique sentinel such as `CUSTOMER_WEBHOOK_SENTINEL_7f3b`.
2. Call `parse_webhook_payload()` with that body and capture the
   `src.api.routes` logger at `INFO` level.
3. Observe the `Digisac webhook field extraction` record contains the sentinel
   under `content.value`.

A direct runtime probe against the current checkout returned
`sentinel_logged=True` and showed the complete sentinel in the log record.
The existing focused tests pass (`7 passed` for
`tests/test_webhook_diagnostic_surfaces.py` and `tests/test_webhook_payload.py`),
but the diagnostic-surface test uses a value in an unknown field for an
unsupported event and therefore does not exercise the known content field
that `extraction_debug()` emits.

**Actual behaviour:** a valid webhook containing a message body logs that
body, and the same mechanism can log other extracted values such as sender or
timestamp values. The response remains sanitized, but logs are an operational
surface retained outside the request and parser boundary.

**Expected behaviour:** normal webhook parsing and ingestion must emit only
safe event, field-name/source, identifier, presence/type, count, and
sanitized-reason metadata. No complete or partial message text, raw body,
secret, token, signed URL, binary media, or other customer-derived value may
appear in logs, exceptions, metrics, or ordinary responses. Parsing,
normalization, HMAC ordering, ignored-event behavior, and downstream durable
processing must remain unchanged.

## Scope

### In scope

- Remove customer-derived values from the production parser's extraction log
  boundary while retaining the safe diagnostics needed to identify the event,
  extracted field sources, safe identifiers, and structural outcome.
- Audit all current callers of `WebhookPayload.extraction_debug()` and adjacent
  webhook parser logs for the same value leakage; preserve the model's
  application-facing extraction behavior unless a caller's logging contract
  requires a narrowly scoped safe representation.
- Add deterministic regression coverage through the parser/HTTP boundary for
  direct and nested message content, unique sentinels, malformed/ignored
  webhook paths, and the continued absence of raw content from responses and
  captured logs.

### Out of scope

- Changing HMAC verification, webhook schemas, accepted event types, message
  normalization, idempotency, media reservation, cycle creation, or durable
  classification data.
- Reintroducing or replacing debug/replay/admin endpoints, changing query
  authentication, deleting historical logs, or changing log-retention and
  deployment configuration.
- Changing the four-field IA contract or the Groq parser logging covered by
  issue `0024`; changing provider, Acessórias, identity, mapping, or Request
  behavior.
- Adding content hashing, encoding, previews, or another reversible/content-
  derived diagnostic in place of the current raw value.

## Implementation Plan

1. Reconfirm the privacy and observability rules in `SPEC-0001` and `SPEC-0002`,
   enumerate every `extraction_debug()` caller and parser log argument, and
   distinguish values required by application processing from values permitted
   in operational logs.
2. Correct the production webhook logging boundary so logs contain only the
   contract-approved safe metadata. Preserve HMAC-before-parse ordering,
   accepted/ignored responses, extraction for downstream processing, and
   sanitized exception behavior. Ensure nested `message.body`/`data.content`
   and any equivalent content aliases cannot leak through a different log
   branch.
3. Add regression tests that capture logs with unique customer-content,
   sender, URL, and secret-like sentinels for direct and nested envelopes;
   assert their absence while checking that safe event/source/status metadata
   remains observable. Keep the existing invalid-signature short-circuit and
   route-not-found coverage.
4. Run the focused webhook tests, applicable offline suite, compileall, strict
   Pyright, `git diff --check`, and the disposable PostgreSQL stage when
   available. Synchronize only implementation-derived privacy/observability
   documentation and run `graphify update .` after implementation.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migrations:** no migration or durable-data rewrite is expected. The
  change prevents new log disclosure and must not remove intended persisted
  message/classification data.
- **Compatibility:** preserve the mounted webhook path, HMAC behavior,
  response status/reason contracts, payload normalization, idempotency, media
  queues, persistent cycles, and all query routes.
- **Security/privacy:** no raw webhook body, message text, PII, secret, token,
  signed URL, binary media, or reversible/content-derived representation may
  enter logs, metrics, exceptions, fixtures, or ordinary responses as a side
  effect of parser diagnostics.
- **Observability:** retain bounded safe event, field/source, identifier,
  presence/type, count, and sanitized-reason metadata sufficient to diagnose
  extraction without reconstructing the input.
- **Rollout:** local focused/offline/static evidence establishes repository
  behavior only; it does not remove or audit historical production logs and
  does not prove deployment configuration.

## Tests

- **Focused:** `tests/test_webhook_diagnostic_surfaces.py`,
  `tests/test_webhook_payload.py`, and the relevant parser/adapter tests —
  direct/nested content sentinel absence, safe metadata retention, HMAC
  short-circuiting, ignored events, and unchanged normalization.
- **Offline:**
  `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`
- **Static:** `python -m compileall -q src tests alembic scripts` and
  `npx --yes pyright`.
- **Canonical:** `PYTHONPATH=/app python scripts/verify.py` when disposable
  PostgreSQL/Docker prerequisites are available; report unavailable
  prerequisites separately from skips and passes.
- **Hygiene:** `git diff --check` and a focused search confirming no raw
  extraction values remain in supported webhook logs.

## Acceptance Criteria

- [x] A valid direct `data.content` message sentinel never appears in captured
  parser, route, or application logs.
- [x] Nested `message.body`/equivalent content aliases and other customer-
  derived text cannot appear through another extraction-debug or adjacent
  parser logging branch.
- [x] Sender/contact values, signed/download URLs, secrets, tokens, binary
  media, and raw webhook bodies are absent from logs, exceptions, metrics,
  fixtures, and ordinary webhook responses.
- [x] Safe event, field/source, identifier, presence/type, count, and
  sanitized-reason metadata remains sufficient for operational diagnosis and
  is bounded/non-reversible.
- [x] HMAC validation still precedes parsing; invalid signatures do not reach
  extraction, and accepted/ignored status and reason behavior is unchanged.
- [x] Downstream payload extraction, message normalization, media reservation,
  idempotency, cycle creation, and classification persistence remain
  unchanged.
- [x] Focused tests cover direct and nested content leaks and pass together
  with the applicable offline/PostgreSQL verification, compileall, strict
  Pyright, and `git diff --check`, with unavailable prerequisites reported
  separately.
- [x] `SPEC-0001`, `SPEC-0002`, `README.md`, `PRD.md`, `ARCHITECTURE.md`, and
  `IMPLEMENTATION_PLAN.md` remain consistent with the corrected logging
  boundary; Graphify metadata is updated according to repository workflow.
- [x] The issue is closed only after validation and one focused commit.

## References

- **Primary contracts:** `specs/0001-shared-data-and-analysis-contract.md`
  v1.4 §§3–4 and `specs/0002-digisac-webhook-and-query-api.md` v1.6 §§3 and 5;
  both prohibit raw webhook/customer content in normal logs and responses.
- **Product/architecture:** `PRD.md` §8 and `ARCHITECTURE.md` §10, which
  require safe operational logs without raw bodies or sensitive content.
- **Current source evidence:** `src/api/routes.py:423-451` logs the safe
  `payload.extraction_debug()` projection; `src/core/models.py:142-202` emits
  only presence/type/source metadata; `src/api/middleware.py` performs HMAC
  validation before this parser.
- **Current test evidence:**
  `tests/test_webhook_diagnostic_surfaces.py` covers direct and nested content,
  sender, origin, URL, and token sentinels at the HTTP boundary;
  `tests/test_webhook_payload.py` verifies safe source metadata without a raw
  `value` field.
- **Related security work:** closed issue `0006` removed the raw-payload
  diagnostic endpoints but explicitly left `WebhookPayload.extraction_debug()`
  as an internal diagnostic helper; open issue `0024` is limited to raw Groq
  classification responses. Neither covers this normal-ingestion extraction
  log path.

---

## Resolution

Implemented the privacy boundary at the normal webhook parser. `WebhookPayload`
continues to return original values through its application-facing extraction
methods, while `extraction_debug()` now emits only bounded `present`, `type`,
and `source` metadata. Parser-adjacent logs sanitize event, origin, message
type, key counts, and parse/validation reasons; HMAC ordering, ignored-event
responses, normalization, media reservation, idempotency, cycles, and
classification persistence are unchanged.

Focused tests cover direct `data.content` and nested `message.body` sentinels,
sender/contact values, arbitrary origin values, signed URLs, secret-like
tokens, ignored events, and invalid-signature short-circuiting. No migration,
backfill, provider call, credential, retention change, or deployment change
was made.

Documentation was synchronized in SPEC-0001 v1.4, SPEC-0002 v1.6, README.md,
PRD.md, ARCHITECTURE.md, and IMPLEMENTATION_PLAN.md. `graphify update .`
completed successfully; its known optional `tree_sitter_sql` and community
label warnings do not affect the code graph update.

Validation performed:

- `PYTHONPATH=/app python -m pytest -q tests/test_webhook_diagnostic_surfaces.py tests/test_webhook_payload.py` — **9 passed**.
- `python -m compileall -q src tests alembic scripts` — passed.
- `npx --yes pyright` — **0 errors, 0 warnings, 0 informations**.
- `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py` — **195 passed, 61 skipped**.
- `PYTHONPATH=/app python scripts/verify.py` — all stages passed; Alembic head `0019_acessorias_request_creation`; disposable PostgreSQL pytest **61 passed, 195 deselected**.
- `git -c safe.directory=/app diff --check` — passed.
- `graphify update .` — passed; graph refreshed to **1,839 nodes and 3,933 edges**.
