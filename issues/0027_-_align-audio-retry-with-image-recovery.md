---
id: 0027
title: "Align audio retry with image recovery"
type: bug
status: open
priority: high
phase: 5
created_at: 2026-08-20
updated_at: 2026-08-20
closed_at: ~
related_issues: []
blocked_by: []
affects:
  - src/workers/audio_worker.py
  - src/core/config.py
  - tests/test_audio_worker.py
  - tests/test_media_scheduling.py
  - README.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
---

## Description

Audio transcription currently treats transient provider failures as terminal
after `MAX_RETRY_ATTEMPTS` attempts. A 429, timeout, connection failure, or
transient 5xx is written as `failed` and placed in
`audio_transcription_dead_letter`, with no automatic recovery path. The image
worker already keeps transient failures pending, honors provider timing, and
recovers legacy transient dead-letters.

At discovery time, PostgreSQL contained 44 failed audio transcriptions and the
Redis audio dead-letter contained 44 entries. All 44 persisted errors matched
transient failure patterns. The global three-attempt setting is intended for IA
classification, not a terminal budget for durable media recovery.

**Root cause:** `AudioTranscriptionWorker.process_job()` checks
`attempt < self.max_retries` before rescheduling a `TransientTranscriptionError`,
and `AudioTranscriptionWorker` has no equivalent of the image worker's
`recover_transient_dead_letters()` routine.

**Expected behaviour:** transient audio failures remain durable `pending` jobs,
with `next_attempt_at` calculated from `Retry-After` and local backoff. Only
permanent processing failures become `failed`. Existing dead-letters are
recovered only when their persisted error is demonstrably transient.

## Scope

### In scope

- Durable, unbounded retry for transient audio/provider/network failures.
- `Retry-After` parsing and audio-specific retry/backoff configuration.
- Automatic recovery of legacy transient audio dead-letters.
- Queue/dead-letter deduplication by `message_id`.
- Removal of a matching dead-letter only after successful persistence of the
  transcription.
- Unit and PostgreSQL-backed recovery coverage where applicable.
- Documentation and implementation-plan synchronization.

### Out of scope

- Changing the Groq transcription model or provider quota.
- Retrying permanent audio errors automatically.
- Changing IA classification retry semantics.
- Database migrations or public API changes.
- Blindly reprocessing dead-letters whose stored error is not transient.

## Implementation Plan

1. Extend audio retry classification to retain provider retry timing and use the
   shared retry-delay helpers.
2. Change transient audio failures to persist `pending` with a future
   `next_attempt_at`, regardless of the global classification attempt limit.
3. Add periodic dead-letter recovery that inspects persisted audio rows, reopens
   only failed rows with transient errors, preserves the dead-letter safety copy,
   and publishes one deduplicated retry job.
4. Remove matching audio dead-letters only after nonempty transcription text is
   persisted as `completed`; release the publication marker if Redis publish
   fails.
5. Add focused tests for retries beyond three attempts, provider timing,
   permanent failures, dead-letter recovery, deduplication, and success cleanup.
6. Update `README.md`, `ARCHITECTURE.md`, and `IMPLEMENTATION_PLAN.md`, then
   run `graphify update .` and the focused/full verification commands.

## Tests

- **Unit:** `tests/test_audio_worker.py`
- **Recovery/queue:** `tests/test_media_scheduling.py`
- **PostgreSQL:** `tests/test_operational_recovery_db.py`

## Acceptance Criteria

- [ ] A transient 429/5xx/timeout/connection failure on attempt 3 remains
  `pending` and is scheduled for a later attempt.
- [ ] `Retry-After` is honored with the configured provider margin and local
  backoff.
- [ ] Transient retries do not depend on `MAX_RETRY_ATTEMPTS`.
- [ ] Permanent failures become `failed` and enter the audio dead-letter.
- [ ] Existing transient dead-letters are recovered without duplicating queue
  entries or removing their safety copies prematurely.
- [ ] A matching dead-letter is removed only after successful nonempty
  transcription persistence.
- [ ] Failed/non-transient dead-letters are not automatically retried.
- [ ] No credentials, raw audio, signed URLs, or sensitive provider payloads are
  added to logs or persisted retry metadata.
- [ ] Focused tests, compileall, and the canonical test suite pass.
- [ ] Documentation and `IMPLEMENTATION_PLAN.md` reflect the implemented
  behavior, and Graphify metadata is updated.

## References

- `ARCHITECTURE.md`, sections 4 and 7
- `IMPLEMENTATION_PLAN.md`, persistent conversation analysis and recovery
- `src/workers/image_worker.py`, transient media recovery behavior
- `src/core/provider_retry.py`, shared retry timing helpers

## Resolution

<!-- Filled by the agent on close. -->
