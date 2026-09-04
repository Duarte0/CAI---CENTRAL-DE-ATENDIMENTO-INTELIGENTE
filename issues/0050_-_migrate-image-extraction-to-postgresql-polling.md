---
id: 0050
title: "Migrate image extraction work from Redis queue to PostgreSQL polling"
type: refactor
status: closed
priority: high
phase: 5
created_at: 2026-09-02
updated_at: 2026-09-02
closed_at: 2026-09-02
related_issues: ["0037", "0046", "0048", "0049"]
blocked_by: ["0048"]
affects:
  - src/api/routes.py
  - src/workers/ia_worker.py
  - src/workers/image_worker.py
  - src/core/durable_media_repository.py
  - src/core/config.py
  - src/core/db.py
  - src/api/openapi.py
  - scripts/retire_legacy_image_queue.py
  - alembic/versions/0024_durable_media_leases.py
  - tests/test_image_extraction.py
  - tests/test_postgres_evolution.py
  - tests/test_retire_legacy_image_queue.py
  - tests/test_media_scheduling.py
  - tests/test_media_detection.py
  - tests/test_operational_recovery_db.py
  - tests/test_webhook_adapter.py
  - tests/test_openapi_contract.py
  - README.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
  - specs/0001-shared-data-and-analysis-contract.md
  - specs/0003-durable-finalization-and-media.md
  - specs/0006-api-documentation-and-openapi-contract.md
  - specs/README.md
---

## Description

Image extraction had durable state in `message_image_extractions`, but active
work was transported through the Redis list `image_extraction_queue`, with
`image_extraction_dead_letter` used for terminal/safety copies. The API and IA
worker published jobs, while the image worker and recovery routines inspected
and mutated Redis lists with non-atomic full scans.

The current runtime snapshot showed approximately 65 pending image rows and 65 unique queue entries, plus two unique dead-letter entries with no observed duplicate at that point. That is a healthy snapshot, not proof that the design is race-free. The worker still has independent producers, a check-then-publish recovery path and a permanent-failure branch that can append another dead-letter copy without first removing an existing one.

This issue migrates image extraction to the same PostgreSQL-native work model as
0048 and 0049. The durable row, retry schedule, lease and media-finalization
gate are sufficient to recover work. Redis is not needed to preserve,
deduplicate or wake an image extraction attempt.

### Confirmed current behavior and risks

- `enqueue_image_extraction()` reserved the durable row and then called
  `RPUSH image_extraction_queue`.
- `IAWorker._ensure_media_jobs()` could reserve and publish an image job
  independently of the API path.
- `ImageExtractionWorker.process_job()` claimed the durable row after `LPOP`,
  removed matching queue entries through a full scan, invoked the provider and
  persisted the outcome.
- Transient failures persisted a future retry but did not themselves enqueue a
  durable replacement; recovery later performed a Redis membership scan and
  could publish.
- Stale-job recovery performed a database recovery claim, then checked Redis
  membership and published when the ID was absent. The check and publish were
  not atomic.
- Permanent failure could append to the image dead-letter list without first
  removing an existing matching safety copy.
- The durable repository already stored pending/processing/completed/failed
  state, attempts, `next_attempt_at`, leases, error information and publication
  markers. The migration makes the durable state authoritative and leaves old
  list entries only for bounded audit/cutover.

### Goals and invariants

- One durable image-extraction row exists per source message.
- One worker lease owns a due `message_id` at a time.
- Future retries are retained in PostgreSQL and are not claimed or moved earlier.
- Provider cooldown is checked before claiming work.
- Lease expiry is sufficient to recover a crashed worker.
- Only a completed extraction with valid nonempty text can unblock IA context, preserving the media-finalization contract.
- Retryable provider/download/decoding failures, permanent failures and media-blocked states retain their current semantics.
- Repeated webhook delivery, repeated IA wake-up and worker restart cannot create duplicate provider work through a transport list.
- A terminal failure is represented durably once; any temporary dead-letter compatibility record is deduplicated and explicitly transitional.

### Scope

### Included

- Add a PostgreSQL operation to claim due pending/stale image extraction rows with `FOR UPDATE SKIP LOCKED`, owner and lease.
- Refactor `ImageExtractionWorker` to poll and claim PostgreSQL directly. Active processing must not use `LPOP`, `LRANGE`, `RPUSH` or `LREM` on the image queue/dead-letter lists.
- Change API and IA media wake-up paths to reserve/update durable state only.
- Persist all retry timing and failure metadata in PostgreSQL, including provider rate-limit information and the next eligible time.
- Preserve MIME detection, download limits, image validation, provider response parsing and the existing “usable text” rule.
- Define a dry-run-first procedure to inventory and retire legacy image queue/dead-letter entries. Unknown or malformed entries must remain auditable.
- Fix the duplicate terminal-dead-letter behavior as part of the cutover or remove the list dependency so it cannot affect active processing.
- Update metrics, docs and operational recovery instructions.

### Delivered implementation

- Reused Alembic `0024_durable_media_leases`: its lease columns and polling
  indexes cover `message_image_extractions`; no schema migration was necessary
  for this cutover.
- Added the durable image claim and metrics façade operations. The claim is due
  aware, bounded by `FOR UPDATE SKIP LOCKED`, assigns a unique worker owner and
  expiry, increments attempts, clears the current schedule, and recovers stale
  processing rows.
- Changed API admission, IA media reconciliation and the image worker to use
  PostgreSQL only for active image work. Retry timing and provider cooldown are
  persisted; completion requires nonempty extracted text; stale ownership cannot
  overwrite a newer claim.
- Added `/queues` durable image metrics (`image_due`, `image_scheduled`,
  `image_leased`, `image_stale`, `image_completed`, `image_failed`) while
  retaining legacy Redis list counts solely as cutover visibility.
- Added `scripts.retire_legacy_image_queue`, a bounded dry-run-first inventory
  and explicitly confirmed apply path. It reports duplicates, malformed and
  unknown IDs, refuses truncated apply snapshots, preserves unsafe entries and
  retains transient dead-letter evidence.

### Explicitly out of scope

- Audio transcription; see 0049.
- IA cycle transport; see 0048.
- A change to the vision model, prompt, image download endpoint or extraction quality criteria.
- Broad deletion of Redis keys or replay of every historical failed image.

## Implementation Plan

1. Define the image durable state machine and document how `pending`, `processing`, `completed`, `failed`, retryable error and media-blocked outcomes map to polling.
2. Implement `claim_next_image_extraction` (or the project’s equivalent name) with a bounded, due-aware, lease-safe PostgreSQL query.
3. Refactor the worker loop to check provider cooldown before claim, process the durable claim, persist status and let the database schedule the next attempt.
4. Remove active image `RPUSH` producers from API enqueue, IA media wake-up, retry and stale-recovery paths. If lower latency is needed, use an in-process wake-up signal that does not carry correctness state.
5. Make lease expiry recover stale `processing` rows through the repository. A worker restart must not reconstruct jobs from Redis.
6. Ensure permanent failure is idempotent and does not create repeated durable or compatibility dead-letter records. Preserve the diagnostic payload without storing sensitive full image content.
7. Inventory the current Redis queue/dead-letter lists, compare every valid ID with durable state, and retire/quarantine them through an idempotent, bounded cutover operation.
8. Replace Redis list lengths and membership scans in operational metrics with PostgreSQL counts for due, future, processing, stale, completed, failed and blocked rows.
9. Update the media specifications, architecture and runbooks, including the interaction with issue 0046’s media-blocking behavior.

## Tests

### Repository and concurrency tests

- A due pending image row is claimed exactly once with owner, lease and attempt metadata.
- Concurrent claimers cannot receive the same `message_id` and can process separate due rows concurrently.
- A future retry is not claimed early, even if its publication marker is null/stale.
- An unexpired processing lease is preserved; an expired lease is recovered once and made eligible according to policy.
- Completed valid extraction is not re-claimed. Failed/permanent rows follow the documented recovery rule and are not silently retried.
- Retry status and `next_attempt_at` are persisted atomically with the failure metadata.
- Completion/failure/retry transitions are idempotent and reject stale ownership where required.
- The claim query is bounded and uses the approved recovery index; add an `EXPLAIN` assertion where supported.

### Image worker tests

- Normal processing works without a Redis client or image list operations, proving PostgreSQL is sufficient.
- Two workers cannot invoke the provider twice for the same claim.
- Provider cooldown occurs before claim/recovery and leaves rows and retry times unchanged.
- HTTP 429/503, timeout, transient download error and retryable provider response schedule one future retry without immediate Redis publication.
- The worker honors `Retry-After`/provider cooldown rules and never retries before the persisted due time.
- A permanent provider/validation error records failed state once. Reprocessing a legacy safety copy cannot append duplicate dead-letter records.
- Empty or invalid provider output follows the existing retry/terminal policy and cannot mark a row as valid completed text.
- MIME detection and allowed image size/download behavior remain unchanged.
- A worker crash after claim is recoverable after lease expiry, and a second worker cannot process before expiry.
- A successful extraction marks the durable row completed, removes any transitional duplicate safety copies and allows the existing media-finalization transition to proceed.
- A pending image does not unblock IA; completion with valid text does unblock it exactly once.

### API, webhook and legacy cutover tests

- Repeated webhook delivery and repeated image reservation remain idempotent and do not reset a future retry schedule.
- API acknowledgment succeeds after durable reservation without image publication to Redis.
- The IA worker can wake/observe an image row through PostgreSQL without `RPUSH image_extraction_queue`.
- The cutover dry run reports physical queue/dead-letter counts, unique message IDs, repetitions, malformed entries and durable statuses.
- Duplicate Redis entries are coalesced in the audit report without creating duplicate durable jobs.
- Unknown/malformed entries are preserved or quarantined according to the runbook; no broad flush or silent delete is allowed.
- Cutover apply is idempotent, bounded and auditable.

### Regression and operational tests

- Existing `tests/test_image_extraction.py`, media scheduling and media recovery tests remain green, with assertions moved from Redis presence to durable claim/state where appropriate.
- Issue 0046’s `waiting_media`/`media_blocked` behavior and classification context tests remain green.
- A source-level guard fails if active image code publishes to or consumes the legacy image lists after cutover.
- Disposable PostgreSQL verification covers image polling while the image worker has no Redis dependency; the live rollout still requires Redis for API/IA flows and is not represented as a Redis outage test.

## Acceptance Criteria

- [x] Image extraction is claimed, retried and recovered entirely through PostgreSQL.
- [x] API, IA wake-up, retry and stale recovery paths no longer publish active image work to Redis.
- [x] Cooldown, retry schedule, lease expiry, duplicate reservation, invalid output and worker crash cases are tested.
- [x] The nonempty valid-text completion and media-blocking contracts remain intact.
- [x] Duplicate terminal/dead-letter behavior is eliminated from active processing and isolated to a bounded transitional compatibility path.
- [x] Legacy image queue/dead-letter cutover has a dry run, audit evidence and idempotent, confirmation-gated apply procedure.
- [x] Metrics and documentation use PostgreSQL as the image work authority.
- [x] No unrelated Redis data is deleted; no live legacy list was deleted or replayed during this implementation.

## References

- `src/api/routes.py`: durable image reservation and queue metrics.
- `src/workers/ia_worker.py`: image media wake-up path.
- `src/workers/image_worker.py`: PostgreSQL polling, lease, provider retry and durable outcome behavior.
- `src/core/durable_media_repository.py`: durable image state, lease and recovery operations.
- `tests/test_image_extraction.py`, `tests/test_media_scheduling.py`, `tests/test_operational_recovery_db.py`: current coverage and gaps.
- `issues/0037_-_audit-and-remove-legacy-redis-residues.md`: safe Redis cleanup boundaries.
- `issues/0046_-_block-finalization-until-audio-transcription.md`: media-finalization/blocking contract.
- `issues/0048_-_prevent-duplicate-ia-cycle-queue-republication.md`: parent migration and cutover rules.

## Resolution

Implemented and verified locally on 2026-09-02.

- `image_worker` now polls `message_image_extractions` in PostgreSQL with an
  atomic due/stale claim, `FOR UPDATE SKIP LOCKED`, owner and lease. The API and
  `IAWorker` reserve durable rows without publishing image work to Redis.
- Retry, provider cooldown, lease recovery, permanent failure and valid
  nonempty-text completion remain durable. A completed image is the only image
  state that can satisfy the existing IA media gate.
- `GET /queues` exposes PostgreSQL image work metrics. Legacy image lists remain
  visible for bounded cutover audit only; no live list deletion or replay was
  performed.
- `scripts/retire_legacy_image_queue` is dry-run by default and requires the
  exact confirmation phrase for bounded apply. Unknown/malformed entries and
  transient dead-letter evidence are retained.
- Verification: `python -m compileall -q src scripts tests alembic/versions`,
  `git diff --check`, full offline suite **280 passed, 84 skipped**, and a
  disposable PostgreSQL run with Alembic head `0024_durable_media_leases`
  passed **34 focused tests**.
- The implementation updates SPEC-0001, SPEC-0003, SPEC-0006, their index,
  README, ARCHITECTURE and IMPLEMENTATION_PLAN. Production/provider acceptance
  and any live legacy-list retirement remain operationally separate.
