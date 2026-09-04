---
id: 0056
title: "Dispose retained Redis storage after the decommission observation window"
type: maintenance
status: open
priority: medium
phase: 6
created_at: 2026-09-03
updated_at: 2026-09-04
closed_at: ~
related_issues: ["0037", "0052", "0053", "0054", "0055"]
blocked_by: ["0054"]
affects:
  - docker-compose.yml
  - src/workers/audio_worker.py
  - reports/
  - backups/
  - README.md
  - ARCHITECTURE.md
  - IMPLEMENTATION_PLAN.md
  - specs/0001-shared-data-and-analysis-contract.md
  - specs/0002-digisac-webhook-and-query-api.md
  - specs/0003-durable-finalization-and-media.md
  - specs/0004-reproducible-verification-baseline.md
  - specs/0006-api-documentation-and-openapi-contract.md
  - specs/README.md
---

## Description

Issue 0055 removes Redis from the application runtime and Compose topology but
intentionally retains its named storage and any approved backup during a
rollback window. This issue is the final, irreversible disposal step. It must
not be combined with the code cutover: once the Redis volume and backups are
deleted, old Redis-only application versions cannot be restored safely and
historical compatibility data cannot be recovered from Redis.

The target volume from the former Compose project is the project-scoped
`cai_redis_data` volume derived from the old `redis_data` declaration. The
current Compose topology no longer declares Redis, but the exact volume ID,
project and mount must still be resolved at execution time; a broad volume
prune or `docker compose down -v` is prohibited.

## Goals and invariants

- Prove that Redis is no longer required by any runtime, maintenance or
  historical recovery operation before disposal.
- Preserve PostgreSQL backups and all durable cycle, media, contact,
  classification, idempotency and audit state.
- Retain only explicitly approved non-Redis evidence required by policy.
- Delete exactly the reviewed project-scoped Redis storage and no other volume.
- Make the irreversible nature and loss of Redis-based rollback explicit.

## Scope

### Included

- Verify issue 0055’s Redis-free deployment and observation window.
- Confirm zero Redis service/container dependency, zero legacy queues and no
  remaining compatibility-key growth.
- Confirm historical backfill and cleanup reports are archived and their
  required outcomes are represented in PostgreSQL or explicitly waived.
- Take and validate the final PostgreSQL backup and retain the approved
  application image/configuration rollback artifacts.
- Resolve and delete only the exact `cai` Redis container/storage target after
  two-person or explicitly authorized review.
- Update operational documentation and close with exact commands, target IDs,
  timestamps and verification evidence.

### Explicitly out of scope

- Deleting PostgreSQL volumes, backups, migrations, cycles, classifications,
  media, contact hydration rows or audit ledgers.
- Replaying old Redis queues or rebuilding work from Redis.
- Removing Redis before issue 0055 is complete and observed.
- `docker system prune`, `docker volume prune`, `down -v`, `FLUSHDB`,
  `FLUSHALL` or any wildcard deletion.
- Deleting source maintenance scripts or historical backfill code without a
  separate archival decision.

## Implementation Plan

1. Verify that issues 0052, 0053 and 0055 are closed with evidence, that issue
   0054 has completed its compatibility-key observation/apply gate, and that
   the deployed revision is Redis-free. Confirm API, IA, audio and image workers are healthy,
   webhook idempotency is PostgreSQL-backed, and durable PostgreSQL metrics are
   stable.
2. Observe the agreed production/staging window, including at least one full
   former Redis TTL period and a normal traffic interval. Confirm no Redis
   connection attempts, legacy queue growth, compatibility-key writes or
   historical recovery requirement occurs.
3. Archive the final bounded cleanup reports, PostgreSQL dump, image/config
   digests and rollback instructions. Validate the dump can be listed/restored
   in a disposable PostgreSQL instance; do not test restoration against the
   production database.
4. Resolve the exact container, Compose project, named volume and mount using
   read-only inspection. Require the expected project/name and a zero active
   attachment count before deletion. Stop/remove only the reviewed Redis
   service/container if it still exists; do not affect Postgres or workers.
5. Delete only the exact project-scoped Redis volume after explicit approval.
   Capture the command result and post-delete `docker volume inspect` failure
   for that exact name. If the name or attachment does not match, stop.
6. Run post-disposal checks: Compose status, API health, PostgreSQL connection,
   durable queue metrics, worker logs, webhook duplicate tests in staging and
   source/config search for forbidden runtime Redis dependencies.
7. Update this issue, README, ARCHITECTURE and implementation planning to
   distinguish irreversible storage disposal from the prior code cutover.

## Data, rollback and safety

- The final PostgreSQL dump is the recovery authority for durable business
  state. A Redis backup, if retained by policy, is only historical evidence
  and must not be replayed without a new reviewed recovery issue.
- After volume deletion, rollback is a forward deployment or restoration of a
  PostgreSQL-backed contract. Restoring a pre-0053 Redis-only image is
  prohibited.
- The operator must record exact target names/IDs, not rely on unresolved
  environment variables or broad globs.
- A failed post-disposal check does not authorize recreating old queues; stop
  and investigate durable PostgreSQL state.

## Tests and operational checks

- A source/config search finds no active Redis runtime dependency after issue
  0055, while retained maintenance tooling is separately classified.
- Redis-free Compose/staging startup and health checks pass for API and all
  workers.
- PostgreSQL durable counts, leases, retry schedules and classifications are
  unchanged by the disposal.
- The final PostgreSQL backup is readable in a disposable target.
- Exact container/volume inspection confirms only the reviewed Redis target is
  detached and deleted.
- Post-disposal API, workers, webhook idempotency, media polling, contact
  hydration and `/queues` checks pass.
- No broad Docker or Redis cleanup command appears in the execution report.

## Acceptance Criteria

- [ ] Issues 0052, 0053, 0054 and 0055 are closed with complete evidence.
- [ ] The Redis-free observation window passed without runtime connection
  attempts, queue growth, compatibility-key writes or recovery gaps.
- [ ] Final PostgreSQL backup and required rollback artifacts are archived and
  validated in a disposable environment.
- [ ] The exact project-scoped Redis container and volume are identified,
  detached, and deleted only after explicit approval.
- [ ] No PostgreSQL data, backup, worker, application container or unrelated
  Docker volume is changed or deleted.
- [ ] Post-disposal API, worker, PostgreSQL, webhook and durable queue checks
  pass.
- [ ] The loss of Redis-based rollback is documented, and any remaining
  maintenance/backfill scripts have an explicit archival or retention owner.
- [ ] The issue records exact target identifiers, commands, timestamps and
  evidence without secrets or raw payloads.

## Pre-disposal verification (2026-09-04)

A pre-disposal check was performed at `2026-09-04T12:10:49Z` against the current checkout and the named
Compose runtime. It is evidence for readiness only; it is not the irreversible
disposal operation.

- The current revision is `f4c2dad` and `docker compose -p cai config --quiet`
  passes. The active services are `postgres`, `migrate`, `api`,
  `audio_worker`, `ia_worker`, `image_worker` and `ralph`; Redis is absent from
  the current Compose configuration and its `config --volumes` output.
- The API and all four application workers are running; the API container is
  healthy. Internal checks returned `/health` HTTP 200 with `{"status":"ok"}`
  and `/queues` HTTP 200. The observed durable snapshot was
  `audio_due=0`, `audio_scheduled=0`, `audio_leased=0`, `image_due=35`,
  `image_scheduled=1`, `image_leased=0`, `ia_due=15`, `ia_scheduled=1` and
  `ia_leased=0`; the PostgreSQL schema head is
  `0025_webhook_event_keys`.
- The exact retained target resolves to container `cai-redis-1`, ID
  `f0e6824f2629ae08953e0a30adee113a2013710d25f7e2f0c1da14d20c06ecd5`, status
  `exited`, and volume `cai_redis_data`. The container
  has the Compose project label `cai`, service label `redis`, and one mount at
  `/data`; the volume has the project label `cai` and the Compose volume label
  `redis_data`. The only container associated with that volume is the stopped
  `cai-redis-1`. No PostgreSQL or worker container is attached to it.
- The Redis-free `api`, `ia_worker`, `audio_worker` and `image_worker` were
  started at `2026-09-03T21:20:45Z`. Issue 0054 remains open and requires a
  complete 86400-second observation window, so the earliest observation gate
  is `2026-09-04T21:20:45Z`. Its compatibility keys must remain retained until
  that gate and its bounded apply are complete.
- The three versioned dumps under `backups/` were successfully listed with
  `pg_restore` from the PostgreSQL container, but they are dated historical
  artifacts, not the final pre-disposal backup. The current checkout contains
  only the older residue report under `reports/`; the external 0052–0055
  report directories are not present. A final dump and archived rollback
  artifacts must therefore be produced and validated after the observation
  gate, in a disposable PostgreSQL target.

Decision: do not remove `cai-redis-1` or `cai_redis_data` in this pass. No
`docker volume rm`, `docker compose down -v`, Docker prune, `FLUSHDB` or
`FLUSHALL` command was executed. The issue remains open and is explicitly
blocked by issue 0054 plus the missing final-backup/report gate. The remaining
maintenance and backfill source is retained for archival review; it is not
part of the application runtime.

## References

- `issues/0055_-_remove-redis-from-application-runtime.md`: Redis-free runtime
  cutover and retained-storage boundary.
- `issues/0052_-_retire-validated-legacy-redis-queues.md`: queue retirement.
- `issues/0053_-_move-webhook-idempotency-to-postgresql.md`: event-ledger
  migration and rollback constraint.
- `issues/0054_-_retire-ia-redis-status-result-compatibility.md`: IA key sunset.
- `issues/0037_-_audit-and-remove-legacy-redis-residues.md`: allowlist and
  destructive-operation safety rules.
- `docker-compose.yml`: current `redis_data` volume and service topology.
- `specs/0001-shared-data-and-analysis-contract.md`,
  `specs/0002-digisac-webhook-and-query-api.md`,
  `specs/0003-durable-finalization-and-media.md`,
  `specs/0004-reproducible-verification-baseline.md`,
  `specs/0006-api-documentation-and-openapi-contract.md` and
  `specs/README.md`: synchronized contracts and verification boundary.
- `README.md` Operação: backup and bounded cleanup procedures.

## Resolution

<!-- Filled only after irreversible storage disposal and post-delete verification. -->

Ainda não resolvida. A verificação de pré-disposição de 2026-09-04 identificou
o alvo exato, mas não apagou o container nem o volume porque a janela completa
do issue 0054 e o backup PostgreSQL final ainda não foram concluídos.
