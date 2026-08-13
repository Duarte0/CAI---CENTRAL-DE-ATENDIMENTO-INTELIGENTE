# CAI — Product Requirements Document

**Status:** implementation-derived baseline
**Version:** 1.0
**Reference date:** 2026-08-09
**Audience:** product, engineering, operations, and integration owners

## 1. Product summary

CAI is a DigiSac conversation analyzer. It receives ticket and message events,
reconstructs the relevant conversation, enriches audio and image messages,
classifies the customer's intent with Groq, and exposes an auditable result
through an HTTP API.

CAI is an analysis and operational-support system. It does not open tickets,
reply to customers, transfer tickets, or autonomously decide the department or
agent responsible for a ticket.

The requirements in this document describe capabilities present in the current
repository. Product policies approved by the product owner are recorded as
decisions and distinguished from capabilities already implemented in the source.

## 2. Users and system actors

The implementation establishes these technical actors:

| Actor | Need | Interaction |
| --- | --- | --- |
| DigiSac | Deliver ticket, assignment, and message events and provide ticket history/media. | Webhooks and DigiSac REST API. |
| Analysis API consumer | Submit no manual analysis request; receive status and classification results for a conversation or cycle. | HTTP query endpoints; a single internal operator uses the system. |
| Operations owner | Inspect readiness, queues, dead letters, cycle states, and failures. | Health and operational endpoints, logs, and PostgreSQL. |
| CAI workers | Process classification, audio, and image jobs reliably. | Redis queues, PostgreSQL, DigiSac, and Groq. |

The system has one internal operator and is not exposed to external or
third-party API consumers. Query endpoints do not require login, API keys,
JWTs, or a separate authorization layer. When the Acessórias integration is
built, access-control requirements will be revisited.

## 3. Product goals

CAI must:

1. Convert DigiSac conversation activity into a consistent, structured intent
   classification.
2. Preserve enough context and metadata to explain which messages and ticket
   cycle produced a result.
3. Handle text, documents, audio, voice/PTT, and images without exposing media
   URLs, tokens, or binary content in durable analysis snapshots.
4. Continue processing safely across duplicate webhooks, worker failures,
   provider throttling, and process restarts.
5. Provide operational visibility into processing status, queues, dead letters,
   and persistent cycles.
6. Build a classification corpus that supports a future FAQ system and
   preserve conversation history as a long-term record of customer-service
   interactions and conversation evolution.
7. Track classification results over time to observe AI evolution and support a
   future Acessórias integration for department routing and request creation.

## 4. Non-goals and product boundaries

CAI does not currently:

- create or close DigiSac tickets;
- send replies or other customer-facing messages;
- transfer tickets;
- assign a department or agent through the language model;
- use `protocol`, `department`, or `agent` as model-generated classification
  fields;
- delete or archive data automatically; data is retained indefinitely, with
  manual deletion by direct PostgreSQL query only when needed;
- provide a general-purpose public API contract beyond the implemented routes.

## 5. End-to-end workflow

### 5.1 Ingestion and normalization

The production webhook is `POST /webhook/digisac`. It accepts:

- `ticket.created`;
- `ticket.updated`;
- `message.created`; and
- `message.updated`.

When `WEBHOOK_SECRET` is configured, the raw request body must contain a valid
HMAC-SHA256 `X-Digisac-Signature`. Invalid signatures are rejected before
normalization.

The webhook must reject invalid JSON and malformed payloads with a client error.
Supported message types are chat/text, document, audio, voice/PTT, and image.
A document with an `image/*` MIME type is treated as an image; other documents
remain documents.

Bot messages, `isFromMe` messages, unsupported types, empty chats, and messages
without a ticket are ignored without creating analysis work. Normalized file
metadata is limited to safe identifiers and descriptive fields; download URLs,
  tokens, and binary data are not placed in queue payloads or analysis snapshots.

### 5.2 Ticket lifecycle and assignment history

Ticket creation and reopening establish the beginning of a persistent
conversation cycle. Ticket updates capture observed department and user
assignments idempotently and chronologically.

Ticket closure requires a valid protocol before classification is scheduled.
The protocol is persisted as metadata and may be used to construct
`display_title`; it is not included in the model context.

The system preserves unresolved external department/user IDs and does not invent
transfers or names. A synchronized DigiSac directory is used to resolve names
when available.

### 5.3 Media enrichment

Audio and image messages are durably reserved in PostgreSQL before lightweight
Redis jobs are published.

- `audio_worker` downloads audio, converts it when necessary, calls the
  configured Groq transcription model, and persists the transcription state.
- `image_worker` downloads and validates image data, calls the configured Groq
  vision model, and persists extracted content.

Transient provider failures honor retry timing, including `Retry-After`, local
backoff, and configured attempt limits. A terminal audio failure may allow a
classification with a warning marker. A terminal image failure blocks the
dependent persistent cycle until the image is successfully recovered.

### 5.4 Finalization mode

Persistent DigiSac-history finalization is the only supported finalization path.
The former feature flag, Redis buffer, debounce generation, and fallback worker
loop have been removed. PostgreSQL cycles are the durable authority and Redis
only transports persistent-cycle/media jobs and transient operational values.

#### Persistent DigiSac-history mode

CAI persists a cycle before publishing its IA job. The IA worker:

1. claims the cycle using PostgreSQL state and a lease;
2. retrieves all pages of the ticket's DigiSac history;
3. deduplicates and orders messages by timestamp and ID;
4. limits messages to the cycle boundary;
5. filters technical, unsupported, and bot records with audit counts;
6. persists ordered message membership and a safe snapshot;
7. reconciles audio and image results;
8. waits for due media retries when required;
9. builds or summarizes the context;
10. classifies the intent;
11. persists the classification, snapshot, and terminal cycle state; and
12. publishes compatibility status/result data where applicable.

Persistent claims, leases, publication markers, `next_attempt_at`, and
reconcilers allow work abandoned by a process failure to be recovered without
duplicating terminal classifications.


## 6. Context and AI contract

The context keeps customer and attendant messages in chronological order.
Attendant messages provide context but are not the classification target. Bot
messages are excluded. Quoted replies use bounded excerpts.

Audio transcriptions and image extractions are rendered into the context when
available. Documents are represented by safe metadata. Context larger than the
configured safe input budget is split without cutting messages when possible,
summarized by blocks, and then sent to the final classification step.

The model output contract contains only these fields:

```json
{
  "intent_type": "question",
  "confidence": 0.98,
  "title": "Error issuing an invoice",
  "description": "The customer reports that invoices cannot be issued."
}
```

The supported `intent_type` values are:

`question`, `problem`, `request`, `complaint`, `payment`, `billing`,
`financial`, `document`, `protocol`, and `other`.

The application, not the model, adds `department`, `agent`, `protocol`,
`display_title`, identifiers, message counts, and cycle metadata. When a
protocol exists, `display_title` follows `[{protocol}] - {title}` without
changing `title`.

Incomplete or truncated model responses must not be persisted as valid
classifications.

## 7. Output and operational interfaces

The current HTTP surface is:

| Endpoint | Purpose |
| --- | --- |
| `POST /webhook/digisac` | Authenticated production ingestion. |
| `GET /health` | Verify Redis and PostgreSQL readiness. |
| `GET /queues` | Expose queue, dead-letter, and cycle metrics. |
| `GET /conversations/{conversation_id}/status` | Return latest conversation processing state. |
| `GET /conversations/{conversation_id}/result` | Return the latest available result. |
| `GET /conversations/{conversation_id}/cycles` | List persistent cycles for a conversation. |
| `GET /cycles/{cycle_id}/status` | Return a persistent cycle record. |
| `GET /cycles/{cycle_id}/result` | Return a persistent cycle result. |

The mounted conversation and cycle query routes are currently unversioned.
`/v1/` and `/v2/` remain future compatibility policy only; they are not aliases
or implemented routes in this checkout.

Normal webhook processing returns `202`. Safely ignored events return `200` with
an ignore reason. Missing conversations, cycles, or unavailable results return
`404`; malformed payloads return `400`; invalid webhook signatures return `401`;
and unavailable dependencies cause health failure with `503`.

## 8. Data, reliability, and security requirements

### Durable data

PostgreSQL is the durable source of truth for classifications, ordered message
links, media states/results, assignment history, DigiSac directory data, and
persistent cycles. Alembic owns the schema; application startup must verify the
schema and must not create or mutate tables.

Redis is limited to queues, locks, temporary idempotency, and TTL-based
status/results.

Classifications use a unique UUIDv7 `public_id`. Classification identity and
message membership must be idempotent, including concurrent retries. Persistent
timestamps use `TIMESTAMPTZ`; structured lists and snapshots use JSONB where
defined by the schema.

### Reliability

- PostgreSQL persistence precedes Redis publication for durable media and cycle
  jobs.
- Failed publication clears the publication marker so reconciliation can retry.
- Leases and claims prevent concurrent workers from processing the same durable
  job.
- Retry scheduling must not republish work before `next_attempt_at`.
- A terminal image failure must prevent incomplete classification.
- A terminal audio failure must remain visible as a safe warning/marker.
- Targeted recovery must not remove unrelated dead-letter entries.

### Security and privacy boundaries

- HMAC validation is available for the production webhook route.
- `WEBHOOK_SECRET` must be configured in production to validate DigiSac
  webhooks and prevent external event injection. Query endpoints have no
  authentication or authorization layer for the single internal operator.
- Production logs and durable operational records must avoid raw request bodies,
  secrets, signed URLs, and binary media.
- There is no webhook diagnostic endpoint. Raw request bodies, headers, secrets,
  tokens, signed URLs, and binary media are not returned or logged by supported
  routes. Operators use structured logs and existing operational metrics.
- Persisted snapshots contain safe metadata and extracted text, not download
  credentials or media binaries.
- Data is retained indefinitely for historical analysis, FAQ-corpus use, and
  tracking conversation evolution. No automatic deletion, archival, cleanup
  job, retention schema change, or LGPD-driven automation is planned. Manual
  deletion, if ever needed, is performed case by case by direct PostgreSQL
  query.

## 9. Current implementation status

The source, migrations, configuration, Compose topology, checked-in tests, and
`scripts/verify.py` establish the implementation baseline. Issues `0001` and
`0002` completed tracked test isolation and the disposable PostgreSQL runner.
The observed local runner evidence on 2026-08-09 is:

- compileall: passed;
- strict Pyright: 0 errors, 0 warnings, 0 informations;
- offline pytest: 122 passed, 33 skipped (the skips are deliberately absent
  `CAI_TEST_DATABASE_URL` prerequisites in that stage);
- Alembic: `0014_retry_scheduling` applied and verified on the runner target; and
- PostgreSQL pytest: 33 passed, 122 deselected, with no prerequisite skips. The
  additional operational slice covers durable cycle publication recovery,
  due-only media recovery, queue deduplication, and dependent image wake-up.

The runner's offline stage does not select a finalization setting; it isolates
the disposable database credentials and injects the runner-owned URL only for
the PostgreSQL stage. This evidence is local and disposable, not verification
of Redis, DigiSac, Groq, replicas, deployment availability, or production
readiness. There is no hosted CI runner enforcing the matrix. The raw-payload
diagnostic surfaces were removed under Phase 1 item 5; no debug endpoint is part
of the supported HTTP surface.

## 10. Product decisions

The product owner has decided the following policies:

| Decision | Why it matters | Current status |
| --- | --- | --- |
| Retention, archival, deletion, and legal/privacy policy | Determines storage jobs, compliance behavior, and schema/lifecycle changes. | Decided — retain indefinitely; no automatic cleanup or archival; manual direct-PostgreSQL deletion only if needed. |
| API consumer authentication and authorization | Determines whether query endpoints can be exposed beyond trusted internal services. | Decided — single internal operator; no query authentication/authorization; require production `WEBHOOK_SECRET`; revisit for Acessórias. |
| Rate limits and external compatibility guarantees | Determines production API protection and versioning commitments. | No rate limiting. The current query routes are unversioned in source; `/v1/` and `/v2/` compatibility remains a future versioning policy and is not claimed as mounted behavior. Webhooks/operations remain unversioned. |
| SLA targets for webhook acceptance and classification completion | Determines capacity, alerting, and provider fallback design. | Decided — no SLA targets, alerting thresholds, or capacity commitments at this stage. |
| Long-term Redis-buffer posture | Determines whether to retain, migrate, or deprecate the single-worker legacy path. | Completed — the legacy mode, flag, Redis keys, code paths, and legacy test coverage were removed; persistent history remains. |
| Historical assignment interpretation | Determines which assignment events are business-significant and how they are presented. | Decided — preserve all observed transfers chronologically to track departments from open to close and support future Acessórias routing. |
| Canonical CI and release-verification matrix | Determines what evidence is required before release. | Decided — commit tests, use a local canonical runner, compileall, zero-diagnostic Pyright, offline tests, and isolated PostgreSQL 16 tests; external CI is optional later. |
| Business personas and success metrics | Determines product value measurement beyond technical processing success. | Decided — one internal operator; measure classification quality, history completeness, AI evolution/corpus growth, and future Acessórias integration value. |

## 11. Source traceability

The PRD is derived from:

- API and webhook behavior: `src/api/routes.py`,
  `src/api/webhook_adapter.py`, and `src/api/middleware.py`;
- context, filtering, media, and finalization: `src/core/models.py`,
  `src/core/message_filter.py`, `src/core/media.py`, and
  `src/core/finalization.py`;
- persistence and durable state: `src/core/db.py` and Alembic revisions
  `0001_initial` through `0014_durable_retry_scheduling`;
- worker behavior: `src/workers/ia_worker.py`, `src/workers/audio_worker.py`,
  and `src/workers/image_worker.py`;
- configuration and deployment: `src/core/config.py`, `.env.example`,
  `docker-compose.yml`, and `Dockerfile`;
- supporting implementation documentation: `README.md`, `ARCHITECTURE.md`,
  `IMPLEMENTATION_PLAN.md`, and `specs/`.

When this document conflicts with source code or migrations, the source and
migrations are authoritative for current behavior. A product-approved change
must update this PRD and the relevant implementation contracts before it is
treated as an implemented capability.
