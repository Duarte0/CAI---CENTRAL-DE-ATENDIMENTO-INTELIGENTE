---
id: 0054
title: "Retire Redis IA status and result compatibility views"
type: refactor
status: open
priority: medium
phase: 6
created_at: 2026-09-03
updated_at: 2026-09-03
closed_at: ~
related_issues: ["0037", "0048", "0053", "0055"]
blocked_by: ["0053"]
affects:
  - src/workers/ia_worker.py
  - src/api/routes.py
  - src/api/openapi.py
  - src/core/config.py
  - src/core/redis_client.py
  - src/utils/backfill_redis_history.py
  - scripts/redis_residue_cleanup.py
  - scripts/retire_ia_redis_compatibility.py
  - Dockerfile
  - docker-compose.yml
  - tests/test_ia_worker_retry.py
  - tests/test_history_finalization_webhook.py
  - tests/test_openapi_contract.py
  - tests/test_redis_residue_cleanup.py
  - tests/test_retire_ia_redis_compatibility.py
  - README.md
  - ARCHITECTURE.md
  - specs/0001-shared-data-and-analysis-contract.md
  - specs/0003-durable-finalization-and-media.md
  - specs/0006-api-documentation-and-openapi-contract.md
  - IMPLEMENTATION_PLAN.md
---

## Description

The IA worker persists the classification, cycle status and result in
PostgreSQL, but after completion it also writes TTL compatibility views to
Redis as `ia_status:{conversation_id}` and `ia_result:{conversation_id}`. The
public status/result routes already read the durable cycle/result repository;
the Redis writes are therefore a compatibility side effect rather than the
source of truth.

The historical `src/utils/backfill_redis_history.py` utility still reads
`ia_result:*` to import legacy results into PostgreSQL. That utility must be
audited and either run to completion, explicitly retired with its evidence
preserved, or moved to a separately documented maintenance environment before
the keys are deleted. Removing new writes must not silently destroy a
historical recovery path.

This issue removes the compatibility producer after an explicit consumer and
historical-data decision. It is a prerequisite for removing Redis from the IA
worker, but it does not yet remove webhook idempotency, the Redis service or
the maintenance scripts.

## Confirmed current behavior and boundaries

- `_process_cycle()` stores the canonical classification and cycle transition
  in PostgreSQL before writing `ia_result:*` and `ia_status:*` with
  `RESULT_TTL_SECONDS`.
- `GET /conversations/{id}/status`, `/result`, `/cycles` and the cycle routes
  use PostgreSQL repositories for their current response.
- `backfill_redis_history.py` scans `ia_result:*` and writes missing durable
  classifications; it is a historical migration tool, not an active IA
  worker.
- `scripts/redis_residue_cleanup.py` explicitly protects both key families
  while their compatibility contract remains.
- `processed:*` webhook idempotency, legacy queue lists and `ia_processing`
  have separate lifecycles and must not be removed by this issue.

## Goals and invariants

- PostgreSQL remains the sole authority for IA cycle status and results.
- No new `ia_status:*` or `ia_result:*` key is written after cutover.
- Public status/result responses remain unchanged and do not depend on Redis.
- Any historical Redis result needed for backfill is processed or explicitly
  accounted for before the compatibility keys expire or are removed.
- Existing result TTL behavior is not mistaken for durable retention.
- Removal is bounded, reportable and limited to the two exact key families.
- No classification, cycle, media state, retry schedule or other Redis family
  is deleted or changed.

## Scope

### Included

- Inventory source and known consumers of `ia_status:*` and `ia_result:*`,
  including operational clients, dashboards, backfill commands and API tests.
- Define the historical backfill disposition and capture a sanitized report of
  key counts, TTLs, successful imports, skipped entries and invalid entries.
- Stop IA compatibility writes after canonical PostgreSQL persistence remains
  verified, while preserving the public PostgreSQL query contract.
- Update the Redis residue allowlist/procedure to permit removal only after the
  compatibility sunset window and report review.
- Remove IA-specific Redis configuration/worker wiring that exists solely for
  these writes, without deleting the general maintenance client prematurely.
- Add regression tests and update OpenAPI, architecture, specs, README and
  implementation planning evidence.

### Explicitly out of scope

- Moving webhook event idempotency; issue 0053 owns that migration.
- Retiring `processed:*`, `ia_processing`, legacy queues or generic Redis key
  families.
- Removing the Redis Compose service; issue 0055 owns that cutover.
- Deleting durable PostgreSQL classifications, cycles, media or audit records.
- Reconstructing missing classifications by replaying conversations or calling
  Groq.

## Implementation Plan

1. Produce a consumer inventory from source, tests, deployment configuration,
   documented commands and approved operational clients. Confirm that all
   public API status/result reads are PostgreSQL-backed and record any external
   reader that is not visible in the repository.
2. Run a bounded dry-run for `ia_status:*` and `ia_result:*` that records
   counts, TTL buckets, key digests and whether each result has a matching
   durable cycle/classification. Do not print or persist key values or result
   payloads in the report.
3. Resolve historical migration. If valid legacy results remain outside
   PostgreSQL, run `backfill_redis_history.py` in a protected maintenance
   context and verify idempotent inserts. If no import is authorized, record
   the explicit retention/retirement decision; never infer that a key is safe
   to discard from its TTL alone.
4. Deploy a version that preserves the PostgreSQL write/read order but omits
   the two Redis `SET` operations. Use a coordinated handoff so a previous IA
   worker cannot continue writing after the sunset version starts. Keep
   `processed:*` and webhook idempotency operational through issue 0053.
5. Observe at least one complete `RESULT_TTL_SECONDS` window plus the agreed
   client-observation period. Confirm no new compatibility keys appear, public
   endpoints remain correct, and classification/provider processing metrics do
   not change unexpectedly.
6. Generate a final dry-run and apply only the explicit `ia_status:*` and
   `ia_result:*` families through the allowlisted cleanup command. Recheck
   PostgreSQL before and after; stop on any new key, unknown consumer or
   mismatch.
7. Remove obsolete worker configuration and documentation, retain historical
   migration tooling only in its declared maintenance boundary, run tests and
   update the graph/issue evidence. Do not close until the next issue can
   remove Redis without hidden IA consumers.

## Data, compatibility and rollback

- Canonical result data is PostgreSQL. Redis keys are disposable compatibility
  views with a documented TTL and must never be treated as the only copy.
- A key with no durable match is not automatically safe to delete; it requires
  an explicit historical recovery decision and evidence of no valid source.
- Rollback before the sunset window means restoring the prior IA worker while
  Redis remains available. Rollback after deletion cannot recreate Redis
  results; the recovery source is the PostgreSQL ledger or an approved backup,
  not a provider replay.
- The cleanup must not touch `processed:*`, queue lists, `ia_processing`,
  Redis AOF metadata or unrelated TTL keys.
- Logs and reports may contain counts, status categories and digests only; no
  conversation content, classification text, secrets or URLs.

## Tests

### Consumer and migration tests

- Source tests prove public status/result routes use PostgreSQL and do not call
  Redis.
- The inventory classifies matching, missing, expired and invalid legacy
  results without exposing their values.
- Backfill is idempotent, validates legacy shape and does not duplicate a
  durable classification.
- The cleanup refuses unreviewed/mismatched results and is bounded and
  repeatable.

### Worker and API regression tests

- IA completion persists PostgreSQL status/result correctly when Redis is
  unavailable after the compatibility sunset.
- No new `ia_status:*` or `ia_result:*` key is written by the worker.
- Public status/result responses and missing-result `404` behavior remain
  unchanged.
- Webhook idempotency and contact hydration backoff remain covered by their
  independent contracts.

### Operational tests

- A complete TTL-window observation proves the two key families do not grow.
- `/health`, `/queues`, worker logs, PostgreSQL counts and provider-call
  metrics remain consistent after writes stop.
- `processed:*` and unrelated active/transient key families remain intact.
- Compileall, Pyright, focused tests and the disposable PostgreSQL runner pass.

## Acceptance Criteria

- [ ] All source and known operational consumers of `ia_status:*` and
  `ia_result:*` are inventoried; unknown external readers are an explicit
  rollout blocker.
- [x] Historical Redis results have a documented disposition and sanitized
  dry-run evidence; no valid unaccounted result is discarded.
- [x] IA worker writes canonical state to PostgreSQL and no longer creates new
  `ia_status:*` or `ia_result:*` keys after the coordinated cutover.
- [x] Public status/result APIs remain PostgreSQL-backed and behaviorally
  compatible without Redis compatibility views.
- [ ] A complete TTL/observation window confirms no compatibility-key growth.
- [ ] Only the two reviewed key families are removed through the allowlist;
  `processed:*`, queues, `ia_processing` and durable PostgreSQL data remain.
- [x] Backfill/cleanup is bounded, repeatable, sanitized and documented for a
  maintenance environment separate from the application image.
- [x] Focused tests, compileall, Pyright, canonical verification, runtime
  checks and documentation updates pass with accurate evidence.

## References

- `src/workers/ia_worker.py`: canonical PostgreSQL completion without Redis
  compatibility writes after the cutover.
- `src/api/routes.py`: PostgreSQL-backed status/result response paths.
- `src/utils/backfill_redis_history.py`: historical `ia_result:*` importer.
- `scripts/redis_residue_cleanup.py`: allowlisted key-family inventory and
  deletion safety.
- `issues/0037_-_audit-and-remove-legacy-redis-residues.md`: prior residue
  cleanup boundary.
- `issues/0048_-_prevent-duplicate-ia-cycle-queue-republication.md`: durable
  IA polling migration.
- `issues/0053_-_move-webhook-idempotency-to-postgresql.md`: remaining active
  API Redis contract.
- `specs/0001-shared-data-and-analysis-contract.md`,
  `specs/0003-durable-finalization-and-media.md` and
  `specs/0006-api-documentation-and-openapi-contract.md`.

## Resolution

Implementation is complete through the no-write cutover. `IAWorker` no longer
initializes Redis, publishes `ia_status:*`/`ia_result:*`, or reads the retired
`RESULT_TTL_SECONDS` setting; the worker's Compose service depends only on
PostgreSQL and the migration. The public status/result handlers remain
PostgreSQL-backed and the OpenAPI descriptions record that boundary.

The dedicated maintenance command
`scripts.retire_ia_redis_compatibility` inventories only the two exact families,
keeps key/entry digests and TTL buckets instead of values, reconciles legacy
results with durable classifications, and refuses apply without the reviewed
report, a full 86400-second observation window, an explicit historical decision,
and a second fingerprint-checked snapshot. The former importer is retained only
in the maintenance image; the general residue command inventories these
families but cannot delete them.

The issue remains open: the named runtime must still complete the external
consumer confirmation, the full TTL/client observation, and the explicit
allowlisted apply. Until then both compatibility families and all unrelated
Redis/PostgreSQL data remain retained. No migration was required.

Runtime handoff evidence (named Compose project `cai`, 2026-09-03): the old IA
worker was stopped before the new image started. `api`, `ia_worker`,
`audio_worker` and `image_worker` were rebuilt from commit `81b89d1`; PostgreSQL
verified head `0025_webhook_event_keys`, the internal API health returned
`{"status":"ok"}`, and the IA worker log showed the PostgreSQL poller without
Redis initialization. The maintenance dry-run found 80 `ia_status:*` keys and
80 `ia_result:*` keys; all 80 result payloads were valid and matched a durable
classification, with zero missing matches. Both families had 77 keys in the
1-hour-to-24-hour TTL bucket and 3 under one hour. The sanitized report digest
was `527e741d7a8d83186bd894e57eac67f2e99eadd36ed3bf14b80969c64651b02b`.
After 30 seconds, both counts remained 80 and no new compatibility write was
observed. `/queues` reported zero entries in the six retired legacy lists;
`ia_processing` remained present and `processed:*` remained retained (its live
count naturally changed from 67 to 55 during TTL expiry, without deletion by
this issue). A Groq rate-limit retry was persisted by the worker during the
check; it did not change the Redis retirement decision. The required
86400-second observation and destructive apply are intentionally still pending.
