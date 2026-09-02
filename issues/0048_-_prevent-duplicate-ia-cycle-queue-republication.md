---
id: 0048
title: "Migrate persistent IA finalization from Redis queue to PostgreSQL polling"
type: bug
status: closed
priority: high
phase: 5
created_at: 2026-09-02
updated_at: 2026-09-02
closed_at: 2026-09-02
related_issues: ["0004", "0005", "0037", "0049", "0050", "0051"]
blocked_by: []
affects:
  - src/api/routes.py
  - src/workers/ia_worker.py
  - src/core/conversation_cycle_repository.py
  - src/core/redis_client.py
  - src/core/config.py
  - alembic/versions/0013_conversation_cycles.py
  - tests/test_ia_worker_retry.py
  - tests/test_operational_recovery_db.py
  - tests/test_history_finalization_webhook.py
  - tests/test_ticket_closure.py
  - tests/test_retire_legacy_ia_queue.py
  - scripts/retire_legacy_ia_queue.py
  - README.md
  - ARCHITECTURE.md
  - PRD.md
  - IMPLEMENTATION_PLAN.md
  - specs/0001-ia-classification.md
  - specs/0002-conversation-cycle-finalization.md
  - specs/0003-conversation-cycle-recovery.md
---

## Description

The persistent transport for IA finalization currently uses the Redis list `ia_queue`. PostgreSQL already stores the authoritative conversation-cycle state, retry schedule, lease and publication marker, but the worker still republishes the same logical cycle to Redis during reconciliation and recovery.

This is the incident behind the observed queue growth: a point-in-time snapshot showed approximately 12.9 thousand physical entries for approximately 104 logical cycle IDs, with individual IDs repeated more than two hundred times. The count is a count of queue items, not complete conversations. The duplicate items do not necessarily create duplicate rows in `ia_classifications` because the classification repository has an idempotency key, but they consume worker/provider capacity and make recovery behavior difficult to audit.

The durable fix is to remove Redis from the persistent IA work path. PostgreSQL must become the queue and claim authority for IA finalization; the IA worker must poll and claim due cycles directly. Redis can remain for unrelated, explicitly scoped compatibility or coordination uses, and the audio/image queues remain separate follow-up migrations tracked by issues 0049 and 0050.

### Confirmed current behavior

The current flow has several independent publishers and a non-atomic transport:

1. A ticket closure creates or updates a conversation cycle and `_publish_cycle()` marks `enqueued_at` before calling `RPUSH ia_queue`.
2. `IAWorker._reconcile_cycles()` calls `get_recoverable_cycles()`, which marks rows with `enqueued_at`, and then unconditionally calls `RPUSH ia_queue` for every returned row.
3. The IA worker runs reconciliation before checking its provider cooldown. During a Groq rate-limit window it does not consume `ia_queue`, but it can continue reconciling and publishing.
4. The publication marker is cleared by the worker claim. If a cycle remains unconsumed, its marker can become stale after `finalization_lease_seconds`, making it eligible for another reconciliation publication even when an old physical Redis item is still present.
5. A future `not_before` item is read with `LPOP` and placed back with `RPUSH`, and provider throttling also returns a popped item to the list. This does not provide durable claim, visibility timeout or an atomic check-and-claim operation.
6. `AsyncRedis` exposes list operations but no atomic claim primitive such as a transactional claim, `LMOVE` or Lua script. A read/check followed by a publish therefore cannot guarantee single-flight behavior across API, worker and recovery processes.

The relevant PostgreSQL behavior already exists in `conversation_cycles`: recoverable status, `next_attempt_at`, `lease_owner`, `lease_expires_at`, `enqueued_at` and a recovery index. The missing capability is a direct “claim next due cycle” operation that does not require publishing a copy to Redis first.

**Root cause:** the system marks a cycle as published in PostgreSQL and then relies on a non-atomic Redis list publication/consumption protocol. Reconciliation can publish again after the marker becomes stale, and it is allowed to continue while the provider is paused. Existing Redis membership is not part of the database claim predicate.

**Reproduction:**

1. Leave one recoverable cycle in `conversation_cycles` and publish one physical entry to `ia_queue`.
2. Pause IA consumption through the provider cooldown window while allowing the reconciliation interval to run.
3. Let the publication marker age past `finalization_lease_seconds` or otherwise make the row eligible for recovery.
4. Run reconciliation repeatedly and inspect `LLEN ia_queue`, the unique cycle IDs and their repetition counts.

**Actual behaviour:** the same logical cycle can be appended repeatedly even though its durable row already represents the work. A provider cooldown can therefore amplify backlog without any new conversation closure.

**Expected behaviour:** a due cycle is claimed once through a PostgreSQL lease. A future or leased cycle is not touched by polling, and no Redis publication is needed to make it recoverable.

### Problem statement

The system has two competing sources of truth for the same work:

- PostgreSQL says whether a cycle is due, leased, retryable or terminal.
- Redis says whether one or more copies happen to be waiting in a list.

Because those states are not atomically coupled, a worker restart, provider cooldown, stale publication marker, concurrent reconciler or retry path can create additional physical copies. A durable classification idempotency key prevents some database duplication but does not prevent repeated provider calls, repeated media inspection and queue starvation.

The target must make it impossible for a cycle to be “published again” merely because a Redis list entry was not consumed. Repeated polling must result in one PostgreSQL lease, not another transport copy.

### Goals and invariants

After this issue is implemented:

- PostgreSQL is the only durable source of pending IA finalization work.
- At most one worker owns a due cycle at a time, enforced by a database claim/lease update.
- A cycle is claimable only when its status is recoverable, `next_attempt_at` is null or due, and no unexpired lease belongs to another worker.
- A future retry remains in PostgreSQL and is invisible to polling until `next_attempt_at`.
- Provider cooldown is checked before claiming work. A cooldown must not mutate the cycle, reset its retry schedule or create another copy.
- A worker crash after a claim is recoverable by lease expiry; a worker crash must not require a Redis item to be recreated.
- Closure, retry and reconciliation are idempotent database transitions. They do not `RPUSH ia_queue`.
- The existing classification idempotency contract remains intact and is not used as a substitute for work claiming.
- No broad Redis deletion, `FLUSHDB`, `FLUSHALL` or unbounded replay is part of the cutover.
- Existing cycles, including cycles waiting for media, remain recoverable according to their persisted state.

### Scope

### Included

- Introduce a repository-level PostgreSQL claim operation for the next due IA cycle using `FOR UPDATE SKIP LOCKED` and an atomic lease update. It must return one complete job payload or a minimal cycle identifier that the worker can load safely.
- Reuse or extend the existing recovery predicate and index rather than creating a second, subtly different definition of “recoverable”.
- Refactor `IAWorker` to poll PostgreSQL, claim a cycle and process that claim. The loop must preserve lease ownership and release/complete/retry through durable state transitions.
- Change closure and all IA recovery/retry producers so they persist the cycle transition only. They must not publish the cycle to `ia_queue`.
- Preserve the current retry classification, `next_attempt_at`, `attempt_count`, media gating, result persistence and terminal classification behavior unless a test demonstrates an existing defect that must be fixed for the migration.
- Define a bounded cutover procedure for the existing `ia_queue`: inventory it, stop competing publishers/consumers as appropriate, make a durable-state comparison, and handle only validated cycle IDs. Unknown or malformed entries must be retained for investigation or moved to an explicitly named quarantine mechanism, never silently discarded.
- Keep `ia_queue` and `ia_dead_letter` observability/compatibility behavior only for the transition period if existing API clients require their counters. The active IA worker must not depend on them after cutover.
- Update architecture, specifications, operational runbooks and API/health documentation to state that Redis is no longer the persistent IA queue.

### Explicitly out of scope

- Removing Redis from the entire application. Redis remains in scope for separate compatibility keys and other workers until their own issues are completed.
- Migrating audio transcription or image extraction in this issue; those changes are 0049 and 0050.
- Changing Groq models, prompts, classification labels, confidence semantics or the `ia_classifications` schema.
- Replaying all historical conversations or re-opening terminal cycles without an explicit operator-approved recovery procedure.
- Changing Acessórias request creation. That flow already uses a durable operation/lease contract and is not a Redis queue.
- Fixing contact hydration backoff. That is a distinct database-only issue, 0051.

## Implementation Plan

1. Add repository tests and implementation for a direct claim operation. The SQL must lock due candidate rows with `FOR UPDATE SKIP LOCKED`, set the lease atomically and return only one claim to a worker. The operation must be safe when multiple workers poll at the same time.
2. Define the worker state machine around the claim: claim, process, mark completed, schedule transient retry, mark terminal failure, or release a claim when processing cannot start. Every branch must verify the lease owner where the existing repository contract supports it.
3. Move provider-window checking ahead of polling and claiming. A throttled worker should wait without touching the database work schedule except for metrics.
4. Remove IA `RPUSH` calls from ticket closure, reconciliation, `_ensure_media_jobs` wake-up paths and IA retry paths. “Wake up” must mean a database state transition or an immediate poll signal local to the worker, not publication of a duplicate list item.
5. Keep `enqueued_at` as a compatibility/observability field during the migration if existing queries or API responses depend on it. Stop using it as a Redis publication gate; remove or rename it only in a separately verified schema/documentation step.
6. Add a one-time, dry-run-first cutover command or operational script. It must report physical Redis entries, unique IDs, repetitions, malformed/unknown IDs, durable status and whether each ID is already terminal, in progress, due or future-scheduled. The apply mode must be idempotent and auditable.
7. Drain or quarantine the legacy IA list under a bounded procedure. Do not re-enqueue every Redis copy. For a valid cycle, the durable PostgreSQL row is the only input to the new worker; duplicate list copies are evidence for the cutover report.
8. Update health/queue metrics so the IA backlog is read from PostgreSQL (due, future, leased, terminal/retryable) and is not inferred from Redis `LLEN`.
9. Update `README.md`, `ARCHITECTURE.md`, `PRD.md`, `IMPLEMENTATION_PLAN.md` and the affected specifications with the new topology, recovery guarantees, rollout and rollback boundaries.
10. Deploy in a controlled sequence: code that can read the new durable state, stop old IA publishers, start the DB-polling worker, verify no Redis publisher remains, monitor claims/retries/provider calls, and only then retire the legacy queue compatibility path.

## Data, compatibility and operational requirements

- Prefer the existing `conversation_cycles` schema. If a migration is needed, it must be additive, reversible where practical, and include indexes justified by `EXPLAIN` on the real recovery predicate.
- Do not rely on Redis delivery semantics for correctness. Redis outages must not lose a cycle that was durably closed; PostgreSQL outages must fail closed without acknowledging work as completed.
- The claim lease duration, polling interval and retry schedule must be configurable and documented. Polling must include bounded backoff/jitter so multiple workers do not create a synchronized database spike.
- Metrics must distinguish due backlog, future backlog, active leases, lease expirations, successful claims, lost lease/claim conflicts, retries, terminal failures and legacy Redis entries seen during cutover.
- Logs must include `cycle_id`, claim owner, attempt number, status transition and a correlation identifier, but must not log full conversation content or secrets.
- Rollback must mean stopping the DB-polling worker and restoring a known compatible application version only after checking whether claimed cycles have leases or persisted terminal results. It must not mean blindly republishing all rows.
- The migration must preserve cycles blocked by pending media. Those cycles must remain persisted as `waiting_media` or the project’s equivalent and become due only through the existing media-finalization transition.

## Tests

The implementation is not complete until the following behaviors are covered with deterministic tests. Tests must use PostgreSQL semantics for claim/recovery tests; an in-memory list fake is insufficient for concurrency guarantees.

### Repository and SQL tests

- `claim_next_cycle` claims one due recoverable cycle and sets the expected owner, lease expiration and processing state atomically.
- Two concurrent claimers cannot receive the same cycle. Each receives a different eligible cycle, or one receives no work.
- A cycle with `next_attempt_at` in the future is not claimed early, including when its `enqueued_at` is null or stale.
- A cycle with an unexpired lease owned by another worker is not claimed.
- An expired lease becomes claimable exactly once and increments/records the recovery attempt according to the existing contract.
- Terminal statuses, `waiting_media` cycles and cycles with an unmet media prerequisite are excluded or handled through the documented transition; no test may accidentally treat every recoverable-looking row as ready for provider processing.
- A retry transition persists `next_attempt_at` and a subsequent claim honors it. Polling repeatedly before that time does not change the schedule.
- Completion and terminal failure are idempotent and reject a stale or wrong lease owner when the repository contract requires ownership.
- The query plan uses the recovery index or an approved replacement, and the query remains bounded by the claim batch size.

### IA worker tests

- The worker processes a database-claimed cycle without calling `rpush`, `lpop`, `lrange` or `lrem` on `ia_queue`.
- Repeated polling of the same due row produces one claim and one provider attempt, not multiple jobs.
- Provider cooldown is evaluated before claim/reconciliation. During cooldown, no cycle is claimed, no `enqueued_at` is reset, no retry schedule is moved and no Redis entry is created.
- Strengthen `tests/test_ia_worker_retry.py::test_active_provider_window_does_not_touch_queue_or_database` so it exercises the real reconciliation path instead of mocking reconciliation into a successful signal; the test must prove that cooldown prevents both claim and reconciliation side effects.
- A provider 429/503 schedules one durable retry with the expected `next_attempt_at`; the worker does not immediately republish the cycle.
- A future retry remains untouched until due. The test advances a controllable clock rather than sleeping for real time.
- A worker crash after claim leaves an expiring lease. Another worker can claim it after the lease expires, while a second worker cannot claim it before expiry.
- A duplicate closure event and a duplicate webhook delivery produce one durable cycle transition and one eventual classification attempt.
- A cycle waiting for transcription/image extraction is not sent to the provider before the media-finalization transition makes it eligible.
- An unexpected exception is persisted or surfaced through the documented durable failure path; it is not hidden in a Redis dead-letter list that the new worker never consumes.
- A worker restart does not require reconstructing a Redis queue from all recoverable cycles.

### API and integration tests

- Closing a ticket persists the cycle and returns the existing acknowledgment/API response without requiring Redis availability for IA publication.
- If Redis is unavailable after a successful database commit, the cycle remains recoverable and appears in the PostgreSQL due backlog.
- A repeated closure/webhook event does not create a second cycle or advance the cycle’s retry schedule unexpectedly.
- Queue/status endpoints and dashboards expose the new PostgreSQL-derived IA counters, with an explicit legacy indicator while Redis compatibility remains.
- The cutover dry run reports the current Redis list accurately: physical count, unique cycle IDs, repetitions, malformed IDs and durable status for each valid ID.
- Cutover apply is idempotent, bounded, produces an audit record/log, and does not delete unknown or malformed entries without explicit quarantine handling.

### Regression and static tests

- Existing IA retry, classification idempotency, webhook closure, ticket closure and media-gating suites remain green.
- `tests/test_operational_recovery_db.py` covers concurrent PostgreSQL claims and lease expiry, not only the old Redis publication marker; any test that asserts queue membership must be rewritten around durable claim state.
- A source-level guard or architecture test fails if active IA production code calls `rpush` for `ia_queue` or if the IA worker consumes that list after the cutover flag is enabled.
- Configuration validation rejects an invalid polling/lease configuration and documents safe defaults.
- The issue’s operational runbook is exercised in a disposable environment with PostgreSQL and Redis so the migration does not rely on host-only behavior.

## Acceptance Criteria

- [x] The IA worker claims due cycles directly from PostgreSQL with a concurrency-safe lease.
- [x] No active IA producer republishes persistent cycles to Redis.
- [x] Provider cooldown, future retry, worker crash and restart scenarios are covered by tests.
- [x] A durable cycle cannot be duplicated by repeated reconciliation, repeated closure events or queue recovery.
- [x] The existing classification idempotency and media gating contracts remain valid.
- [x] The current `ia_queue` backlog has a dry-run report and a bounded, auditable cutover procedure; no broad Redis flush is used.
- [x] IA backlog and lease metrics are derived from PostgreSQL and distinguish due work from future work.
- [x] The documentation accurately describes PostgreSQL as the IA work authority and identifies the remaining media Redis queues.
- [x] Audio, image and hydration follow-up work is linked to 0049, 0050 and 0051 rather than silently expanding this issue.
- [x] Focused tests, the full relevant test suite and the operational cutover verification pass before closing the issue.

## References

- `src/workers/ia_worker.py`: current reconciliation-before-cooldown ordering, Redis consumption and retry republication.
- `src/api/routes.py`: cycle closure publication and media queue producers.
- `src/core/conversation_cycle_repository.py`: durable cycle recovery predicate, publication marker and lease claim.
- `src/core/classification_repository.py`: classification idempotency key; useful for regression, not a queueing solution.
- `src/core/redis_client.py`: limited list API without an atomic claim primitive.
- `alembic/versions/0013_conversation_cycles.py`: existing recovery index and cycle fields.
- `ARCHITECTURE.md`: current Redis topology and persistent IA flow that must be revised.
- `specs/0002-conversation-cycle-finalization.md` and `specs/0003-conversation-cycle-recovery.md`: durable state and recovery requirements.
- `issues/0004_-_verify-durable-operational-recovery-on-runner.md`: previous recovery/deduplication contract and its missing coverage for existing Redis membership.
- `issues/0037_-_audit-and-remove-legacy-redis-residues.md`: prohibition on broad Redis deletion and preservation of active queues during cleanup.
- `issues/0049_-_migrate-audio-transcription-to-postgresql-polling.md`, `issues/0050_-_migrate-image-extraction-to-postgresql-polling.md`, `issues/0051_-_preserve-contact-hydration-backoff.md`: coordinated follow-up issues.

## Resolution

Implemented on 2026-09-02 without a schema migration. `claim_next_cycle()` now selects one due recoverable cycle and writes its lease atomically with `FOR UPDATE SKIP LOCKED`; the worker polls it after provider cooldown checks and no longer consumes, republishes or dead-letters IA Redis jobs. Ticket closure now only persists the cycle. `enqueued_at` remains a compatibility field and is cleared on claim, not used as a publication gate.

`GET /queues` retains legacy IA list lengths for cutover visibility and adds PostgreSQL-derived `ia_due`, `ia_scheduled` and `ia_leased`. The bounded `scripts/retire_legacy_ia_queue.py` dry run compares a limited `ia_queue` snapshot with durable rows; apply requires an explicit confirmation, refuses a truncated snapshot and removes one validated exact list item at a time while retaining malformed/unknown entries.

Focused offline verification passed with `PYTHONPATH=/app pytest -q tests/test_ia_worker_retry.py tests/test_ticket_closure.py tests/test_history_finalization_webhook.py tests/test_retire_legacy_ia_queue.py tests/test_openapi_contract.py tests/test_conversation_cycle_repository.py --ignore=tests/test_webhook_local.py` and `python -m compileall -q src scripts tests`. PostgreSQL/disposable-runner evidence and container rollout are recorded with the implementation commit.
