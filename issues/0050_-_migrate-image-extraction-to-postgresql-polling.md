---
id: 0050
title: "Migrate image extraction work from Redis queue to PostgreSQL polling"
type: refactor
status: open
priority: high
phase: 5
created_at: 2026-09-02
updated_at: 2026-09-02
closed_at: ~
related_issues: ["0037", "0046", "0048", "0049"]
blocked_by: ["0048"]
affects:
  - src/api/routes.py
  - src/workers/ia_worker.py
  - src/workers/image_worker.py
  - src/core/durable_media_repository.py
  - src/core/redis_client.py
  - src/core/config.py
  - alembic/versions/0013_conversation_cycles.py
  - tests/test_image_extraction.py
  - tests/test_media_scheduling.py
  - tests/test_operational_recovery_db.py
  - tests/test_webhook_adapter.py
  - README.md
  - ARCHITECTURE.md
  - specs/0001-ia-classification.md
---

## Description

Image extraction has durable state in `message_image_extractions`, but active work is still transported through the Redis list `image_extraction_queue`, with `image_extraction_dead_letter` used for terminal/safety copies. The API and IA worker publish jobs, while the image worker and recovery routines inspect and mutate Redis lists with non-atomic full scans.

The current runtime snapshot showed approximately 65 pending image rows and 65 unique queue entries, plus two unique dead-letter entries with no observed duplicate at that point. That is a healthy snapshot, not proof that the design is race-free. The worker still has independent producers, a check-then-publish recovery path and a permanent-failure branch that can append another dead-letter copy without first removing an existing one.

This issue migrates image extraction to the same PostgreSQL-native work model as 0048. The durable row, retry schedule, lease and media-finalization gate must be sufficient to recover work. Redis must not be needed to preserve, deduplicate or wake an image extraction attempt.

### Confirmed current behavior and risks

- `enqueue_image_extraction()` reserves the durable row and then calls `RPUSH image_extraction_queue`.
- `IAWorker._ensure_media_jobs()` can reserve and publish an image job independently of the API path.
- `ImageExtractionWorker.process_job()` claims the durable row after `LPOP`, removes matching queue entries through a full scan, invokes the provider and persists the outcome.
- Transient failures persist a future retry but do not themselves enqueue a durable replacement; recovery later performs a Redis membership scan and may publish.
- Stale-job recovery performs a database recovery claim, then checks Redis membership and publishes when the ID is absent. The check and publish are not atomic.
- Permanent failure can append to the image dead-letter list without first removing an existing matching safety copy. Recovery also builds jobs by message ID without a complete dead-letter deduplication step.
- The durable repository already stores pending/processing/completed/failed state, attempts, `next_attempt_at`, leases, error information and publication markers. The migration should make those fields authoritative.

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

- Normal processing works with a Redis client that raises for all image list operations, proving PostgreSQL is sufficient.
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
- API acknowledgment succeeds after durable reservation even when Redis is unavailable for image publication.
- The IA worker can wake/observe an image row through PostgreSQL without `RPUSH image_extraction_queue`.
- The cutover dry run reports physical queue/dead-letter counts, unique message IDs, repetitions, malformed entries and durable statuses.
- Duplicate Redis entries are coalesced in the audit report without creating duplicate durable jobs.
- Unknown/malformed entries are preserved or quarantined according to the runbook; no broad flush or silent delete is allowed.
- Cutover apply is idempotent, bounded and auditable.

### Regression and operational tests

- Existing `tests/test_image_extraction.py`, media scheduling and media recovery tests remain green, with assertions moved from Redis presence to durable claim/state where appropriate.
- Issue 0046’s `waiting_media`/`media_blocked` behavior and classification context tests remain green.
- A source-level guard fails if active image code publishes to or consumes the legacy image lists after cutover.
- Disposable Compose verification succeeds with Redis unavailable after startup while PostgreSQL polling still processes/retries image work.

## Acceptance Criteria

- [ ] Image extraction is claimed, retried and recovered entirely through PostgreSQL.
- [ ] API, IA wake-up, retry and stale recovery paths no longer publish active image work to Redis.
- [ ] Cooldown, retry schedule, lease expiry, duplicate reservation, invalid output and worker crash cases are tested.
- [ ] The nonempty valid-text completion and media-blocking contracts remain intact.
- [ ] Duplicate terminal/dead-letter behavior is eliminated or isolated to a deduplicated, transitional compatibility path.
- [ ] Legacy image queue/dead-letter cutover has a dry run, audit evidence and idempotent apply procedure.
- [ ] Metrics and documentation use PostgreSQL as the image work authority.
- [ ] No unrelated Redis data is deleted.

## References

- `src/api/routes.py`: image reservation and Redis publication.
- `src/workers/ia_worker.py`: image media wake-up path.
- `src/workers/image_worker.py`: current consumer, retry, dead-letter and stale recovery behavior.
- `src/core/durable_media_repository.py`: durable image state, lease and recovery operations.
- `tests/test_image_extraction.py`, `tests/test_media_scheduling.py`, `tests/test_operational_recovery_db.py`: current coverage and gaps.
- `issues/0037_-_audit-and-remove-legacy-redis-residues.md`: safe Redis cleanup boundaries.
- `issues/0046_-_block-finalization-until-audio-transcription.md`: media-finalization/blocking contract.
- `issues/0048_-_prevent-duplicate-ia-cycle-queue-republication.md`: parent migration and cutover rules.

## Resolution

<!-- Complete with the deployed worker topology, legacy-list inventory/cutover evidence and test commands. -->
