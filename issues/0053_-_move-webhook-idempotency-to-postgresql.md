---
id: 0053
title: "Move webhook event idempotency from Redis to PostgreSQL"
type: refactor
status: closed
priority: high
phase: 6
created_at: 2026-09-03
updated_at: 2026-09-03
closed_at: 2026-09-03
related_issues: ["0030", "0037", "0052"]
blocked_by: ["0052"]
affects:
  - src/utils/idempotency.py
  - src/api/routes.py
  - src/core/db.py
  - src/core/webhook_event_repository.py
  - alembic/versions/
  - tests/test_idempotency.py
  - tests/test_history_finalization_webhook.py
  - tests/test_webhook_adapter.py
  - tests/test_postgres_evolution.py
  - tests/test_openapi_contract.py
  - README.md
  - ARCHITECTURE.md
  - specs/0001-shared-data-and-analysis-contract.md
  - specs/0002-digisac-webhook-and-query-api.md
  - specs/0006-api-documentation-and-openapi-contract.md
  - IMPLEMENTATION_PLAN.md
---

## Description

The active webhook path still depends on Redis for event idempotency. The API
computes a SHA-256 event digest and executes `SET processed:{event_id} 1 NX EX
3600`. This is atomic inside Redis, but it makes Redis a runtime dependency for
the webhook even though ticket lifecycle idempotency and all durable cycle,
media and contact state already live in PostgreSQL.

The target is to preserve the current behavior — one winner for concurrent
deliveries and suppression for one hour — with a PostgreSQL-owned, sanitized
event ledger. The ledger must contain only the opaque digest and timestamps; it
must never store the webhook body, message content, contact values, secrets,
signed URLs or provider payloads.

This is a correctness migration, not a request to make all webhook history
permanent. The current Redis key expires after one hour, so the PostgreSQL
contract must define equivalent expiry and bounded cleanup rather than growing
without a retention policy.

## Confirmed current behavior and boundaries

- `IdempotencyService.generate_event_id()` derives a digest from selected
  conversation/event/message/timestamp/content fields.
- `try_mark_processed()` uses Redis `NX` and a one-hour TTL.
- The webhook reserves durable audio/image rows before checking the event key;
  that ordering protects a durable reservation from a duplicate delivery and
  must remain unchanged.
- Contact hydration is a separate PostgreSQL effect and must not be folded into
  the event ledger or allowed to clear its own retry backoff.
- Ticket assignment and cycle lifecycle idempotency already have PostgreSQL
  event-key boundaries, but they do not replace the generic message webhook
  digest used by this route.
- A mixed deployment in which old API instances use only Redis and new
  instances use only PostgreSQL can accept the same event twice. Rollout must
  either prevent mixed versions or implement and test a transitional dual
  boundary explicitly.

## Goals and invariants

- Preserve the current one-hour duplicate-suppression contract.
- Guarantee exactly one PostgreSQL winner for concurrent identical digests.
- Expire a digest only after its persisted expiry time; never suppress a new
  event because an old record was retained past its contract.
- Keep event derivation deterministic and free of raw payload persistence.
- Fail closed on a database outage: do not report a webhook as accepted when
  the idempotency decision was not durably recorded.
- Preserve reservation-before-idempotency ordering and existing HTTP responses.
- Make expired-row cleanup bounded, observable and independent of request-path
  correctness.
- Remove Redis from `IdempotencyService` and from the webhook dependency graph
  without changing hydration, ticket, media or IA semantics.

## Scope

### Included

- Add an Alembic-owned table, preferably `webhook_event_keys`, with a
  nonblank digest primary/unique key, `first_seen_at TIMESTAMPTZ` and
  `expires_at TIMESTAMPTZ`, plus an index supporting bounded expiry cleanup.
- Add a repository operation that atomically returns whether the digest was
  newly accepted, handles an expired conflicting row according to the chosen
  SQL contract, and is safe under PostgreSQL concurrency.
- Replace the Redis-backed `IdempotencyService` implementation while retaining
  a compatibility-facing service method if callers/tests require it.
- Add a bounded maintenance operation for expired rows with before/after
  counts and no payload access.
- Preserve webhook ordering, response status/body, HMAC-before-normalization,
  media reservation semantics and contact-hydration independence.
- Define a coordinated deployment or dual-read/dual-write bridge and document
  how old Redis markers are handled during the handoff.
- Update OpenAPI, specs, README, architecture and operational verification.

### Explicitly out of scope

- Removing Redis from IA status/result compatibility views or Compose; those are
  later issues 0054 and 0055.
- Replacing ticket assignment, cycle or classification idempotency contracts.
- Storing raw webhook bodies or extending the event ledger into a message
  archive.
- Changing the event digest fields without a separately reviewed compatibility
  decision.
- Deleting historical PostgreSQL rows outside expired event-key records.

## Implementation Plan

1. Freeze the current digest and expiry contract in tests and documentation.
   Verify whether any external consumer depends on the Redis key names before
   changing them; the application itself must not expose those names as API
   data.
2. Add the migration and repository boundary. Enforce nonblank digest,
   `TIMESTAMPTZ` fields, unique ownership and an index for bounded expiry
   cleanup. The request operation must use a unique constraint/transactional
   SQL path rather than a read-then-insert race.
3. Define the expired-conflict algorithm. It must allow a digest to be
   accepted again only when the stored `expires_at` is due, while ensuring
   concurrent requests cannot both replace the same expired row. Add a bounded
   cleanup path for unrelated expired rows; cleanup failure must not make an
   otherwise valid event appear processed.
4. Refactor the webhook to call PostgreSQL after durable media reservation and
   before acknowledging the event. Preserve the existing duplicate response
   and ensure the contact hydration request remains a separate effect whose
   backoff is governed by issue 0051.
5. Plan the deployment boundary. Preferred rollout is migration first, drain
   old API instances, deploy the PostgreSQL idempotency version, then verify
   no old version remains. If zero-downtime rollout is required, implement a
   temporary bridge that reads/writes both stores with an explicit consistency
   policy and an expiry date; do not assume two non-atomic stores are safe by
   default.
6. Remove the Redis import/dependency from the idempotency service and update
   source guards, tests and docs. Keep Redis available for the remaining IA
   compatibility path until issue 0054/0055 completes.
7. Add dashboards/logs for accepted, duplicate, expired-replaced, cleanup,
   database-error and conflict outcomes without logging the digest source
   payload. Record the migration and rollout evidence before closing.

## Data, compatibility and rollback

- The digest is the only event identity persisted by this table. A digest must
  be validated as a nonblank SHA-256-compatible opaque value at the boundary.
- `expires_at` must be compared using database time or a clearly documented
  UTC application time; tests must cover timezone-aware values and the
  project’s `APP_TIMEZONE` behavior.
- A database outage must result in a non-successful webhook response and must
  not acknowledge the event as processed. A Redis outage after this issue must
  not affect webhook idempotency.
- Rollback must be coordinated with the deployment mode. If old code is
  restored, its Redis marker behavior must be re-enabled only while Redis
  still exists and the handoff state is understood; never blindly replay the
  webhook stream.
- Expired ledger cleanup may be interrupted and rerun. It must report the
  number removed and never use an unbounded delete without a batch limit.

## Tests

### Repository and migration tests

- Two concurrent PostgreSQL calls for one digest return exactly one accepted
  result and one duplicate result.
- Distinct digests are accepted independently.
- An unexpired digest remains a duplicate.
- An expired digest can be accepted once, and two concurrent replacements do
  not both win.
- Blank/invalid digests are rejected without insertion.
- Bounded cleanup removes only expired rows and is safe to rerun.
- Schema upgrade/downgrade checks preserve unrelated durable data and expose
  the expected index/constraint.

### Webhook behavior tests

- Duplicate webhook delivery preserves the current response contract.
- Audio/image durable reservation still happens before event idempotency, and
  the duplicate path does not erase the reservation.
- Contact hydration remains independent and repeated references preserve its
  persisted future backoff.
- HMAC validation and malformed-payload rejection happen before any ledger
  insertion.
- Database failure does not return a successful receipt.

### Compatibility and static tests

- `tests/test_idempotency.py` no longer requires a Redis fake for the active
  service and has PostgreSQL coverage for atomicity.
- A source-level guard detects Redis imports/operations in the active webhook
  idempotency path after cutover, while allowing explicitly scoped maintenance
  tooling and the remaining IA compatibility keys.
- OpenAPI and route tests reflect unchanged supported response semantics.
- Full compileall, Pyright, offline and disposable PostgreSQL verification
  pass.

## Acceptance Criteria

- [x] PostgreSQL owns webhook event idempotency with an additive Alembic
  migration, unique digest constraint, expiry timestamps and bounded cleanup.
- [x] Concurrent identical webhook events produce exactly one accepted event;
  duplicates and expired events follow the documented one-hour contract.
- [x] No raw payload, message/contact value, secret, URL or provider response is
  stored in or logged from the ledger.
- [x] Media reservation ordering, contact hydration/backoff, HMAC behavior and
  HTTP response compatibility are preserved.
- [x] The rollout prevents mixed Redis-only/PostgreSQL-only idempotency gaps or
  provides a tested transitional bridge with a removal date.
- [x] Webhook success fails closed when PostgreSQL cannot make the idempotency
  decision; no Redis availability is required for this decision.
- [x] Expired-row cleanup is bounded, observable and safely repeatable.
- [x] Tests, compileall, Pyright, canonical verification and documentation are
  synchronized with accurate evidence.

## References

- `src/utils/idempotency.py`: deterministic digest derivation and compatibility
  service delegating to PostgreSQL.
- `src/core/webhook_event_repository.py` and
  `alembic/versions/0025_webhook_event_keys.py`: atomic ledger, expiry and
  bounded cleanup.
- `scripts/migrate_legacy_webhook_idempotency.py` and
  `scripts/cleanup_expired_webhook_event_keys.py`: report-bound handoff and
  bounded maintenance operations.
- `src/api/routes.py`: event digest, media reservation ordering and webhook
  acknowledgment path.
- `src/core/ticket_assignment_repository.py` and
  `src/core/conversation_cycle_repository.py`: existing PostgreSQL idempotency
  boundaries that must not be conflated with this digest ledger.
- `issues/0030_-_isolate-ticket-assignment-persistence-from-db-facade.md`:
  assignment event-key boundary.
- `issues/0037_-_audit-and-remove-legacy-redis-residues.md`: Redis safety and
  transient-key boundaries.
- `issues/0051_-_preserve-contact-hydration-backoff.md`: independent contact
  hydration request/backoff contract.
- `specs/0001-shared-data-and-analysis-contract.md`,
  `specs/0002-digisac-webhook-and-query-api.md` and
  `specs/0006-api-documentation-and-openapi-contract.md`.

## Resolution

Closed on 2026-09-03. Commit `db7a077` adds Alembic
`0025_webhook_event_keys`, the PostgreSQL repository and the bounded cleanup
operation. The digest derivation remains unchanged; the active service has no
Redis dependency, and webhook media reservation still precedes the ledger
decision. Concurrent, expiry, privacy, handoff and route-regression tests are
included in the canonical evidence.

The controlled `cai` handoff stopped the old API before migration, applied and
verified head `0025_webhook_event_keys`, and captured a valid PostgreSQL
custom-format recovery point at
`/tmp/cai-0053-reports/cai-postgres-before-0025-db7a077.dump`
(SHA-256 `d482b15b57086e8095c938c86b9c1e2b56ecb8e617448d4d7b1d360295c3fe0c`).
The reviewed dry-run scanned 171 valid live `processed:*` markers without
truncation; apply imported all 171, with
`/tmp/cai-0053-reports/cai-0053-handoff-apply.json` as the report
(SHA-256 `bcedce97eb4f699576f373161301d7af46bca4c5240b2fea48c675644a03e2d5`).
No Redis source marker was deleted. After restart, the PostgreSQL ledger held
176 live rows (171 imported plus five new deliveries), while Redis still held
171 `processed:*` markers. The API and workers were rebuilt from `db7a077`;
internal `/health` returned `{"status":"ok"}`, and the API image contains no
maintenance scripts or Redis reference in the active idempotency module.

Verification: compileall, Pyright, **290 passed / 90 skipped** offline and
**90 passed / 290 deselected** against disposable PostgreSQL 16, with Alembic
head `0025_webhook_event_keys`. The final container update recreated `api`,
`ia_worker`, `audio_worker` and `image_worker`; all were running on the rebuilt
image. `processed:*` remains intentionally retained for the subsequent issues
0054–0056 and its natural TTL; no Redis volume or unrelated durable data was
removed.

Na checagem posterior da atualização final dos containers, `api`, `ia_worker`,
`audio_worker` e `image_worker` continuavam em execução e o health interno
seguia `ok`; o ledger tinha 189 linhas, 181 ainda dentro da janela, e Redis
mantinha 163 marcadores. A redução dos 171 marcadores observados no handoff é
expiração natural, não remoção executada por esta issue.
