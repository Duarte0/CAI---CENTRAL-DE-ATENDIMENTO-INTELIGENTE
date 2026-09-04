---
id: 0049
title: "Migrate audio transcription work from Redis queue to PostgreSQL polling"
type: refactor
status: closed
priority: high
phase: 5
created_at: 2026-09-02
updated_at: 2026-09-02
closed_at: 2026-09-02
related_issues: ["0027", "0037", "0048", "0050"]
blocked_by: ["0048"]
affects:
  - src/api/routes.py
  - src/workers/ia_worker.py
  - src/workers/audio_worker.py
  - src/core/durable_media_repository.py
  - src/core/db.py
  - src/core/config.py
  - alembic/versions/0024_durable_media_leases.py
  - docker-compose.yml
  - scripts/retire_legacy_audio_queue.py
  - tests/test_audio_worker.py
  - tests/test_retire_legacy_audio_queue.py
  - tests/test_media_scheduling.py
  - tests/test_operational_recovery_db.py
  - tests/test_webhook_adapter.py
  - README.md
  - ARCHITECTURE.md
  - specs/0001-shared-data-and-analysis-contract.md
  - specs/0003-durable-finalization-and-media.md
  - specs/0006-api-documentation-and-openapi-contract.md
---

## Description

Audio transcription has a durable PostgreSQL row in `message_transcriptions`, but its work transport still relies on the Redis list `audio_transcription_queue` and the Redis list `audio_transcription_dead_letter`. The API, the IA worker’s media wake-up path, the audio worker’s transient retry path and the recovery routines can all publish to Redis. Recovery also checks list membership with `LRANGE` and then performs `RPUSH` as separate operations.

This issue follows 0048 and applies the same durable-work principle to audio: PostgreSQL must decide whether a transcription is pending, due, leased, completed or permanently failed. The audio worker must claim due rows directly from PostgreSQL. Redis must not be required to preserve or deduplicate a transcription attempt.

The current runtime snapshot did not show an audio queue backlog, but an empty queue does not prove the producer/consumer design is safe. The implementation has the same non-atomic check-then-publish pattern that allowed the IA backlog to grow, so this issue is preventive as well as architectural.

### Confirmed current behavior

- `enqueue_audio_transcription()` reserves the durable row and then calls `RPUSH audio_transcription_queue`.
- `IAWorker._ensure_media_jobs()` can reserve a transcription and directly publish it to the same Redis list.
- `AudioTranscriptionWorker.process_job()` claims the durable row after receiving a Redis item, removes matching queue entries with a full-list scan, calls the provider, and republishes transient failures immediately.
- `recover_transient_dead_letters()` and `recover_stale_jobs()` inspect Redis lists with `LRANGE`, derive a set of message IDs and publish with `RPUSH`. The membership check and publication are not atomic across workers.
- Permanent failure handling and safety-copy recovery depend on Redis dead-letter cleanup. The durable status is authoritative, but Redis can contain stale or repeated copies.
- The existing `message_transcriptions` state model already carries the important fields: status, attempts, retry schedule, lease, error metadata, text and publication marker. The migration should use that model rather than introduce a second queue state.

### Goals and invariants

- A transcription is represented by one durable row keyed by `message_id`.
- At most one audio worker owns a due transcription claim at a time.
- `next_attempt_at` is the only authority for when a transient failure can be retried.
- Pending work with a future retry time is not claimed, republished or moved earlier by polling.
- Provider cooldown is checked before claiming work.
- A worker crash is recovered by an expired database lease, not by a Redis safety copy.
- Successful transcription is usable only when the status is `completed` and the persisted text is nonempty, preserving the current media-finalization gate.
- Transient, permanent and media-blocking outcomes retain the semantics documented by issue 0027 and the media specifications.
- Duplicate webhook delivery, duplicate media wake-up and worker restart cannot create duplicate provider attempts solely through transport duplication.

### Scope

### Included

- Add a PostgreSQL repository operation to claim one or a bounded batch of due pending/stale audio rows with `FOR UPDATE SKIP LOCKED`, an owner and a lease.
- Refactor `AudioTranscriptionWorker` to poll and claim PostgreSQL rows directly. The worker must no longer `LPOP`, `LRANGE`, `RPUSH` or `LREM` the active audio queue during normal processing.
- Change API and IA media reservation paths so they only create or wake the durable row. A wake-up may be an immediate local poll signal, but it must not be a second durable queue representation.
- Persist transient retry timing in PostgreSQL and let polling discover the row when it becomes due.
- Persist terminal failure and the diagnostic metadata in PostgreSQL. If a dead-letter compatibility view is retained, it must be derived from durable state or be explicitly transitional.
- Add a bounded, idempotent procedure for inspecting and retiring/importing legacy Redis audio queue/dead-letter entries. Unknown or malformed entries must be retained or quarantined with an audit record.
- Update queue metrics, health checks, architecture documentation, specifications and the issue 0027 retry documentation.

### Explicitly out of scope

- Image extraction; see 0050.
- IA conversation-cycle transport; see 0048.
- Provider/model changes, audio download protocol changes or a rewrite of transcript quality validation.
- Broad Redis cleanup or deletion of unrelated keys; issue 0037 remains authoritative.

## Implementation Plan

1. Document the durable audio state machine and map each current Redis transition to a PostgreSQL transition: pending, processing, completed, failed, retryable and stale lease.
2. Implement `claim_next_transcription` (or the project’s equivalent name) using the existing durable row and recovery predicate. The query must be bounded, due-aware and lease-safe.
3. Refactor the worker loop to check provider cooldown before claiming, claim from PostgreSQL, process the claim and persist the outcome. Keep the worker’s local pacing only as an optimization, never as correctness state.
4. Remove active audio `RPUSH` producers from the API, IA media wake-up and retry/recovery code. Preserve a local wake-up event only if polling latency requires it.
5. Implement lease-expiry recovery through the repository. A stale `processing` row must return to a due pending state according to the existing retry policy without needing the old queue.
6. Define the legacy list cutover: inventory physical entries and unique IDs, compare them to durable rows, preserve evidence, and retire only validated entries after the DB worker is healthy. The procedure must be safe to run twice.
7. Replace Redis `LLEN`/list membership as the primary audio backlog signal with PostgreSQL metrics: due pending, future pending, processing, stale leases, completed, failed and blocked.
8. Update documentation and the operational runbook, explicitly retaining Redis only where another active component still needs it.

## Tests

### Repository and concurrency tests

- A due pending transcription is claimed once with the expected owner, lease and attempt metadata.
- Concurrent PostgreSQL claimers do not receive the same `message_id`; `SKIP LOCKED` allows independent rows to proceed.
- A future `next_attempt_at` row is not claimed early, even when `enqueued_at` is null or stale.
- An unexpired processing lease is not stolen; an expired lease becomes claimable once and is recorded as recovery.
- A completed row with nonempty text is not re-claimed. A completed row with empty text follows the documented failure/blocking path and is never silently treated as usable.
- Retry transition persists the exact next-attempt schedule, and polling before that time is side-effect free.
- Completion, permanent failure and retry transitions are idempotent and lease-owner aware.
- The claim query remains bounded and uses the approved index; add an `EXPLAIN` regression check if the repository’s database test harness supports it.

### Audio worker tests

- Normal audio processing uses only PostgreSQL claim/status operations; a Redis client that raises on audio list operations proves the worker does not depend on the list.
- Two worker instances processing the same due row result in one provider call and one durable claim.
- Provider cooldown happens before claim/recovery. No row is claimed, no retry timestamp is moved and no list item is published during cooldown.
- Transient provider errors, HTTP 429/503, download timeouts and retryable decoding errors set pending with the expected backoff and do not immediately republish.
- A future retry is picked up after a controllable clock crosses `next_attempt_at`.
- Permanent provider errors persist failed state and diagnostic metadata exactly once; repeated polling does not create duplicate dead-letter copies.
- Successful provider output marks the row completed only when the text is valid/nonempty and removes any transitional safety record if one still exists.
- A worker crash after claim is recovered after lease expiry; a second worker cannot process it before expiry.
- A missing/invalid media payload follows the existing terminal or retryable policy and never gets stuck only because Redis is unavailable.
- A duplicate webhook/media reservation is idempotent and does not reset an existing future retry or create another durable row.

### Legacy migration and integration tests

- The cutover dry run reports physical queue count, unique IDs, repetitions, malformed items and the corresponding durable status.
- Duplicate Redis entries for one `message_id` are coalesced in the report without producing duplicate durable work.
- Unknown or malformed entries are preserved/quarantined according to the runbook and are never silently deleted.
- Applying the cutover twice produces the same durable state and audit result.
- API acknowledgment remains successful after the durable reservation even when Redis is down for audio publication.
- IA media wake-up makes a durable row eligible without requiring `RPUSH audio_transcription_queue`.
- Existing issue 0027 tests for retries, backoff, dead-letter semantics and nonempty completion remain green or are updated to assert the PostgreSQL equivalent.

### Static and operational tests

- A source-level guard fails if active audio production code publishes to `audio_transcription_queue` after the cutover flag is enabled.
- Queue/health endpoints report PostgreSQL-derived counts and label any remaining legacy Redis list explicitly.
- A disposable Compose test runs API, PostgreSQL and audio worker with Redis unavailable after startup and verifies durable processing/recovery.

## Acceptance Criteria

- [x] Audio work is claimed and scheduled entirely through PostgreSQL.
- [x] API, IA wake-up, retry and recovery paths no longer publish active audio work to Redis.
- [x] Provider cooldown, due scheduling, lease expiry, duplicate reservations and worker crashes are covered by tests.
- [x] Completed audio is usable only with `completed` status and nonempty text.
- [x] The legacy audio queue/dead-letter contents have a dry-run and idempotent retirement procedure.
- [x] Metrics and documentation describe PostgreSQL as the audio work authority.
- [x] Issue 0027’s retry contract is preserved and its tests assert the new transport behavior.
- [x] No unrelated Redis keys or queues are deleted.

## References

- `src/api/routes.py`: audio reservation and Redis publication path.
- `src/workers/ia_worker.py`: media reservation/wake-up from IA recovery.
- `src/workers/audio_worker.py`: current Redis consumer, retries, dead-letter recovery and stale-job recovery.
- `src/core/durable_media_repository.py`: durable transcription reservation, status transitions, recovery claim and publication marker.
- `tests/test_audio_worker.py`, `tests/test_media_scheduling.py`, `tests/test_operational_recovery_db.py`: current coverage and concurrency gaps.
- `issues/0027_-_align-audio-retry-with-image-recovery.md`: existing audio retry contract.
- `issues/0037_-_audit-and-remove-legacy-redis-residues.md`: safe Redis cleanup boundaries.
- `issues/0048_-_prevent-duplicate-ia-cycle-queue-republication.md`: parent migration and cutover rules.

## Resolution

Implemented and rolled out the audio transport migration:

- added Alembic `0024_durable_media_leases`, with explicit `lease_owner` and
  `lease_expires_at` on durable media rows and polling indexes; pre-existing
  audio `processing` rows are made recoverable from their last `updated_at`, and
  legacy audio publication markers are cleared;
- added an atomic `claim_next_transcription()` repository operation using
  `FOR UPDATE SKIP LOCKED`, due scheduling, owner, lease expiry and stale-lease
  recovery; completion/retry/failure transitions are lease-owner aware;
- refactored `AudioTranscriptionWorker` to operate without a Redis client. It
  checks provider cooldown before claim, persists transient retry timing in
  `next_attempt_at`, records permanent failure in PostgreSQL, and never uses
  `LPOP`, `LRANGE`, `RPUSH`, `LREM` or the audio dead-letter list;
- changed API admission and IA media reconciliation to reserve audio only in
  PostgreSQL. Image publication remains unchanged and is explicitly retained
  for issue 0050;
- added PostgreSQL-derived audio metrics to `GET /queues` and removed the audio
  dead-letter recovery setting, because recovery is now performed by polling;
- added `scripts/retire_legacy_audio_queue.py`. It inventories both legacy lists
  with bounded dry-run semantics, groups duplicate IDs, retains malformed and
  unknown entries, can explicitly import persisted transient dead-letter
  evidence, and only retires validated safe entries with confirmation. No live
  apply/deletion was run as part of this implementation;
- synchronized SPEC-0001, SPEC-0003 v1.9, SPEC-0006, the specs index, README,
  ARCHITECTURE, IMPLEMENTATION_PLAN and issue 0027's post-cutover contract.

Validation:

- focused audio/webhook/media/repository/cutover/OpenAPI suite: **42 passed**;
- full offline suite: **273 passed, 82 skipped**;
- PostgreSQL disposable suite after applying Alembic `0024_durable_media_leases`:
  **19 passed**, covering repository claim, future schedule, stale lease,
  lease-owner completion, concurrency and existing image recovery;
- `python -m compileall -q src scripts tests alembic/versions` — passed;
- `git diff --check` — passed;
- `graphify update .` — passed and refreshed `graphify-out`;
- implementation commit: `92e41f7 fix: migrate audio transcription to
  PostgreSQL polling`;
- Compose `cai`: `migrate` applied the new head, all application services were
  rebuilt/recreated, the API returned `{"status":"ok"}` from inside the
  container, `/queues` exposed the durable audio counters, and `audio_worker`
  logged schema `0024_durable_media_leases` before starting without a Redis
  client or audio-list operations. No live provider call was forced during the
  rollout.
