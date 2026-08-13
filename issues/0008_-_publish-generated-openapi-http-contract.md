---
id: 0008
title: "Publish the generated OpenAPI HTTP contract"
type: feature
status: closed
priority: high
phase: 1
created_at: 2026-08-13
updated_at: 2026-08-13
closed_at: 2026-08-13
related_issues:
  - "0007"
blocked_by: []
affects:
  - src/api/routes.py
  - src/api/openapi.py
  - README.md
  - tests/
---

## Description

Implement the pending SPEC-0006 increment so the existing FastAPI application
publishes a complete, consumer-usable OpenAPI contract without changing HTTP
behavior.

**Verified gap:** the current `app.openapi()` output has the eight mounted
business paths and the existing `ConversationProcessing` schema, but no
`servers`, tags, security scheme, request-body contract, useful response
schemas, or operation examples. The generated operations otherwise expose only
their current default responses, and the README has a route table but no links
or concise consumer introduction for `/openapi.json`, `/docs`, and `/redoc`.
The runtime already sets the application title/version, preserves FastAPI's
documentation URLs, and implements the response/error behavior described by
SPEC-0006; this issue documents that behavior rather than widening it.

Expected outcome: internal consumers can use the generated OpenAPI document,
Swagger UI, ReDoc, and the README introduction to understand the exact current
HTTP surface, conditional webhook HMAC, response variants, identifiers,
processing states, and error boundaries without inferring versioned routes,
authentication, production URLs, or unsupported guarantees.

## Scope

### In scope

- Add generated OpenAPI metadata, tags, schemas, examples, parameters,
  request-body documentation, response documentation, and security metadata
  for exactly the eight mounted business operations in SPEC-0006:
  `/health`, `/queues`, `/webhook/digisac`, the three conversation routes, and
  the two cycle routes.
- Represent the current JSON bodies and status/error variants faithfully,
  including webhook `200`/`202`/`400`/`401` behavior, health `503`, query
  `404`/`422` behavior, cycle/result projections, the `limit` default and
  runtime clamp, and the distinction between mapped database failure and
  unmapped Redis failure.
- Keep schemas and examples derived from the current FastAPI/Pydantic and
  handler/database projections. Reuse schemas where the wire shape is shared,
  while preserving real nullable/variant fields and the distinction between
  `conversation_id`, `cycle_id`, and `classification_public_id`.
- Add a concise README “API HTTP” introduction with the development base URL,
  supported unversioned routes, conditional webhook HMAC, query security
  boundary, result/state/error overview, and links to the three FastAPI
  documentation URLs.
- Add focused tests for the generated document and documentation endpoints,
  then run the repository validation required by the specification.

### Out of scope

- Changing handler bodies, status codes, validation order, normalization,
  idempotency, durable cycle/media behavior, queue publication, database
  projections, or any other business semantics to make them easier to document.
- Adding response enforcement that drops fields or changes serialization unless
  the implementation proves byte-for-byte compatible behavior at the existing
  HTTP boundary; a documentation schema is not permission to stabilize an
  otherwise variable API.
- Adding business endpoints, `/v1` or `/v2` aliases, query authentication,
  authorization, rate limiting, a universal error envelope, polling/SLA
  promises, or stronger Redis/production guarantees.
- Adding migrations, changing persisted schemas, changing infrastructure or
  deployment configuration, calling external providers, or running a live
  webhook as part of canonical automation.
- Resolving the three SPEC-0006 implementation gaps by changing the API:
  response-shape stabilization, the persisted-state versus `ProcessingStatus`
  `processing` discrepancy, and a new stable Redis failure response. Document
  the observed behavior and keep those changes as separate future work.

## Implementation Plan

1. Reconfirm the current source contract before editing: inspect the FastAPI
   application metadata and route decorators, `ConversationProcessing` and
   `IAAnalysisResult`, webhook signature middleware and parser, the queue
   metrics dictionary, the cycle/result database projections, and the existing
   README route table. Capture the current `app.openapi()` path set as the
   invariant: exactly the eight business operations, with FastAPI's
   `/openapi.json`, `/docs`, and `/redoc` remaining documentation URLs rather
   than additional business paths.
2. Add documentation metadata and generated schema composition at the existing
   FastAPI/Pydantic boundary. Set the effective application title/version and a
   safe development `servers` value from the existing runtime configuration;
   do not invent a production URL or a second manually maintained OpenAPI
   file. Tag operations only as `Webhook DigiSac`, `Operações`, `Conversas`,
   and `Ciclos`, and ensure every operation appears exactly once at its current
   unversioned path.
3. Document the webhook as a permissive JSON object with `event`/`data`
   examples for ticket and message events, without requiring unsupported
   fields or exposing URLs, credentials, raw bodies, or binary media. Add the
   HMAC-SHA256 header scheme and explain that `X-Digisac-Signature` is required
   only when `WEBHOOK_SECRET` is configured; preserve the middleware's
   before-parse ordering and plain/`sha256=` digest forms. Describe only the
   observed accepted, ignored, duplicate, malformed, and invalid-signature
   responses and sanitized ignored reasons.
4. Document `/health` and `/queues` from their actual dictionaries, including
   queue/dead-letter integer fields and the optional empty cycle-metrics map.
   Document the conversation and cycle endpoints from their actual Pydantic
   model and database projections: reusable classification/result schemas,
   all serialized cycle fields only, UUID/timestamp formats where applicable,
   nullable fields, valid persisted states excluding emitted `processing`,
   external versus public identifiers, `limit=50` and the 1–100 runtime clamp,
   and the exact known `404`/`422` details. Do not advertise a UUID format for
   textual `conversation_id` or promise a result merely because a cycle is
   terminal.
5. Add sanitized, fictitious examples for success, ignored/duplicate webhook,
   malformed and unauthorized webhook, healthy/unavailable health, queue
   metrics, intermediate/terminal processing, completed classification, and
   missing resources. Use `$ref` for genuinely shared shapes, validate every
   example against its documented schema, and ensure no secret, token, signed
   URL, raw request material, or binary artifact appears in generated output.
6. Add a focused test module under `tests/` that inspects `app.openapi()` and
   the `/openapi.json`, `/docs`, and `/redoc` responses without requiring
   Redis, PostgreSQL, DigiSac, Groq, or the opt-in live webhook. Assert the
   path/tag/security/response/schema/example invariants and the absence of
   `/v1`, `/v2`, removed diagnostic routes, query security schemes, and
   sensitive example content. Keep existing handler tests as the behavioral
   authority and do not replace them with schema-only assertions.
7. Update the README API section to point to the confirmed local
   documentation URLs and explain the consumer contract without claiming
   deployment or production readiness. Run the focused tests, the canonical
   offline suite, compileall, strict Pyright, and the full verification runner
   when available; record exact observed results. Run `graphify update .`,
   synchronize `IMPLEMENTATION_PLAN.md` and SPEC-0006/index status with the
   evidence, inspect the final diff, and close through one focused commit.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migrations:** none. Do not alter tables, projections, persisted JSON,
  or durable identifiers; no migration or backfill is authorized.
- **Compatibility:** preserve the eight current unversioned paths, FastAPI's
  default documentation URLs, response bodies/status codes, conditional HMAC
  behavior, and current lack of query authentication. Any API-shape change
  requires a separate approved specification and issue.
- **Security:** describe the webhook HMAC scheme only as conditional on
  `WEBHOOK_SECRET`; never publish a secret or imply that queries have a
  credential they do not have. Examples, descriptions, README text, and UI
  output must exclude raw webhook bodies, received headers, tokens, signed
  download URLs, and binary media. Do not reintroduce `/webhook/debug`.
- **Observability:** documentation may describe current queue/cycle status and
  sanitized error fields, but must not promise stable formats for unmapped
  Redis/server failures, SLAs, polling intervals, or classification completion
  after an asynchronous `202`.
- **Rollout:** documentation/runtime-schema metadata only; no production
  deployment, provider call, live webhook, Redis operation, or database target
  is required.

## Tests

- **Focused OpenAPI:** generated document is valid OpenAPI 3.x; title/version,
  servers, four tags, eight paths, methods, request body, parameters,
  responses, reusable schemas, security metadata, and examples match
  SPEC-0006 and the current code.
- **Endpoint documentation:** `/openapi.json`, `/docs`, and `/redoc` return
  usable documentation responses without external services.
- **Contract safety:** tests cover webhook HMAC conditionality and
  before-parse errors, `400`/`200`/`202` variants, health `503`, query/cycle
  `404`, `limit` validation, nullable projections, identifier distinctions,
  and the absence of a fabricated universal error or Redis `503` contract.
- **Privacy/compatibility:** examples and descriptions contain no secret,
  token, raw payload/header, signed URL, binary media, `/v1`, `/v2`, or removed
  debug route; existing route behavior and response bodies remain covered by
  the existing tests.
- **Repository validation:**
  `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`,
  `python -m compileall -q src tests alembic scripts`,
  `npx --yes pyright`, and `PYTHONPATH=/app python scripts/verify.py` when the
  runner prerequisites are available. Report unavailable PostgreSQL/runtime
  stages separately rather than treating skips as external verification.
- **Graph/documentation:** `graphify update .`, README/reference checks, and
  final `git diff --check` are required before closure.

## Acceptance Criteria

- [x] `app.openapi()` produces a valid OpenAPI 3.x document whose title and
  version match the effective FastAPI application metadata and whose only
  business paths are the eight currently mounted operations.
- [x] The generated document contains the four required tags, a safe verified
  development server value, operation summaries/descriptions, and no
  `/v1/...`, `/v2/...`, `/webhook/debug`, or unmounted business route.
- [x] Swagger UI and ReDoc remain available at `/docs` and `/redoc`, and
  `/openapi.json` serves the generated document without requiring external
  services.
- [x] The webhook schema documents the permissive JSON envelope, supported
  ticket/message examples, safe media metadata, and all observed `200`, `202`,
  `400`, and conditional `401` variants without changing handler behavior.
- [x] The HMAC header security scheme documents SHA-256 plain and
  `sha256=` forms, applies only to the webhook operation, explains the
  `WEBHOOK_SECRET` conditionality, and does not create query authentication.
- [x] Health and queue schemas document the actual success fields, database
  `503` detail, cycle-metrics map/empty capability case, and the absence of a
  fabricated stable Redis failure response.
- [x] Conversation and cycle schemas document only serialized fields and
  actual projections, distinguish `conversation_id`, `cycle_id`, and
  `classification_public_id`, represent nullable/intermediate/terminal states,
  and do not present `processing` as an emitted persisted state.
- [x] The conversation-cycle `limit` parameter documents default `50`, the
  runtime clamp `1–100`, and FastAPI type-validation `422` without claiming
  range rejection that the handler does not perform.
- [x] Known `404` result/entity responses, result-unavailable behavior, and
  the distinction between terminal cycle status and classification availability
  are represented without inventing UUID validation or an SLA.
- [x] Every included example validates against its referenced schema and all
  generated documentation/README content is free of secrets, tokens, signed
  URLs, raw webhook data/headers, and binary media.
- [x] README includes the concise “API HTTP” consumer introduction, local base
  URL, supported unversioned surface, HMAC/query-security boundary, state and
  error overview, and links to `/openapi.json`, `/docs`, and `/redoc`.
- [x] Focused OpenAPI and documentation-endpoint tests pass without external
  services, and the existing behavioral suite remains green with
  `tests/test_webhook_local.py` excluded from canonical automation.
- [x] Compileall, strict Pyright, applicable canonical verification, targeted
  documentation searches, and `git diff --check` pass with exact results
  recorded; unavailable runtime stages are explicitly separated from passes.
- [x] `graphify update .` succeeds, SPEC-0006/index and
  `IMPLEMENTATION_PLAN.md` are synchronized with observed evidence, and no
  prohibited application semantics or unrelated files are changed.
- [x] The issue is closed only after all criteria are met and the implementation
  plus required documentation/plan updates are included in one focused commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — Phase 1, item 2's approved
  SPEC-0006 increment. Phase 2, item 3 remains blocked on a separately
  authorized production acceptance
  decision and is not part of this issue.
- Primary specification: `specs/0006-api-documentation-and-openapi-contract.md`
  v1.1 — complete OpenAPI/Swagger/ReDoc, endpoint, security, privacy, README,
  and validation contract.
- Required dependencies: SPEC-0001 v1.1, SPEC-0002 v1.5, SPEC-0003 v1.3,
  SPEC-0004 v1.4, and SPEC-0005 v1.1; all are active/completed baselines and
  no open or in-progress issue duplicates this outcome.
- Related issue: `0007` established the current persistent-only,
  unversioned-route, and verification-evidence documentation baseline.
- Current evidence: `src/api/routes.py`, `src/api/middleware.py`,
  `src/core/models.py`, the cycle/result projections in `src/core/db.py`,
  `src/core/config.py`, `main.py`, `README.md`, and the current generated
  `app.openapi()` output.

---

## Resolution

Implemented and published the generated HTTP documentation contract without
changing handler behavior.

- Added `src/api/openapi.py`, which composes FastAPI's generated document with
  source-backed schemas for the webhook, health/queues, conversation results,
  and persisted cycle projections; it also adds the safe local server, four
  tags, conditional webhook HMAC scheme, operation descriptions, status/error
  variants, parameters, and sanitized examples.
- Installed the cached composition from `src/api/routes.py` and added
  `tests/test_openapi_contract.py` covering the eight business paths, schema
  projections, security boundary, examples, validation responses, and
  `/openapi.json`, `/docs`, and `/redoc` without external services.
- Added the concise `API HTTP` README introduction and synchronized SPEC-0006
  v1.1, `specs/README.md`, and `IMPLEMENTATION_PLAN.md`. The implementation
  deliberately does not add response enforcement, migrations, aliases,
  query authentication, production URLs, or a stable unmapped Redis error.

Validation performed:

- `PYTHONPATH=/app python -m pytest -q tests/test_openapi_contract.py` — **5
  passed**.
- `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py` —
  **127 passed, 33 skipped**.
- `PYTHONPATH=/app python scripts/verify.py` — compileall and strict Pyright
  passed; disposable PostgreSQL 16 reached Alembic head
  `0014_retry_scheduling`; PostgreSQL tests **33 passed, 127 deselected**;
  scoped Compose resources were removed by the runner.
- Targeted webhook regression tests — **33 passed**; documentation/reference
  searches, `git diff --check`, and `graphify update .` passed. The database
  skips and disposable runner do not establish Redis, DigiSac, Groq, replica,
  deployment, or production evidence.
