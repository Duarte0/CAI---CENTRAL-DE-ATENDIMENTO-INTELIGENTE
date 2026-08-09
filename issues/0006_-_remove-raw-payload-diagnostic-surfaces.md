---
id: 0006
title: "Remove raw-payload diagnostic surfaces"
type: refactor
status: closed
priority: high
phase: 1
created_at: 2026-08-09
updated_at: 2026-08-09
closed_at: 2026-08-09
related_issues:
  - "0003"
  - "0005"
blocked_by: []
affects:
  - src/api/routes.py
  - src/api/debug_routes.py
  - tests/
  - README.md
  - PRD.md
  - ARCHITECTURE.md
  - specs/0002-digisac-webhook-and-query-api.md
  - specs/README.md
  - IMPLEMENTATION_PLAN.md
---

## Description

Deliver `IMPLEMENTATION_PLAN.md` Phase 1, item 5: eliminate raw webhook bodies
from diagnostic HTTP surfaces before any exposure or contract expansion.

**Verified gap:** `src/api/routes.py` still mounts `POST /webhook/debug`, which
validates the webhook signature but returns the received `raw_payload`. The
separate `src/api/debug_routes.py` handler is not mounted, but prints headers
and the raw request body and remains an avoidable sensitive surface. README,
PRD, and architecture currently describe the mounted endpoint as an internal
exception, while SPEC-0002 v1.4 and the plan now record removal as the approved
security/operations decision. No open or in-progress issue covers this removal
outcome.

Expected outcome: DigiSac ingestion continues through the authenticated
production webhook and normal operational responses expose only their existing
sanitized fields; neither debug endpoint exists or returns/logs raw request
bodies. The operator relies on existing safe structured logs and operational
metrics. This issue does not add authentication, authorization, redaction
storage, a replacement debug API, or a public API version.

## Scope

### In scope

- Delete the unmounted raw-header/raw-body debug handler and remove the mounted
  `POST /webhook/debug` route and its route-specific response contract.
- Preserve production webhook HMAC-before-parse behavior, normalized ingestion,
  ignored-event responses, durable cycle/media processing, and existing safe
  operational endpoints.
- Add or update focused route tests proving the removed endpoint is unavailable,
  production webhook behavior is unchanged, invalid signatures do not reach
  parsing, and normal logs/responses do not expose raw bodies.
- Remove both diagnostic surfaces and the obsolete raw-payload exception from
  implementation-derived README, PRD, architecture, SPEC-0002, and the
  specification index as applicable; synchronize the plan status and evidence
  only after validation.
- Run `graphify update .` after implementation changes and close the work in one
  focused commit.

### Out of scope

- Adding a replacement debug, replay, payload-inspection, admin, or public API
  endpoint; changing normal webhook response fields; or changing query-route
  versioning.
- Adding query authentication/authorization, rate limiting, retention or audit
  storage, LGPD automation, or a broader logging redesign.
- Changing HMAC configuration, webhook normalization rules, Redis/PostgreSQL
  contracts, persistent-cycle behavior, media retry/recovery, migrations,
  production data, or deployment topology.
- Removing `WebhookPayload.extraction_debug()` where it is used for safe
  structured internal diagnostics; only raw-payload surfaces and misleading
  documentation are in scope.

## Implementation Plan

1. Trace the application route registration and existing debug tests before
   editing. Confirm the mounted production webhook remains the sole ingestion
   route and that no deployment or client contract requires either diagnostic
   endpoint. Treat the current plan/spec approval as the governing decision;
   update stale PRD wording rather than preserving the old exception.
2. Remove the `/webhook/debug` handler from the FastAPI route module and delete
   the unmounted handler module. Do not weaken `verify_webhook_signature` or
   alter `parse_webhook_payload` for the production route: HMAC validation must
   still precede JSON parsing, and production logs may contain only event,
   field-name, ID, status, and sanitized reason metadata—not raw bodies,
   secrets, tokens, signed URLs, or binary media.
3. Add focused tests at the existing HTTP/test boundary. Assert that requests
   to `/webhook/debug` and the obsolete `/debug/webhook` path are not served,
   that a valid production webhook still follows its current accepted/ignored
   behavior, that an invalid signature is rejected before parsing or side
   effects, and that representative raw content does not appear in captured
   production logs or ordinary responses. Avoid claiming PostgreSQL or live
   DigiSac coverage unless those prerequisites are actually supplied.
4. Remove route tables, prose, security exceptions, and references that claim
   either debug surface exists. Keep the canonical statement that ordinary
   operational responses and logs exclude raw payloads, and document no new
   diagnostic mechanism. Synchronize `IMPLEMENTATION_PLAN.md`, SPEC-0002's
   version/status and index entry, and any verification counts only from
   observed results.
5. Run the focused tests, the canonical offline suite excluding
   `tests/test_webhook_local.py`, compileall, strict Pyright, and
   `PYTHONPATH=/app python scripts/verify.py` as applicable to the changed
   behavior. Run `graphify update .`, inspect the final diff for raw-payload
   references and accidental route changes, then close via the plan sync and
   one focused commit.

## Data, migration, compatibility, security, and rollout

- **Data/migrations:** no schema, backfill, data deletion, or Redis cleanup is
  required. Existing durable PostgreSQL cycle/media state is untouched.
- **Compatibility:** this intentionally removes the internal diagnostic route
  and the unmounted legacy handler. The production `/webhook/digisac` contract,
  HMAC requirement when configured, status codes, and normalized processing
  remain compatible.
- **Security:** no raw request body, header dump, secret, token, signed URL, or
  binary media may be returned or emitted by a supported diagnostic or
  production path. Do not replace removal with weaker access control or an
  undocumented internal exception.
- **Observability:** retain safe event/field-name and sanitized reason logs,
  health/queue/cycle metrics, and existing error handling. Do not log raw input
  while testing rejected routes or malformed production requests.
- **Rollout:** deploy the route removal with the normal application rollout;
  no production migration, Redis purge, provider call, or live webhook test is
  required by this issue.

## Tests

- **Focused HTTP/security:** route-not-found behavior for both obsolete paths;
  valid and invalid signature behavior on `POST /webhook/digisac`; no raw-body
  leakage in captured logs or ordinary responses.
- **Regression:** existing webhook normalization, ignored-event, bot, media,
  persistent-cycle, and query tests remain green without adding a debug route.
- **Static:** `python -m compileall -q src tests alembic scripts` and
  `npx --yes pyright`.
- **Canonical verification:**
  `PYTHONPATH=/app pytest -q --ignore=tests/test_webhook_local.py` and
  `PYTHONPATH=/app python scripts/verify.py`; report PostgreSQL/runtime evidence
  only when the runner actually executes it.
- **Graph:** `graphify update .` after implementation and documentation changes.

## Acceptance Criteria

- [x] `POST /webhook/debug` is no longer registered and requests to it receive
  the application's standard not-found behavior.
- [x] The unmounted `/debug/webhook` handler and its raw header/body printing
  are removed; no supported route returns or logs a raw request body.
- [x] `POST /webhook/digisac` still validates HMAC before parsing when a secret
  is configured, rejects invalid signatures without normalization or side
  effects, and preserves its existing accepted/ignored response behavior.
- [x] Focused tests prove both obsolete routes are unavailable and cover raw
  content non-disclosure in normal production logs/responses without requiring
  a live DigiSac service.
- [x] Existing durable cycle, media reservation/recovery, idempotency, and
  query-route behavior remains green; no PostgreSQL or Redis data contract is
  changed.
- [x] No migration, backfill, Redis purge, new credential, access-control
  exception, replacement diagnostic endpoint, or public API version is added.
- [x] README, PRD, architecture, SPEC-0002, and the specification index no
  longer advertise either raw-payload diagnostic surface, and their wording
  matches the implementation and recorded removal decision.
- [x] `IMPLEMENTATION_PLAN.md` is synchronized with observed completion
  evidence, `graphify update .` succeeds, and the final diff contains no
  unrelated files or stale debug-route references.
- [x] Compileall, Pyright, focused tests, and the applicable canonical runner
  pass with exact results recorded; `tests/test_webhook_local.py` remains
  opt-in and no production environment is used.
- [x] The issue is closed only after all criteria are met and the implementation
  plus required documentation/plan updates are included in one focused commit.

## References

- Plan: `IMPLEMENTATION_PLAN.md` — Phase 1, item 5 (selected); prior completed
  baseline items 1–4 and the persistent-only refactor item 8 are prerequisites.
- Primary spec: `specs/0002-digisac-webhook-and-query-api.md` v1.4 — HMAC,
  sanitized operational surfaces, and approved removal of both debug surfaces.
- Related specs: `specs/0001-shared-data-and-analysis-contract.md` v1.1 for
  durable authority/privacy boundaries and `specs/0003-durable-finalization-and-media.md`
  v1.3 for unchanged cycle/media invariants.
- Completed issues: `0003` reconciled the implementation-derived diagnostic
  wording; `0005` removed the legacy finalization path without changing this
  security decision. No open/in-progress issue duplicates this outcome.
- Current evidence: `src/api/routes.py`, `src/api/debug_routes.py`,
  `src/api/middleware.py`, existing webhook tests, and the documented local
  verification runner.

---

## Resolution

The two raw-payload diagnostic surfaces were removed. The mounted
`POST /webhook/debug` handler was deleted from `src/api/routes.py`, and the
unmounted `src/api/debug_routes.py` module was deleted. The production
`POST /webhook/digisac` route and its HMAC-before-parse dependency were left
unchanged. Focused HTTP tests cover both `404` routes, valid HMAC with the
existing sanitized ignored response, invalid-signature short-circuiting, and
raw-marker absence from responses and captured logs. `pyrightconfig.json` was
updated to remove the deleted module from strict analysis.

No migration, backfill, Redis purge, credential, access-control exception,
replacement endpoint, API version, or production data change was made.
README, PRD, ARCHITECTURE, SPEC-0002 (v1.4), `specs/README.md`, and
`IMPLEMENTATION_PLAN.md` now describe the production webhook as the sole
ingestion surface and the absence of a raw-payload diagnostic contract.

Validation performed:

- `PYTHONPATH=/app pytest -q tests/test_webhook_diagnostic_surfaces.py tests/test_webhook_security.py tests/test_webhook_adapter.py tests/test_history_finalization_webhook.py tests/test_webhook_payload.py` — **34 passed**.
- `python -m compileall -q src tests alembic scripts` — passed.
- `npx --yes pyright` — **0 errors, 0 warnings, 0 informations**.
- `PYTHONPATH=/app pytest -q --ignore=tests/test_webhook_local.py` — **122 passed, 33 skipped**.
- `PYTHONPATH=/app python scripts/verify.py` — all stages passed; disposable PostgreSQL pytest **33 passed, 122 deselected**, Alembic head `0014_retry_scheduling`.
- `graphify update .` — succeeded and refreshed the code graph.

The known Graphify warning about the optional `tree_sitter_sql` dependency did
not prevent the required update. No production environment was used.
