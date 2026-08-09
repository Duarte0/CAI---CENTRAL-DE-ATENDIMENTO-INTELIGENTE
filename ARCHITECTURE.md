# CAI System Architecture

**Status:** implementation-derived architecture baseline  
**Reference date:** 2026-08-09  
**Audience:** engineering and operations

This document describes the architecture implemented in this repository. Source
code, Alembic migrations, and tests are authoritative. \`README.md\`,
\`IMPLEMENTATION_PLAN.md\`, and the specifications provide supporting context;
planned or not-yet-verified behavior is marked as such.

## 1. System purpose and boundaries

CAI receives DigiSac webhook events, reconstructs ticket conversations,
enriches audio and image messages with Groq, classifies the customer's intent,
and exposes the result through an HTTP API.

CAI does not open tickets, reply to customers, transfer tickets, or make
department/agent decisions through the language model. It ends at analysis and
result availability.

\`\`\`mermaid
flowchart LR
    D[DigiSac] -->|webhooks and message history| API[FastAPI API]
    API --> R[(Redis)]
    API --> P[(PostgreSQL)]
    API --> D
    R --> IW[IA worker]
    R --> AW[Audio worker]
    R --> IMW[Image worker]
    AW --> D
    IMW --> D
    IW --> D
    AW --> G[Groq]
    IMW --> G
    IW --> G
    AW --> P
    IMW --> P
    IW --> P
    API --> C[Query and operations API]
    P --> C
    R --> C
\`\`\`

## 2. Runtime components

| Component | Responsibility | Durable state or coordination |
| --- | --- | --- |
| \`api\` | FastAPI lifecycle, HMAC validation, webhook parsing, normalization, media reservation, ticket events, finalization scheduling, and queries. | Writes PostgreSQL and Redis. |
| \`webhook_adapter\` | Converts DigiSac envelopes into normalized \`DigisacMessage\` values. | No persistence. |
| \`message_filter\` / \`media\` | Removes bot/unsupported messages and determines effective media type, including image MIME documents. | No persistence. |
| \`ia_worker\` | Consumes legacy IA jobs and persistent cycle jobs, retrieves history, builds context, calls Groq, and persists classifications. | PostgreSQL claims, leases, snapshots, and classifications; Redis queues/results. |
| \`audio_worker\` | Downloads audio from DigiSac, calls Groq transcription, and persists status/result. | PostgreSQL reservation/status; Redis queue and dead letter. |
| \`image_worker\` | Downloads image data, calls Groq vision, and persists extracted text/status. | PostgreSQL reservation/status; Redis queue and dead letter. |
| \`digisac_directory\` | Periodically synchronizes departments and users for assignment-name resolution. | PostgreSQL directory cache and sync state. |
| \`postgres\` | Source of truth for analysis, media state, assignment history, directory data, and persistent cycles. | PostgreSQL 16, managed by Alembic. |
| \`redis\` | Lightweight work transport and transient coordination. | Redis 7 with AOF in Compose. |
| \`migrate\` | Applies the configured Alembic revision before application services start. | No application schema creation at runtime. |

The application uses one PostgreSQL connection pool per process. Database
initialization verifies the Alembic schema and does not create or mutate
tables. Redis is not the authoritative store for durable classifications or
media results.

## 3. Inbound webhook flow

\`POST /webhook/digisac\` accepts \`ticket.created\`, \`ticket.updated\`,
\`message.created\`, and \`message.updated\`.

\`\`\`mermaid
sequenceDiagram
    participant D as DigiSac
    participant A as FastAPI
    participant R as Redis
    participant P as PostgreSQL
    participant W as Worker

    D->>A: signed webhook event
    A->>A: verify HMAC and parse envelope
    A->>A: normalize and filter event/message
    alt human audio or image
        A->>P: reserve media row
        A->>R: publish media job
    else text in legacy mode
        A->>R: atomic buffer append
    else persistent-history mode
        A->>P: maintain ticket/cycle state
    end
    A-->>D: 202 accepted or 200 ignored
    R->>W: consume durable/lightweight job
    W->>P: persist processing result
\`\`\`

### Authentication and normalization

- If \`WEBHOOK_SECRET\` is configured, the raw request body must carry a valid
  \`X-Digisac-Signature\` HMAC-SHA256 value. Invalid or missing signatures return
  \`401\` before normalization.
- Invalid JSON or a non-object \`data\` field returns \`400\`.
- Bot messages, \`isFromMe\` messages, empty chats, missing tickets, and
  unsupported message types are ignored without queueing or buffering.
- Normalized file data keeps safe metadata such as ID, name, public name,
  extension, and MIME type. Download URLs, tokens, and binary data do not enter
  Redis buffers, context snapshots, or classification records.
- A \`document\` whose MIME type starts with \`image/\` is treated as an image;
  other documents remain documents.

### Ticket events

Ticket events record assignment changes idempotently using an event key. The
history preserves department/user IDs even when the local directory cannot
resolve their names. The application never invents historical transfers.

Ticket protocol is retained as application metadata. It is associated with the
classification/result after persistence and is not included in the model
prompt.

## 4. Redis coordination

Redis carries work and short-lived coordination, not the durable business
record.

### Queues and dead letters

| Queue | Producer | Consumer | Dead letter |
| --- | --- | --- | --- |
| \`ia_queue\` | API and reconcilers | \`ia_worker\` | \`ia_dead_letter\` |
| \`audio_transcription_queue\` | API/recovery | \`audio_worker\` | \`audio_transcription_dead_letter\` |
| \`image_extraction_queue\` | API/recovery | \`image_worker\` | \`image_extraction_dead_letter\` |

The legacy IA worker also uses \`ia_processing\` while a job is claimed.
Persistent jobs use PostgreSQL publication markers and leases so a failure
between database persistence and Redis publication can be recovered.

### Transient keys

Important key families include:

- \`ticket_last_message_at:*\` and \`ticket_protocol:*\` for short-lived ticket
  coordination;
- \`processed:*\` for temporary webhook idempotency;
- \`ia:cycle:{cycle_id}\` for
  classification coordination/idempotency.

The former legacy buffer and debounce key families are scheduled for removal
with the complete finalization refactor.

## 5. Persistent finalization mode

Persistent DigiSac-history finalization is the only supported mode. The
\`DIGISAC_HISTORY_FINALIZATION_ENABLED\` flag will be removed in the complete
refactor.

### 5.1 Persistent DigiSac-history mode

When enabled, each ticket opening/reopening creates or reuses a persistent
conversation cycle. Closing a ticket persists the cycle before publishing its
IA job.

The IA worker then:

1. claims the cycle using PostgreSQL state and a lease;
2. retrieves all pages of DigiSac ticket history;
3. deduplicates and orders messages by timestamp and ID;
4. infers the cycle boundary and excludes messages outside the cycle;
5. filters bots, technical events, unsupported content, and other excluded
   records with audit counts;
6. creates the ordered cycle-message membership and safe snapshot;
7. reconciles audio/image extraction jobs;
8. waits for scheduled media at \`next_attempt_at\` when required;
9. renders the context and summarizes oversized context when necessary;
10. calls Groq and persists the classification, snapshot, and result;
11. transitions the cycle to a terminal state and publishes compatibility
    status/result data where applicable.

Persistent cycle publication is guarded by \`enqueued_at\`. Reconcilers only
republish due work and recover jobs whose publication/lease has expired.

### 5.2 Legacy Redis-buffer mode (scheduled for removal)

The former mode appended messages to \`buffer:{conversation_id}\` and used
Redis debounce generations. Its flag, key families, debounce logic, IA worker
buffer handling, and single-replica recovery limitation will be removed. The
persistent cycle mode remains as the only mode after the refactor.

## 6. Persistent cycle state machine

The persistent cycle schema is introduced by migrations \`0013\` and \`0014\`.
The implemented states include:

\`\`\`text
open
  -> pending
  -> recovering_messages
  -> waiting_media
  -> building_context
  -> summarizing
  -> classifying
  -> completed
  -> completed_with_warnings

Any recoverable processing failure -> retryable_failure -> pending
Terminal processing failure -> failed
Terminal image extraction failure -> media_blocked
media_blocked + successful dependent image recovery -> waiting_media/pending
\`\`\`

The exact transition is persisted in PostgreSQL and protected by expected
status checks and leases. Concurrent claimers use row locking and
\`FOR UPDATE SKIP LOCKED\` patterns where applicable.

## 7. Media processing

Media work is reserved durably before it is published to Redis. If publication
fails, the reservation is released so a repeated webhook or reconciler can
retry it.

### Audio

\`audio_worker\` downloads the DigiSac file, sends it to the configured Groq
transcription model, and updates \`message_transcriptions\`. Transient provider
failures honor \`Retry-After\`, backoff, and the configured attempt limit.
Terminal audio failure leaves a safe marker in the context and can produce
\`completed_with_warnings\`.

### Image

\`image_worker\` validates image data and MIME type, downloads and encodes the
content, calls the configured vision model, and updates
\`message_image_extractions\`. Truncated or invalid vision output is not treated
as a successful extraction.

A terminal image failure places a dependent persistent cycle in \`media_blocked\`;
the IA worker must not classify incomplete context. A later successful image
recovery wakes only cycles that depend on that image.

\`next_attempt_at\` is the durable source of the next eligible attempt. Provider
retry timing and local backoff use the later applicable time, preventing broad
retry loops during quota or service failures.

## 8. Context and IA contract

The context pipeline is implemented in \`finalization.py\`, \`models.py\`, and
\`ia_worker.py\`.

- Client and attendant messages remain in chronological order.
- Attendant messages provide context but are not the classification target.
- Bot messages are excluded.
- Quoted replies use bounded excerpts.
- URLs, signed download links, tokens, and binary media are excluded from
  persisted snapshots.
- Oversized context is chunked without splitting messages where possible,
  summarized by blocks, and then sent to final classification.

The model contract contains only:

\`\`\`json
{
  "intent_type": "question",
  "confidence": 0.98,
  "title": "Erro ao emitir nota fiscal",
  "description": "O cliente informa que não consegue emitir notas fiscais."
}
\`\`\`

\`department\`, \`agent\`, \`protocol\`, \`display_title\`, IDs, message counts, and
cycle metadata are derived or added by application code. The intent taxonomy is
defined in \`src/core/intents.py\`.

## 9. PostgreSQL data model

Alembic owns the schema through \`0014_retry_scheduling\`. The main data groups
are:

| Data group | Tables | Purpose |
| --- | --- | --- |
| Analysis | \`ia_classifications\`, \`classification_messages\` | Classification identity, result, full context, and ordered supporting messages. |
| Media | \`message_transcriptions\`, \`message_image_extractions\` | Durable reservation, attempts, provider model, status, retry schedule, error, and extracted text. |
| Assignment | \`ticket_assignment_history\`, \`ticket_assignment_event_keys\` | Chronological, idempotent ticket assignment history. |
| Directory | \`digisac_departments\`, \`digisac_users\`, \`digisac_directory_sync_state\` | Local lookup cache and synchronization state. |
| Cycles | \`conversation_processing_cycles\`, \`conversation_cycle_messages\` | Persistent finalization state, sequence, lease, snapshot, scheduling, status, and ordered membership. |

Classifications expose UUIDv7 \`public_id\` values. JSON snapshots and lists use
JSONB where defined by the schema, and durable timestamps use \`TIMESTAMPTZ\`.
The application retains data indefinitely for historical and analytical value,
including future FAQ use. It does not auto-delete or archive data; no cleanup
jobs, retention schema changes, or LGPD-driven automation are planned. Manual
deletion, if ever needed, is performed case by case by direct PostgreSQL query.

## 10. HTTP API and operational surface

Implemented routes include:

| Route | Purpose |
| --- | --- |
| \`POST /webhook/digisac\` | Authenticated production webhook ingestion. |
| \`POST /webhook/debug\` | Normalization/debug view without normal processing writes. |
| \`GET /health\` | PostgreSQL and Redis readiness. |
| \`GET /queues\` | Redis queue and dead-letter metrics. |
| \`GET /v1/conversations/{conversation_id}/status\` | Conversation processing status. |
| \`GET /v1/conversations/{conversation_id}/result\` | Latest conversation result. |
| \`GET /v1/conversations/{conversation_id}/cycles\` | Persistent cycle list. |
| \`GET /v1/cycles/{cycle_id}/status\` | Persistent cycle status. |
| \`GET /v1/cycles/{cycle_id}/result\` | Persistent cycle result. |

The webhook normally returns \`202\`; safely ignored events return \`200\` with an
ignore reason. Missing persisted entities return \`404\`. Logs and operational
responses should expose IDs and status without raw bodies or secrets.

## 11. Deployment topology

\`docker-compose.yml\` defines:

1. PostgreSQL 16.14 with a persistent volume and readiness check.
2. Redis 7 with AOF persistence and readiness check.
3. A one-shot \`migrate\` service running \`alembic upgrade\`.
4. \`api\`, \`ia_worker\`, \`audio_worker\`, and \`image_worker\`, all gated on healthy
   PostgreSQL, healthy Redis, and successful migration completion.
5. An optional \`ralph\` development/verification service.

The application requires Python 3.11+, PostgreSQL, Redis, DigiSac and Groq
credentials, and \`ffmpeg\` for audio processing. The active deployment project
is conventionally named \`cai\`:

\`\`\`bash
docker compose -p cai up --build -d
docker compose -p cai ps
docker compose -p cai logs -f api ia_worker audio_worker image_worker
\`\`\`

The persistent-history flag must be changed through a coordinated rollout:
migrations first, then recreate API and IA worker with the new value.

## 12. Reliability and recovery invariants

- PostgreSQL persistence precedes media/cycle Redis publication.
- A failed Redis publication clears the corresponding PostgreSQL publication
  marker so reconciliation can retry it.
- Persistent claims and leases recover work abandoned by process failure.
- Media reservations are idempotent by message ID.
- Cycle and classification identity prevent duplicate terminal analysis.
- Retry scheduling respects provider timing and does not broadly republish
  future work.
- Terminal image failures block classification; terminal audio failures preserve
  a warning marker.
- Unrelated dead-letter entries are not removed during targeted recovery.
- No retention, archival, or deletion policy is currently implemented.

## 13. Current gaps and delivery follow-up

The architecture is implemented, but the repository's implementation plan
records these delivery limitations:

- The full intended test suite is not fully versioned or deterministic; legacy
  ticket-closure tests currently assume the Redis path while the workspace flag
  enables persistent cycles.
- PostgreSQL-marked tests require an isolated disposable PostgreSQL service;
  static checks do not prove deployment/runtime health.
- No CI runner currently enforces compile, strict Pyright, offline tests, and
  PostgreSQL integration tests together.
- Data retention is indefinite; no automatic deletion or archival is planned.
- Historical assignment events are preserved chronologically to track department
  routing from ticket open to close and support future Acessórias integration.
- The legacy Redis-buffer mode is approved for complete deprecation and removal;
  persistent history is the only long-term mode.

These items are not architectural failures of the current implementation, but
they limit release verification and future evolution decisions.

## 14. Source map

- API and webhook lifecycle: \`src/api/routes.py\`, \`src/api/webhook_adapter.py\`,
  \`src/api/middleware.py\`.
- Shared message and context rules: \`src/core/models.py\`,
  \`src/core/message_filter.py\`, \`src/core/media.py\`,
  \`src/core/finalization.py\`.
- PostgreSQL access and cycle coordination: \`src/core/db.py\` and
  \`alembic/versions/0001_initial.py\` through
  \`0014_durable_retry_scheduling.py\`.
- Worker behavior: \`src/workers/ia_worker.py\`,
  \`src/workers/audio_worker.py\`, and \`src/workers/image_worker.py\`.
- Configuration and deployment: \`src/core/config.py\`, \`.env.example\`,
  \`docker-compose.yml\`, and \`Dockerfile\`.
- Baseline contracts and known gaps: \`IMPLEMENTATION_PLAN.md\` and \`specs/\`.
