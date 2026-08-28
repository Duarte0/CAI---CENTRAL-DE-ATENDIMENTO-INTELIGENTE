---
id: 0046
title: "Block conversation finalization until audio transcription succeeds"
type: bug
status: closed
priority: high
phase: 5
created_at: 2026-08-26
updated_at: 2026-08-26
closed_at: 2026-08-26
related_issues:
  - "0004"
  - "0027"
  - "0029"
  - "0031"
  - "0035"
blocked_by: []
affects:
  - src/workers/ia_worker.py
  - src/workers/audio_worker.py
  - src/core/finalization.py
  - src/core/conversation_cycle_repository.py
  - src/core/durable_media_repository.py
  - tests/test_audio_worker.py
  - tests/test_conversation_finalization.py
  - tests/test_media_scheduling.py
  - tests/test_operational_recovery_db.py
  - README.md
  - PRD.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
  - specs/0003-durable-finalization-and-media.md
  - specs/README.md
---

## Description

The persistent finalization flow treats terminal audio extraction differently
from terminal image extraction. A terminal image failure puts dependent cycles
in `media_blocked` and prevents classification, but a terminal audio failure is
converted into a synthetic context marker and may produce a classification with
`completed_with_warnings`.

This violates the required media dependency contract: an audio message is not
available to the classifier until a non-empty transcription has been durably
persisted. An audio failure must never be treated as successful enrichment or
as sufficient context for an intent classification. Audio must follow the same
finalization safety boundary as image: the cycle waits while the media is
pending/recoverable and remains blocked when the media reaches a terminal
failure, until an operator or a durable recovery path makes the media available.

### Current implementation evidence

- [`apply_media_states()`](/app/src/core/finalization.py:207) adds `pending` for
  `pending`/`processing` media and for failed media below the attempt threshold,
  but only adds terminal images to `blocked`. A terminal audio row instead gets
  the synthetic text `[ÁUDIO NÃO DISPONÍVEL — processamento falhou após N
  tentativas]` and a `media_failed` warning.
- [`IAWorker._process_cycle()`](/app/src/workers/ia_worker.py:451) stops before
  context construction only when `blocked` is non-empty. Because audio is not
  added to that set, the worker hydrates the marker, transitions to
  `classifying`, persists a classification, and chooses
  `completed_with_warnings` when the warning is present.
- [`_ensure_media_jobs()`](/app/src/workers/ia_worker.py:232) and the media
  projection use the classification retry limit as the terminal threshold for
  failed media. This issue must preserve the durable transient retry behavior
  delivered by issue 0027, while making the finalization gate independent of
  whether the failed row has a marker or warning: no non-completed audio may be
  classified.
- [`wake_unblocked_media_cycles()`](/app/src/core/conversation_cycle_repository.py:609)
  currently checks terminal failed image rows only. A successfully recovered
  audio must also wake only cycles that depend on that audio, and a cycle with
  another still-blocking media item must remain blocked/waiting.
- The durable media repository already persists status, attempt count,
  `error_message`, `next_attempt_at`, and publication state in PostgreSQL
  ([`durable_media_repository.py`](/app/src/core/durable_media_repository.py:73)).
  No new transient Redis authority or raw media persistence is needed.

**Root cause:** the media state projection and persistent-cycle orchestration
encode an image-specific blocking rule instead of a shared invariant that every
audio/image dependency must be `completed` with usable extracted text before
context rendering and classification. The image wake-up query repeats the same
image-only assumption.

**Actual behaviour:** after a terminal audio failure, the audio row is
`failed`, a dead-letter entry is retained, the context contains a synthetic
audio-unavailable marker, and the cycle can be classified. A later retry or
manual recovery is therefore not a prerequisite for the classification.

**Expected behaviour:**

```text
audio/image absent, pending, processing, or recoverable failure
  -> waiting_media
  -> retry only when next_attempt_at is due

audio/image terminal failure
  -> media_blocked
  -> no context rendering, Groq classification, terminal classification,
     Acessórias preparation, or Request creation

all dependent audio/image items completed with non-empty extracted text
  -> build context
  -> classify
  -> completed or completed_with_warnings only for unrelated warnings
```

The existing image behavior is the reference. `completed_with_warnings` may
remain a valid terminal state for other documented warnings, but it must not be
used to make a cycle eligible when any required audio transcription is absent.

Existing historical cycles already classified under the old audio-warning
behavior must not be silently rewritten or bulk-reprocessed by this issue. Any
historical recovery requires a separately authorized, auditable operation.

## Scope

### In scope

- Make audio and image use one shared media-readiness policy in the persistent
  finalization path.
- Keep a cycle in `waiting_media` while any required audio is missing,
  `pending`, `processing`, or otherwise eligible for retry.
- Put a cycle in `media_blocked` when any required audio reaches the same
  terminal failed condition used for image extraction.
- Prevent context construction, classification persistence, Acessórias
  preparation, and Request creation for a cycle with non-completed required
  audio or image media.
- Require a non-empty persisted transcription/extraction for media readiness;
  a row with `completed` but unusable text must not satisfy the gate.
- Extend selective cycle wake-up and recovery checks from image-only to the
  audio and image dependencies of each cycle.
- Preserve issue 0027's durable audio retry semantics: transient provider,
  timeout, and connection failures remain `pending`, honor provider timing and
  `next_attempt_at`, are deduplicated by `message_id`, and retain dead-letter
  safety copies until successful non-empty persistence.
- Preserve compare-and-set transitions, leases, publication recovery,
  provider-error sanitization, and the existing public status surface.
- Add focused unit, orchestration, and disposable-PostgreSQL coverage and
  synchronize implementation-derived documentation.

### Out of scope

- Changing the audio transcription model, DigiSac download contract, Groq quota,
  `Retry-After` calculation, or issue 0027's transient retry policy.
- Treating a permanent audio error as successfully processed or inventing a
  fallback transcription, placeholder accepted by the model, or inferred text.
- Bulk reprocessing or rewriting historical classifications that were already
  persisted with `completed_with_warnings`.
- New public/admin retry endpoints, operator UI, automatic production
  reprocessing, or live queue manipulation.
- Changes to the IA four-field model-facing contract, intent taxonomy,
  Acessórias identity/mapping rules, Request payload, or Request lifecycle.
- A database migration unless implementation inspection proves that an additive,
  data-preserving schema change is unavoidable. Existing cycle/media statuses
  and scheduling columns should be reused.
- Removing unrelated Redis queue or dead-letter entries, changing retention, or
  introducing a second durable media authority.

## Implementation Plan

1. Reconfirm the current state machine and dependency representations in
   `conversation_cycle_messages`, `message_transcriptions`, and
   `message_image_extractions`. Define one internal readiness result that
   distinguishes `ready`, `waiting`, and `blocked`, includes the media kind and
   message ID, and treats only `completed` with non-empty text as ready.
2. Update `apply_media_states()` and its callers so terminal audio and terminal
   image failures both produce `blocked`, with no synthetic audio marker and no
   `media_failed` warning used as a substitute for missing required content.
   Keep retryable/pending media in `pending` and preserve the existing image
   behavior.
3. Update `IAWorker._process_cycle()` to apply the shared gate before rendering
   `model_context` or transitioning to `classifying`. For `waiting`, persist
   `media_wait` and the actual next eligible time from media
   `next_attempt_at`. For `blocked`, persist a safe kind/message-ID diagnostic,
   transition the cycle to `media_blocked`, clear publication/lease fields, and
   return without calling classification or Request preparation.
4. Update the cycle repository's blocked-media recovery query and any related
   projections to consider both audio types (`ptt`, `audio`, `voice`) and
   `image`. Wake only cycles whose blocking dependencies are no longer terminal;
   after wake-up, let the normal `waiting_media` gate verify every dependency
   before classification. Do not wake unrelated cycles.
5. Audit `_ensure_media_jobs()` and the audio/image recovery paths for the
   invariant that requeueing a failed or recovered item can only move the cycle
   toward `waiting_media`; it must never bypass the readiness gate. Keep
   transient audio retries independent of the IA classification retry budget as
   specified by issue 0027, and avoid duplicate queue publication.
6. Add focused tests covering:
   - terminal audio failure returns `blocked`, with no placeholder and no warning;
   - pending/processing/missing audio keeps the cycle in `waiting_media`;
   - a failed audio below the terminal threshold remains recoverable but cannot
     classify;
   - a cycle with terminal audio does not call Groq, insert a classification,
     prepare identity/mapping, or create an Acessórias Request;
   - mixed audio/image dependencies block or wait until all required media are
     usable;
   - successful audio recovery wakes only dependent blocked cycles;
   - a completed audio row with empty text does not pass the gate;
   - existing terminal-image blocking and selective wake-up remain unchanged;
   - issue 0027 retry, dead-letter safety-copy, deduplication, publication
     failure, and sanitized-error tests remain green.
7. Synchronize `README.md`, `PRD.md`, `ARCHITECTURE.md`,
   `IMPLEMENTATION_PLAN.md`, SPEC-0003, and the specifications index. Replace
   the audio-warning-only rule with the shared media-blocking invariant, retain
   the historical issue 0027 retry description, and state clearly that local
   tests do not prove provider or production availability. Run `git diff --check`
   and `graphify update .` after the implementation is complete.

## Data, migration, compatibility, security, observability, and rollout

- **Data/migration:** PostgreSQL remains the durable authority for media status,
  attempts, schedules, errors, cycles, snapshots, and classifications. Reuse
  `media_blocked`, `waiting_media`, `next_attempt_at`, leases, and existing
  media tables. Do not delete or rewrite old classifications. No migration is
  expected.
- **State integrity:** a terminal cycle must not have a classification unless
  every required audio/image dependency was `completed` with non-empty text at
  the gate. A cycle with any terminal media failure must remain
  `media_blocked`; a cycle with only retryable media must remain
  `waiting_media` and be scheduled from the later applicable retry time.
- **Recovery/concurrency:** preserve compare-and-set media transitions,
  `FOR UPDATE SKIP LOCKED`, cycle claims/leases, publication markers, queue
  deduplication, and selective wake-up. A successful audio recovery may wake
  only cycles that reference that message ID.
- **Compatibility:** retain webhook fast acknowledgment, media reservation
  before Redis publication, existing queue/dead-letter names, `GET /queues`,
  conversation status/result routes, the four-field IA contract, and the
  distinction between classification warnings unrelated to media and missing
  required media. `media_blocked` is already an exposed persistent status.
- **Security/privacy:** logs, snapshots, errors, metrics, and wake diagnostics
  may contain only safe media kind/message ID, status, attempt, timestamps,
  counts, and sanitized categories. Never persist or log signed URLs, tokens,
  raw provider bodies, audio bytes, or transcription payloads in retry metadata.
- **Observability:** expose enough existing status/queue/cycle metadata to
  distinguish `waiting_media` from `media_blocked`, identify audio versus image
  by safe category, and verify that no classification was inserted while a
  required audio dependency was incomplete. Do not add a raw-payload debug
  surface.
- **Rollout:** deploy the coordinated `ia_worker`/audio and persistence code
  together after focused and disposable-PostgreSQL verification. Monitor
  `message_transcriptions`, audio queue/dead-letter membership, and cycle states
  before considering any separately authorized historical recovery. Local
  verification is not production acceptance.

## Tests

- **Unit/state projection:** `tests/test_conversation_finalization.py`,
  `tests/test_media_detection.py`
- **Audio worker/retry compatibility:** `tests/test_audio_worker.py`,
  `tests/test_media_scheduling.py`
- **IA orchestration:** existing IA worker tests plus a focused regression test
  for the no-classification terminal-audio gate
- **PostgreSQL/recovery:** `tests/test_operational_recovery_db.py`,
  `tests/test_conversation_cycles_db.py`
- **Verification:** focused pytest, the canonical offline suite, compileall,
  strict Pyright, disposable PostgreSQL/Alembic verification, `git diff --check`,
  and `graphify update .`

## Acceptance Criteria

- [x] Any required audio with status other than `completed` and non-empty text
  prevents context classification for its cycle.
- [x] Pending/recoverable audio keeps the cycle in `waiting_media` and uses the
  durable `next_attempt_at`/provider timing contract.
- [x] Terminal audio failure puts the dependent cycle in `media_blocked`, using
  the same safety boundary as terminal image failure.
- [x] A cycle with terminal audio does not render a synthetic audio marker,
  persist a classification, publish a classification result, prepare an
  Acessórias Request, or create a Request.
- [x] `completed_with_warnings` is not produced solely because required audio is
  unavailable; it remains available only for unrelated documented warnings
  after all required media is usable.
- [x] A completed audio row with null, blank, or unusable transcription does not
  satisfy the media gate.
- [x] Successful audio recovery wakes only cycles that depend on that audio;
  unrelated or still-blocked cycles remain unchanged.
- [x] Existing image blocking, image recovery, transient audio retry,
  dead-letter safety-copy, queue deduplication, stale-job recovery, and failed
  Redis publication behavior remain intact.
- [x] No credentials, signed URLs, raw provider responses, audio bytes, or raw
  customer content are added to durable failure state or logs.
- [x] No historical classification is silently rewritten or bulk-reprocessed.
- [x] Focused tests, canonical verification, compileall, strict Pyright,
  disposable PostgreSQL checks, and `git diff --check` pass.
- [x] PRD, ARCHITECTURE, IMPLEMENTATION_PLAN, SPEC-0003, README, and the specs
  index describe the same audio/image readiness and blocking contract.

## References

- `PRD.md` §§5.3–5.4, 8, and 9 — media enrichment, persistent finalization,
  reliability, current implementation status, and source authority.
- `ARCHITECTURE.md` §§4–7 and 12 — queues/dead-letters, cycle state machine,
  media processing, and reliability invariants.
- `IMPLEMENTATION_PLAN.md` — completed durable audio retry parity and the
  current local-only verification boundary.
- `specs/0003-durable-finalization-and-media.md` §§71–103 — persistent cycles,
  media readiness, failure behavior, recovery, and verification obligations.
- `specs/README.md` — active SPEC-0003 baseline and issue traceability.
- `issues/0004_-_verify-durable-operational-recovery-on-runner.md` — durable
  media recovery and selective cycle wake-up verification.
- `issues/0027_-_align-audio-retry-with-image-recovery.md` — current audio
  retry/dead-letter contract; this issue changes finalization eligibility, not
  transient retry timing.
- `issues/0029_-_isolate-conversation-cycle-persistence-from-db-facade.md` and
  `issues/0031_-_isolate-durable-media-persistence-from-db-facade.md` — current
  repository boundaries and preserved cycle/media semantics.
- `src/core/finalization.py`, `src/workers/ia_worker.py`,
  `src/workers/audio_worker.py`,
  `src/core/conversation_cycle_repository.py`, and
  `src/core/durable_media_repository.py` — implementation anchors.
- `tests/test_conversation_finalization.py` — terminal-audio blocking,
  non-empty completion, and terminal-image blocking regression coverage.

---

## Resolution

<!-- Filled by the agent on close. DO NOT edit manually. -->
<!-- What was done, decisions made, and why. -->
<!-- Include: files modified, tests added, edge cases handled. -->

Implemented the shared media-readiness gate for audio and image finalization.
`apply_media_states()` now considers only non-empty `completed` media usable;
pending/recoverable rows keep the cycle in `waiting_media`, while terminal audio
and image failures both transition the dependent cycle to `media_blocked` with
no synthetic marker, classification, result publication, or Acessórias Request
preparation. The selective wake-up query now covers `ptt`, `audio`, and `voice`
transcriptions as well as images. Existing issue 0027 durable retry, dead-letter
safety-copy, deduplication, and recovery semantics were preserved; no migration
was required and historical classifications were not changed.

Changed source: `src/core/finalization.py`, `src/workers/ia_worker.py`, and
`src/core/conversation_cycle_repository.py`. Added/updated regression coverage
in `tests/test_conversation_finalization.py`, `tests/test_ia_worker_retry.py`,
and `tests/test_conversation_cycles_db.py`. Synchronized README, PRD,
ARCHITECTURE, IMPLEMENTATION_PLAN, SPEC-0003, and the specs index.

Verification: focused media/worker tests **27 passed**; canonical verification
passed compileall, strict Pyright, offline pytest **258 passed, 78 skipped**,
Alembic head `0023_manual_reconciliation`, and PostgreSQL pytest **78 passed,
258 deselected**.
