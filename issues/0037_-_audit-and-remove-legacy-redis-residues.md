---
id: 0037
title: "Audit and remove legacy Redis residues and unreferenced code"
type: maintenance
status: closed
priority: medium
phase: 5
created_at: 2026-08-21
updated_at: 2026-08-21
closed_at: 2026-08-21
related_issues: []
blocked_by: []
affects:
  - src/core/redis_client.py
  - src/utils/idempotency.py
  - src/api/routes.py
  - src/workers/ia_worker.py
  - src/workers/audio_worker.py
  - src/workers/image_worker.py
  - tests/test_idempotency.py
  - tests/test_media_scheduling.py
  - README.md
  - ARCHITECTURE.md
  - docker-compose.yml
---

## Description

The current architecture uses PostgreSQL as the durable authority and Redis as
work transport and short-lived coordination. Persistent DigiSac-history
finalization is the only supported mode; the former Redis buffer, debounce, and
legacy worker branch are no longer current application paths.

The live Redis inspection on 2026-08-21 found active queues that must be
preserved, as well as key families that have no producer or consumer in the
current source. The same inspection also found a small number of code symbols
with no references outside their definitions.

This issue is an evidence-first cleanup. It must not treat PostgreSQL
`pending` rows and Redis queue entries as duplicate data: PostgreSQL stores the
durable media state, while Redis transports the work to the workers.

## Current evidence

Runtime Redis state at inspection time:

- Redis 7.4.9 responded to `PING` with `PONG`.
- Approximately 1.59 MB was used; 1,059 keys existed and 1,056 had expiry.
- Active queue metrics were `ia_queue=0`,
  `audio_transcription_queue=0`, and `image_extraction_queue=77`.
- Active dead-letter metrics were `ia_dead_letter=0`,
  `audio_transcription_dead_letter=0`, and
  `image_extraction_dead_letter=1`.
- PostgreSQL contained 78 `pending`, 1 `failed`, and 443 `completed`
  `message_image_extractions` rows. The image backlog was associated with a
  Groq TPD rate limit and is not a Redis-residue cleanup target.

Key families observed without a corresponding current producer/consumer:

- `buffer:*` — 26 keys;
- `ticket_close_scheduled:*` — 265 keys;
- `ticket_last_message_at:*` — 271 keys;
- `ticket_protocol:*` — 256 keys;
- `ticket_classify_after:*` — 22 keys;
- `ticket_close_task:*` — 22 keys;
- `ia_processing` — one list item without TTL.

Potentially unreferenced code symbols:

- `src/core/redis_client.py::get_redis_client`;
- the module-level `redis_client` singleton in that module;
- `IdempotencyService.is_processed`;
- `IdempotencyService.mark_processed`.

Migration and backfill utilities are not automatically dead code. They may be
needed for historical recovery and require an explicit decision before removal.

## Scope

### In scope

- Reconfirm producers and consumers for every Redis key family through source,
  tests, Compose, README, ARCHITECTURE, and operational scripts.
- Produce a dry-run inventory containing prefix/key, Redis type, count, TTL,
  identified owner, and classification as active, recoverable historical,
  orphaned, or inconclusive.
- Reconcile the inventory with PostgreSQL media/cycle states, publication
  markers, `next_attempt_at`, leases, queue lengths, dead letters, and the
  transient status/result views before any deletion.
- Remove only explicitly approved patterns through an allowlist and a
  repeatable, bounded operation.
- Inspect `ia_processing` as an individual item and retain it until it is
  proven not to represent recoverable work.
- Remove unreferenced code only after checking tests, CLI entry points, dynamic
  imports, and documented operational commands.
- Update README and ARCHITECTURE if they contain stale active-flow statements.
- Add regression tests for the cleanup classification and active queue safety.

### Explicitly preserve

- `ia_queue`, `ia_dead_letter`;
- `audio_transcription_queue`, `audio_transcription_dead_letter`;
- `image_extraction_queue`, `image_extraction_dead_letter`;
- `processed:*` keys within their idempotency TTL;
- `ia_status:*` and `ia_result:*` while their compatibility contract remains;
- PostgreSQL publication markers, pending media, leases, and durable results.

### Out of scope

- Removing Redis from the architecture or replacing it with PostgreSQL
  polling/`LISTEN`/`NOTIFY`.
- Modifying or deleting PostgreSQL business records.
- Reprocessing the current image backlog.
- Changing Groq credentials, quotas, retry policy, or model configuration.
- Using `FLUSHDB`, `FLUSHALL`, broad `SCAN ... DEL`, or an unbounded key glob.

## Required implementation safety

1. Complete and review the dry-run report before mutation.
2. Use an explicit deletion allowlist, not a denylist.
3. Recheck queue and database counts immediately before deletion.
4. Delete only keys that remain classified as orphaned and have no active
   consumer or recovery meaning.
5. Make partial execution safe and repeatable; record before/after counts.
6. Recheck `/queues`, `/health`, worker logs, queue lengths, dead letters, and
   PostgreSQL publication/state invariants after cleanup.

## Acceptance criteria

- [x] The dry-run report exists and identifies every removed key family.
- [x] Every deleted pattern has evidence of no current producer, consumer, or
  recovery requirement.
- [x] Active queues, dead letters, idempotency keys, transient results, and
  PostgreSQL durable state remain intact.
- [x] No broad Redis deletion command is used.
- [x] The cleanup can be safely rerun after interruption.
- [x] Focused webhook, idempotency, cycle, media scheduling, and worker tests pass.
- [x] Runtime checks confirm healthy Redis, API, and workers after the operation.
- [x] Documentation no longer presents removed Redis buffer/debounce paths as
  active functionality.

## References

- `specs/0001-shared-data-and-analysis-contract.md` v1.5 — PostgreSQL durable
  authority, transient Redis boundary, idempotency, and sanitized operations.
- `specs/0003-durable-finalization-and-media.md` v1.6 — persistent queues,
  media state, publication markers, leases, schedules, and legacy removal.
- `README.md`, `ARCHITECTURE.md`, and `IMPLEMENTATION_PLAN.md` — operational
  command, ownership map, and implementation evidence.

## Resolution

Implemented the evidence-first Redis cleanup and removed only code proven to be
unreferenced:

- added `scripts/redis_residue_cleanup.py`, with bounded `SCAN` inventory,
  family/type/TTL/owner/classification reporting, PostgreSQL reconciliation,
  reviewed key-digest plans, explicit per-key deletion, `--confirm`, and safe
  rerun behavior;
- removed `get_redis_client` and the module-level Redis singleton from
  `src/core/redis_client.py`, while retaining the per-process lifecycle factory;
- removed the unused non-atomic `IdempotencyService.is_processed` and
  `mark_processed` methods, retaining atomic `try_mark_processed`;
- added regression tests for orphan classification, active queue/idempotency/
  status/result protection, `ia_processing` retention, bounded scans, and
  repeatable deletion; and
- synchronized README, architecture, SPEC-0001, SPEC-0003, the specification
  index, this plan, and this issue. The checked-in dry-run report is
  `reports/redis-residue-dry-run-2026-08-21.json` and contains no key values.

Runtime evidence on the local `cai` Compose project:

- reviewed dry-run: six orphan families, 857 keys total; `ia_processing` was a
  one-item list with no TTL and remained retained;
- reviewed allowlist apply: **857 deleted**, with no PostgreSQL mutation;
- post-cleanup inventory: all six orphan families at zero; image queue 79 and
  image dead-letter 1 remained; `ia_processing` remained one item; active
  `processed:*`, `ia_status:*`, and `ia_result:*` remained present;
- PostgreSQL remained at Alembic `0020_cycle_contact_provenance`, with 80
  pending images, one failed image, 443 completed images, zero publication
  markers, and zero active cycle leases; and
- API `/health` and `/queues` returned HTTP 200 from inside the API container,
  and Compose reported API/Redis/PostgreSQL healthy. Host-loopback `curl` was
  unavailable in this environment, so the container-local checks are the
  recorded API evidence.

Validation executed:

- baseline focused suite before implementation — **44 passed**;
- cleanup regression suite — **3 passed**;
- focused webhook/idempotency/cycle/media/worker suite — **47 passed**;
- `python -m compileall -q src tests alembic scripts` — passed;
- `PYTHONPATH=/app python -m scripts.redis_residue_cleanup --dry-run ...` —
  passed before and after cleanup;
- `PYTHONPATH=/app python -m scripts.redis_residue_cleanup --apply --confirm
  ...` — passed, **857 deleted**; rerun after cleanup was safe and deleted 0;
- post-cleanup API, Compose, Redis, PostgreSQL, and Alembic checks — passed;
- `PYTHONPATH=/app python scripts/verify.py` — compileall, Pyright and offline
  pytest passed, but the PostgreSQL stage had one pre-existing timezone
  assertion in `tests/test_department_mapping.py`; the current uncommitted
  `APP_TIMEZONE=America/Sao_Paulo` worktree change produces `-03:00` while that
  unchanged test expects UTC;
- `APP_TIMEZONE=UTC PYTHONPATH=/app python scripts/verify.py` — all stages
  passed: compileall, Pyright, **228 passed/69 skipped** offline, Alembic
  `0020_cycle_contact_provenance`, and **69 passed/228 deselected** PostgreSQL;
- `graphify update .` — passed after the final documentation/code diff.
