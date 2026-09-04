---
id: 0055
title: "Remove Redis from the application runtime and Compose topology"
type: refactor
status: closed
priority: high
phase: 6
created_at: 2026-09-03
updated_at: 2026-09-03
closed_at: 2026-09-03
related_issues: ["0048", "0049", "0050", "0052", "0053", "0054", "0056"]
blocked_by: []
affects:
  - src/api/routes.py
  - src/api/openapi.py
  - src/workers/ia_worker.py
  - src/core/config.py
  - scripts/redis_maintenance_client.py
  - src/utils/idempotency.py
  - scripts/
  - requirements.txt
  - requirements-maintenance.txt
  - .env.example
  - Dockerfile
  - docker-compose.yml
  - tests/
  - README.md
  - ARCHITECTURE.md
  - specs/0001-shared-data-and-analysis-contract.md
  - specs/0002-digisac-webhook-and-query-api.md
  - specs/0003-durable-finalization-and-media.md
  - specs/0006-api-documentation-and-openapi-contract.md
  - specs/README.md
  - IMPLEMENTATION_PLAN.md
---

## Description

After issues 0052–0054, Redis should no longer carry an active queue, webhook
idempotency marker or IA status/result compatibility view. The remaining
application references are lifecycle wiring, API health/legacy queue metrics,
the IA worker client and configuration. Keeping Redis in the runtime after
those contracts are retired adds an unnecessary dependency and makes a
PostgreSQL-native system appear to have a second work authority.

This issue removes Redis from the supported `api` and `ia_worker` runtime and
from the main Docker Compose topology. PostgreSQL remains the authority for
webhook idempotency, conversation cycles, classifications, media, contacts and
all retry/lease state. Audio, image and contact hydration workers already do
not require Redis.

The removal must be coordinated with the idempotency migration. An old
Redis-only application version cannot safely be restored after Redis is gone,
because it cannot see event keys written to PostgreSQL. Rollback therefore
requires a tested transitional version or a forward fix, not blind restoration
of the pre-0053 image.

## Baseline before implementation

The following inventory records the pre-0055 runtime that this issue retired;
the post-implementation evidence is recorded in the Resolution section below.

- `api` creates a Redis client at lifespan startup, checks it in `/health`,
  reads legacy list lengths in `/queues`, and passes it to webhook idempotency.
- `ia_worker` requires Redis at startup and writes `ia_status:*` and
  `ia_result:*` compatibility keys after PostgreSQL persistence.
- `audio_worker` and `image_worker` use PostgreSQL polling/lease and have no
  Redis client in their active worker implementations.
- The Compose file still defines Redis with AOF storage and gates API/IA on
  Redis health. `REDIS_URL` and `REDIS_DB` remain in `.env.example` and
  `requirements.txt` installs the Redis client for the application image.
- Redis maintenance and historical backfill scripts may still need a client
  after runtime removal. They must be moved to an explicitly separate
  maintenance dependency/context or archived with their recovery evidence;
  deleting them as an incidental cleanup is not allowed.

The implementation baseline was completed only after issues 0052–0054 had
removed the active queue, idempotency and IA compatibility producers. The
remaining Redis container/storage was treated as retained rollback evidence;
its irreversible disposal remains exclusively issue 0056.

## Goals and invariants

- `api` and `ia_worker` start and operate with PostgreSQL and their provider
  dependencies while Redis is absent or unreachable.
- Health and queue observability report PostgreSQL readiness and durable work,
  not Redis connectivity or legacy list lengths.
- The webhook preserves HMAC, event ordering, durable media reservation,
  PostgreSQL idempotency and contact-hydration backoff semantics.
- IA completion and public status/result APIs remain correct from PostgreSQL
  alone.
- No Redis key, volume or service is deleted by this issue unless the separate
  post-retirement issue 0056 is explicitly executed.
- Maintenance tooling remains available in a controlled non-runtime context
  until its retention and historical-recovery decisions are complete.
- The resulting Compose topology has no hidden `depends_on`, environment,
  health-check or package dependency that reintroduces Redis at startup.

## Scope

### Included

- Remove Redis dependencies from API lifespan, webhook dependency injection,
  health and `/queues` handlers while preserving their supported response and
  error contracts through a documented compatibility change.
- Remove Redis client construction and compatibility writes from `IAWorker`.
- Remove runtime-only Redis settings, environment examples and package
  dependencies; provide a separate maintenance dependency if scripts remain.
- Remove the Redis service, named volume declaration and API/IA `depends_on`
  conditions from the application Compose topology.
- Update OpenAPI schemas, tests, README, ARCHITECTURE, specifications and
  implementation planning to describe a PostgreSQL-only runtime.
- Define a coordinated deployment, observation window and rollback/forward-fix
  procedure that preserves durable work.

### Explicitly out of scope

- Migrating webhook idempotency; issue 0053 must be complete first.
- Retiring IA compatibility keys; issue 0054 must be complete first.
- Removing legacy queue entries or generic key families; issue 0052 owns their
  bounded cleanup.
- Deleting the retained Redis volume or any Redis backup; issue 0056 owns that
  irreversible disposal.
- Deleting historical PostgreSQL records, classifications, cycles, media or
  audit state.
- Changing provider models, retry policy, identity rules or Acessórias flows.

## Implementation Plan

1. [x] Build a final dependency inventory from source, tests, Dockerfile,
   requirements, Compose, OpenAPI, dashboards and documented operational
   commands. Treat unknown external Redis clients as a rollout blocker.
2. [x] Verify completion evidence for issues 0052, 0053 and 0054: legacy queues are
   retired, PostgreSQL owns webhook idempotency, IA compatibility writes are
   stopped, and no required historical migration is pending.
3. [x] Refactor API startup and routes. Remove `create_redis_client()` from
   lifespan, remove the Redis dependency from webhook handlers, make health
   verify PostgreSQL only, and make `/queues` return only durable PostgreSQL
   work metrics. Because the route is currently unversioned, update all known
   internal consumers and OpenAPI in the same release; do not silently return
   fabricated zero legacy counts.
4. [x] Refactor `IAWorker` to have no Redis constructor argument, startup ping or
   result/status writes. Preserve PostgreSQL classification persistence,
   cycle transitions, provider cooldown, media gating and logs.
5. [x] Remove `REDIS_URL`, `REDIS_DB` and Redis pool settings from runtime config
   and `.env.example`. Remove `redis` from the application dependency set; if
   historical cleanup/backfill scripts remain, add a separately installed
   `requirements-maintenance.txt` and document its protected execution path.
6. [x] Remove the Redis service, AOF volume declaration, API/IA environment
   overrides and Redis health dependencies from `docker-compose.yml`. Keep
   Postgres/migration ordering and worker restart behavior intact. Do not use
   `down -v` or remove the old volume in this issue.
7. [x] Add a staging Compose profile or equivalent verification that starts API,
   IA, audio and image workers with Redis unavailable. Exercise webhook
   idempotency, durable polling, health, queue metrics, status/result queries,
   hydration backoff and media recovery.
8. [x] Roll out in order: apply required PostgreSQL migrations, stop old API/IA
   versions, start the Redis-free image, verify durable work and providers,
   observe the agreed window, then stop the Redis service while retaining its
   storage for issue 0056. If a defect appears, use the tested transitional
   version or forward fix; never republish all historical Redis entries.
9. [x] Record exact dependency/search/test/runtime evidence, update all affected
   documentation and Graphify, then close in a focused commit.

## Data, compatibility and rollback

- PostgreSQL is the only work and business-data authority. Removing the Redis
  service must not alter durable rows or their retry/lease schedules.
- `/health` must return success when PostgreSQL is healthy even if no Redis
  endpoint exists. A PostgreSQL outage remains a failure.
- `/queues` must expose durable IA/audio/image/cycle metrics and must not imply
  that a removed Redis list is an active backlog. The response compatibility
  decision must be explicit and tested because the endpoint is unversioned.
- The Redis volume and any approved backup are retained for the rollback window
  but are not mounted by the new application containers.
- A rollback to a pre-0053 Redis-only idempotency implementation is prohibited
  unless a tested bridge reconciles PostgreSQL event keys and Redis markers.
  Forward recovery is preferred; durable cycles remain recoverable from
  PostgreSQL.
- Maintenance scripts may connect to a separately authorized Redis endpoint,
  but no application container may import or initialize them at startup.

## Tests

### Runtime and API tests

- API startup succeeds with Redis DNS/port unavailable.
- `/health` depends on PostgreSQL only and returns the documented `503` when
  PostgreSQL is unavailable.
- `/queues` returns durable work counters without querying legacy list lengths.
- Webhook duplicate handling, HMAC validation, media reservation ordering and
  contact-hydration backoff remain correct without Redis.
- Public status/result routes return the same PostgreSQL-backed data and `404`
  behavior as before.

### IA and worker tests

- `IAWorker` can be constructed and run without a Redis client.
- IA completion persists status/result durably and does not write
  `ia_status:*` or `ia_result:*`.
- Audio/image polling, lease expiry, retry schedules and media gates remain
  green with no Redis service.
- Worker crashes and provider cooldown do not recreate a Redis queue.

### Static, Compose and dependency tests

- Source guards find no active runtime Redis import, client creation, ping,
  queue operation or compatibility write outside the declared maintenance
  boundary.
- Compose config has no Redis service, volume or API/IA dependency.
- Runtime requirements no longer install Redis; maintenance requirements do so
  only if retained tooling needs it.
- OpenAPI/tests/documentation agree on the durable-only `/queues` contract.
- Compileall, Pyright, focused tests, the disposable PostgreSQL runner and the
  Redis-free staging Compose check pass.

## Acceptance Criteria

- [x] API and IA worker run successfully with Redis absent and no startup path
  creates or pings a Redis client.
- [x] Health, webhook idempotency, `/queues`, IA status/result and media/contact
  flows use their PostgreSQL or provider contracts without Redis.
- [x] No active runtime path publishes/consumes legacy queues or writes IA
  compatibility keys.
- [x] Compose, Dockerfile, runtime requirements and environment examples no
  longer require Redis; maintenance tooling has a separate documented boundary
  if retained.
- [x] `/queues` and OpenAPI have an explicit, tested durable-only compatibility
  decision; no legacy fields are silently fabricated.
- [x] A Redis-free staging run proves startup, webhook duplicates, durable
  polling, retries, leases, media gates and public queries.
- [x] Rollout and rollback/forward-fix procedures account for the PostgreSQL
  idempotency cutover and retain the Redis volume without deleting it here.
- [x] No PostgreSQL data or retained Redis backup is deleted, and all tests,
  verification, documentation and graph evidence are accurate.

## References

- `docker-compose.yml`, `Dockerfile`, `requirements.txt`,
  `requirements-maintenance.txt` and `.env.example`: runtime-free topology and
  explicit maintenance boundary.
- `src/api/routes.py`: lifespan, health, queue metrics and webhook dependency.
- `src/workers/ia_worker.py`: current Redis lifecycle and compatibility writes.
- `scripts/redis_maintenance_client.py`: maintenance-only client protocol/factory.
- `issues/0048_-_prevent-duplicate-ia-cycle-queue-republication.md`,
  `issues/0049_-_migrate-audio-transcription-to-postgresql-polling.md` and
  `issues/0050_-_migrate-image-extraction-to-postgresql-polling.md`: completed
  PostgreSQL polling cutovers.
- `issues/0052_-_retire-validated-legacy-redis-queues.md`: queue cleanup.
- `issues/0053_-_move-webhook-idempotency-to-postgresql.md`: active API
  dependency migration.
- `issues/0054_-_retire-ia-redis-status-result-compatibility.md`: IA key sunset.
- `issues/0056_-_dispose-retained-redis-storage.md`: separate irreversible
  storage disposal.

## Resolution

Implemented and deployed in the named `cai` Compose project on 2026-09-03.

- `src/api/routes.py` no longer creates, injects, pings or closes a Redis client;
  webhook media reservations, PostgreSQL idempotency, `/health` and `/queues`
  operate without Redis. `/queues` now has an explicit durable-only contract and
  no longer returns the six legacy list fields.
- `src/core/config.py` and `requirements.txt` no longer expose/install Redis.
  `scripts/redis_maintenance_client.py` plus
  `requirements-maintenance.txt` provide the only historical Redis boundary and
  require `MAINTENANCE_REDIS_URL` explicitly. The API image excludes the
  historical backfill and does not install the maintenance dependency.
- `docker-compose.yml` no longer defines the Redis service, `redis_data` volume,
  API/worker Redis environment or Redis health dependencies. The old `cai-redis-1`
  container/storage was stopped and retained; no key, volume, PostgreSQL row or
  backup was deleted. Issue 0056 owns final disposal.
- Focused runtime/route/OpenAPI/dependency tests passed **61 tests**. The
  canonical runner passed compileall, Pyright, **297 passed and 90 skipped**
  offline, and **90 passed, 297 deselected** against disposable PostgreSQL 16
  with head `0025_webhook_event_keys`. `docker compose -p cai config --quiet`
  passed and the rebuilt named runtime returned `{"status":"ok"}` from inside
  the API container; all application workers started without Redis.

The change is intentionally not a rollback to a pre-0053 image: the PostgreSQL
`webhook_event_keys` ledger remains authoritative. Any defect after rollout
requires the documented transitional version or forward-fix path. Historical
Redis cleanup and compatibility apply remain bounded maintenance operations and
are not implicit in this closure.
