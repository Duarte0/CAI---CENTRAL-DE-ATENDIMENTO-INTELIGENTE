---
id: 0052
title: "Retire validated legacy Redis queues after PostgreSQL cutover"
type: maintenance
status: closed
priority: high
phase: 6
created_at: 2026-09-03
updated_at: 2026-09-03
closed_at: 2026-09-03
related_issues: ["0037", "0048", "0049", "0050"]
blocked_by: []
affects:
  - scripts/retire_legacy_ia_queue.py
  - scripts/retire_legacy_audio_queue.py
  - scripts/retire_legacy_image_queue.py
  - scripts/retire_validated_legacy_redis_queues.py
  - scripts/redis_residue_cleanup.py
  - src/api/routes.py
  - src/workers/ia_worker.py
  - src/workers/audio_worker.py
  - src/workers/image_worker.py
  - Dockerfile
  - docker-compose.yml
  - tests/test_retire_legacy_ia_queue.py
  - tests/test_retire_legacy_audio_queue.py
  - tests/test_retire_legacy_image_queue.py
  - tests/test_retire_validated_legacy_redis_queues.py
  - README.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
---

## Description

Issues 0048, 0049 and 0050 moved persistent IA finalization, audio
transcription and image extraction work to PostgreSQL polling. The Redis lists
remain physically present only as cutover evidence, but their size makes the
runtime look as if it still has an active backlog and increases the risk of an
operator replaying obsolete work.

The current `cai` runtime snapshot on 2026-09-03 found:

- `ia_queue=17161` and `ia_dead_letter=3`;
- `image_extraction_queue=68` and `image_extraction_dead_letter=3`;
- `audio_transcription_queue=0` and `audio_transcription_dead_letter=0`;
- durable PostgreSQL work metrics of `ia_due=42`, `ia_scheduled=2`,
  `ia_leased=0`, `image_due=46`, `image_scheduled=1`, and `image_leased=0`.

The complete bounded inventory of `ia_queue` inspected all 17,161 entries:
17,032 were duplicate physical entries for 129 unique cycle IDs; none was
malformed or pointed to an unknown cycle, and all entries had a durable
PostgreSQL cycle match. This is evidence that the list is stale residue, not
proof that every future inventory will have the same shape.

This issue performs the first physical queue cleanup after the cutover. It
must not replay Redis entries, delete PostgreSQL cycles or media rows, import
unreviewed dead letters, or remove unrelated Redis key families. The current
workers remain the source of truth: IA, audio and image work is claimed from
PostgreSQL with due scheduling and leases.

The maintenance scripts are not copied into the production `api` image. The
issue must therefore define a supported controlled-checkout or dedicated
maintenance-image execution path before asking an operator to apply deletion;
an arbitrary `docker compose exec api python -m scripts...` command is not a
supported procedure in the current image.

## Goals and invariants

- Establish a complete, timestamped, reviewable inventory before mutation.
- Prove that no supported producer or consumer still uses each legacy list.
- Reconcile every inspected valid entry with its durable PostgreSQL state.
- Remove only exact validated entries, one at a time, through the existing
  confirmation-gated scripts.
- Preserve malformed, unknown and unreviewed dead-letter evidence.
- Leave PostgreSQL cycles, media rows, retry schedules, leases and results
  unchanged.
- Make interruption safe: a rerun must report the remaining state and must not
  reprocess or duplicate work.
- Keep `processed:*`, `ia_status:*`, `ia_result:*` and other transient Redis
  families outside this issue's deletion boundary.

## Scope

### Included

- Define the execution boundary for the maintenance scripts, including the
  required `DATABASE_URL`, `REDIS_URL`, repository revision, operator, report
  path and read-only versus apply mode.
- Capture a PostgreSQL backup or approved recovery point and archive the
  dry-run reports before any Redis mutation.
- Reconfirm source, Compose and runtime ownership for `ia_queue`, both media
  queues, and their dead-letter lists.
- Run full dry-run inventories with `max-items` greater than the observed
  physical length, without provider calls or work republication.
- Apply retirement family by family, with the exact script confirmation phrase,
  only after a second snapshot shows no unexpected growth.
- Verify the post-cleanup durable counts, worker health, `/health`, `/queues`,
  Redis key families outside the scope, and PostgreSQL invariants.
- Update operational documentation and record the exact before/after evidence
  when closing this issue.

### Explicitly out of scope

- Replaying, re-enqueuing or reconstructing work from any Redis list.
- Deleting or changing PostgreSQL business records, cycles, media rows,
  classifications, leases or retry schedules.
- Recovering every dead-letter item automatically. Transient dead letters may
  be imported only by a separately reviewed recovery decision.
- Removing `processed:*`, `ia_status:*`, `ia_result:*`, `ia_processing` or
  generic Redis keys; those are handled by later issues and the allowlist
  cleanup procedure.
- Removing the Redis service or changing webhook idempotency.
- Using `FLUSHDB`, `FLUSHALL`, broad `SCAN ... DEL`, an unbounded glob or a
  direct list purge.

### Delivered implementation

- Added the `maintenance` Docker target and Compose profile. It contains the
  bounded maintenance scripts without adding `scripts/` to the production
  `api` image; reports are written to an operator-selected bind-mounted
  directory.
- Added `scripts.retire_validated_legacy_redis_queues` as the supported entry
  point. It requires an operator, the checked-out revision, Compose project,
  report path and explicit `--dry-run` or family-scoped `--apply` mode. Apply
  also requires the approved PostgreSQL backup/recovery-point reference and
  the exact family confirmation phrase.
- Each complete inventory now records SHA-256 digests of inspected list values,
  rather than raw Redis values. Apply consumes the reviewed report, verifies a
  healthy `/health` and `/queues`, compares a second snapshot, and refuses any
  changed or truncated family. The existing helpers still perform one-at-a-
  time exact `LREM` and retain malformed, unknown and transient dead-letter
  evidence.
- No provider call, replay, PostgreSQL mutation or unapproved Redis key-family
  operation is part of this command. A failed or interrupted apply must be
  re-inventoried and reviewed before another family is attempted.

### Controlled runtime evidence (2026-09-03)

- The `cai` maintenance image was built from revision
  `465be2ce80c9cc4083d8a88c992a3486b44ee022`. The complete dry-run used
  `--max-items 50000`, returned HTTP 200 from `/health` and `/queues`, and
  found `truncated=false` for all six lists: 17,164 IA entries (17,161 in
  `ia_queue` and 3 in `ia_dead_letter`), 71 image entries (68 + 3), and zero
  audio entries. All observed entries were validated against durable rows and
  were eligible under the family rules.
- A PostgreSQL custom-format recovery point was captured before apply at
  `/tmp/cai-0052-reports/cai-0052-20260903-final.dump` (6,443,396 bytes,
  SHA-256 `7a178051734fb48c13ceb38b3a92c9ba67db87fc0d86c24c59985ba58d9bc790`)
  and verified with `pg_restore --list`.
- Apply ran sequentially with the exact confirmations: IA removed 17,164
  entries, image removed 71, and audio removed 0. Each family recorded a
  healthy runtime, schema `0024_durable_media_leases`, no provider call, no
  PostgreSQL mutation and no touch to another family. IA was applied before
  image, then audio; each next step revalidated the reviewed report and second
  snapshot.
- The final dry-run found zero entries in all six retired lists. The apply
  report preserved the durable pre/post totals: 2,147 cycles, 1,259 audio
  rows and 852 image rows, with no active lease. A later final snapshot showed
  only normal poller progress in due/status counters while totals remained
  unchanged. `processed:*`, `ia_status:*`, `ia_result:*` and the
  `ia_processing` list were not deleted; their observed key counts remained
  outside the retirement boundary.
- Reports are retained outside Git under `/tmp/cai-0052-reports/`; they contain
  digests and aggregate/state evidence, not raw Redis values or provider
  payloads. This is acceptance evidence for the named `cai` runtime, not a
  claim about any other deployment.

## Implementation Plan

1. Choose and document the execution context. Use the dedicated `maintenance`
   image built from the checked-out revision and pinned Python dependencies,
   with a protected environment and a bind-mounted report directory. The
   context must connect to the intended `cai` Redis and PostgreSQL instances
   and must not use a disposable test database.
2. Record the revision, operator, UTC timestamp, Compose project, `/queues`
   response, Redis `LLEN` values, PostgreSQL schema head and worker status.
   Take the approved PostgreSQL backup/recovery point and archive reports in a
   path excluded from secrets and raw Redis values.
3. Run `scripts.retire_validated_legacy_redis_queues` in dry-run mode. For every
   family, require `truncated=false` before apply; report physical entries,
   inspected entries, unique IDs, duplicates, malformed/unknown entries,
   durable states and validated entries eligible for retirement. Store only
   value digests, never raw Redis values.
4. Reconfirm with source search and runtime logs that no active application
   path calls `RPUSH`, `LPOP`, `BRPOP`, `LRANGE` or equivalent for these queues.
   Observe two snapshots across a normal traffic interval; any growth,
   unknown producer or worker restart blocks apply and opens investigation.
5. Review dead-letter evidence separately. Retain malformed/unknown entries
   and any transient error that has not been reconciled to a durable row.
   Never pass `--recover-transient` as an automatic side effect of queue
   retirement.
6. Apply the IA queue first because it is the largest residue, then image and
   audio lists one family at a time through the coordinator. It must consume
   the reviewed report, require an approved backup/recovery-point reference,
   compare the second snapshot and use `--max-items` above the complete
   snapshot plus the exact family confirmation phrase. Stop on any validation,
   connectivity or count mismatch; do not continue with another family.
7. Rerun all inventories in dry-run mode and compare PostgreSQL cycle/media
   counts, retry timestamps, leases, terminal results and provider-call logs.
   A successful cleanup must leave durable work available to the PostgreSQL
   pollers and must not create new work.
8. Record the result in this issue, update README/ARCHITECTURE/implementation
   planning evidence, run focused and canonical verification, refresh Graphify
   and close in a focused commit. No production acceptance may be claimed from
   a local or disposable run.

## Tests

### Inventory and apply safety

- A full inventory reports `truncated=false` and refuses apply for a truncated
  snapshot.
- Duplicate entries are counted without becoming additional durable jobs.
- Malformed and unknown entries are retained and never included in the
  removable set.
- Apply removes only exact validated list values and is safe to rerun after an
  interruption.
- Empty lists and already-retired lists produce a zero-removal, successful
  rerun.
- The operation never calls a provider and never mutates PostgreSQL.

### Runtime and reconciliation

- Source-level guards prove that the active IA/audio/image workers do not
  publish or consume the retired lists.
- The durable PostgreSQL due/scheduled/leased counts are unchanged except for
  normal poller activity and remain the work authority.
- Current `waiting_media`, `retryable_failure`, completed and terminal rows
  remain represented only by their durable state.
- `/health`, `/queues`, worker startup and Redis connectivity remain healthy
  after the bounded operation.
- Reports contain no raw Redis values, message bodies, provider payloads,
  credentials or signed URLs.

### Verification

- Existing `tests/test_retire_legacy_ia_queue.py`,
  `tests/test_retire_legacy_audio_queue.py`,
  `tests/test_retire_legacy_image_queue.py`,
  `tests/test_retire_validated_legacy_redis_queues.py` and Redis-residue tests
  pass.
- `python -m compileall -q src scripts tests alembic/versions`, Pyright and
  `PYTHONPATH=/app python scripts/verify.py` pass with their results recorded.
- The post-operation inventory and a second report prove that no unapproved
  Redis family was touched.

## Acceptance Criteria

- [x] A supported maintenance execution context and protected target are
  documented; the production application image is not assumed to contain
  `scripts/`.
- [x] Complete dry-run reports exist for IA, audio and image queues/dead
  letters, with `truncated=false` and before/after timestamps.
- [x] Source and runtime evidence show no active producer or consumer for the
  lists being retired during the observation window.
- [x] Only exact validated legacy entries are removed with confirmation; no
  replay, broad deletion, provider call or PostgreSQL mutation occurs.
- [x] Malformed, unknown and unreviewed dead-letter evidence remains intact.
- [x] PostgreSQL durable cycle/media counts, schedules, leases and results are
  preserved, and all work remains claimable by the appropriate poller.
- [x] The operation is repeatable and its reports contain no sensitive values.
- [x] Focused tests, compileall, Pyright, canonical verification and runtime
  health checks pass; evidence is recorded without claiming provider or
  production acceptance beyond what was actually observed.
- [x] README, ARCHITECTURE, `IMPLEMENTATION_PLAN.md` and this issue describe
  the retired lists and the remaining Redis boundary accurately.

## References

- `issues/0037_-_audit-and-remove-legacy-redis-residues.md`: bounded allowlist,
  Redis safety and PostgreSQL reconciliation rules.
- `issues/0048_-_prevent-duplicate-ia-cycle-queue-republication.md`: IA
  PostgreSQL polling cutover and legacy queue semantics.
- `issues/0049_-_migrate-audio-transcription-to-postgresql-polling.md`: audio
  queue retirement and transient dead-letter rules.
- `issues/0050_-_migrate-image-extraction-to-postgresql-polling.md`: image
  queue retirement and media-gate invariants.
- `scripts/retire_legacy_ia_queue.py`,
  `scripts/retire_legacy_audio_queue.py`,
  `scripts/retire_legacy_image_queue.py`: dry-run and confirmation-gated
  retirement implementations.
- `scripts/retire_validated_legacy_redis_queues.py`: report-bound, family-scoped
  coordinator used by the maintenance image/profile.
- `scripts/redis_residue_cleanup.py`: separate key-family allowlist cleanup;
  not a substitute for queue reconciliation.
- `README.md` Operação and `ARCHITECTURE.md` Redis coordination: current
  operator procedures and ownership map.

## Resolution

Closed after the controlled `cai` runtime cleanup on 2026-09-03. The dedicated
maintenance target/profile, report-bound coordinator and six-list inventory
were implemented and exercised from revision
`465be2ce80c9cc4083d8a88c992a3486b44ee022`. The reviewed recovery point and
reports are recorded above. Exactly 17,235 validated Redis list entries were
removed (`17,164` IA, `71` image, `0` audio); the final dry-run found all six
lists empty, while PostgreSQL totals, workers, `/health`, `/queues` and the
protected Redis families remained within the documented boundary.
