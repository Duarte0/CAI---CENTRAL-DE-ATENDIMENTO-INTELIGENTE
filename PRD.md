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

CAI is an analysis and operational-support system. The implemented system does
not open DigiSac tickets, reply to customers, transfer tickets, or autonomously
decide the department or agent responsible for a ticket. The approved
Acessórias directory, identity, department mapping, and durable external
Request increments are implemented locally behind their own contracts.

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
third-party API consumers. Existing public query endpoints do not require
login, API keys, JWTs, or a separate authorization layer. The six internal
Acessórias identity-administration operations introduced by SPEC-0012 are a
separate authenticated administrative surface using `ADMIN_API_TOKEN`; they
are not public consumer endpoints. Issues 0042–0043 mount the local
authenticated UI shell/session boundary and the read-only triage queue, detail,
and company search; command actions remain in the follow-up issue 0044.

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
7. Integrate the approved DigiSac → CAI → Acessórias flow safely: maintain
   external directories, resolve identity conservatively, map departments, and
   later create auditable, idempotent external Requests.

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

The directory foundation does not create or update Requests, synchronize Users,
expose resolution/refresh endpoints or a UI, use fuzzy name matching, or change
the IA contract. The implemented Request increment creates only external
Requests of `tipo=E` from eligible durable facts. A unique phone match is a
candidate, not an automatic confirmation; DigiSac groups remain unresolved
unless an explicit confirmed link exists.

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

### 5.5 Approved Acessórias integration status

The approved product flow is DigiSac ticket/conversation and contact → CAI
classification → Acessórias company resolution → Acessórias department mapping
→ durable external Request → persisted CAI-to-Request link. The Acessórias
Directory Foundation (SPEC-0007; issue 0012) is implemented locally and
establishes only the directories required for this flow.

CAI now retains a minimal local DigiSac-contact representation keyed by
`contact.id`; `contact.data.number` is evidence only and
`contact.idFromService` is not an Acessórias matching key. Ticket webhooks are
the preferred incremental source when they include the complete contact;
message references schedule deduplicated individual hydration outside the
webhook request path. The Contacts API also supports the implemented internal
full backfill: it validates one-page or `page=N` responses, deduplicates by
opaque `contact.id`, and publishes only a complete snapshot.

Before any Acessórias Request operation, the terminal cycle retains only the
canonical ticket `data.contact.id` and the worker runs the durable sequence
`contact.id` → identity resolution → department-mapping snapshot → Request
operation → provider POST. A message sender `contactId`, group participant,
name, phone, or fallback metadata cannot replace the ticket contact. Missing,
unconfirmed, ambiguous, conflicting, or invalid preparation facts fail closed
without a provider POST.

The local Acessórias directory durably retains companies (active and inactive),
company contacts, departments, and current company-department relationships.
Raw and normalized phone/email values are both preserved.
PostgreSQL is the local durable authority; Redis is never the identity or
directory authority.

Issues 0038–0040 implement the complete six-operation SPEC-0012 surface under
`/admin/acessorias`: three bounded GET projections for identity-link triage, one
canonical contact, and present/active companies; idempotent confirmation and
rejection commands; and deterministic identity discovery. The routes require
Bearer `ADMIN_API_TOKEN`, use opaque scope-bound cursors where applicable, and
return only stable IDs, safe display names, states, availability, evidence
categories/counts/timestamps, and transition metadata. They do not expose
phone/email/evidence values or call providers, Redis, discovery from reads,
hydration, synchronization, or Request code. PostgreSQL is authoritative for
command idempotency: same-key retries converge, incompatible key reuse is a
conflict, and contact serialization prevents duplicate transitions under
concurrency. Administrative discovery does not call providers or Redis, create
Requests, or mutate historical cycle resolution. Product authorization is
granted for adoption and issue decomposition of SPEC-0013. Issue 0042
implements only its local shell, login/logout, signed session, and in-process
BFF boundary; the read and command UI increments remain unimplemented.

Identity distinguishes technical match evidence, persisted contact-company
links, audited link transitions, and the resolution used by a conversation/
cycle. Issue 0015 implements candidate, confirmed, ambiguous, unresolved, and
rejected semantics, including one DigiSac contact linked to multiple companies.
Exact normalized phone, an approved deterministic Brazilian mobile variant, and
exact normalized email may discover candidates; only an explicit/manual
confirmation initially produces `confirmed`. Group names and numbers are
diagnostic only, not automatic matching evidence.

Department routing is implemented under issues 0016 and 0020: it maps the
cycle-applicable DigiSac department through a PostgreSQL rule keyed by stable
external IDs and validates the selected Acessórias department against the
resolved company's current directory relationship. The assignment is selected
only within the cycle's persisted `cycle_started_at`/`ticket_closed_at` interval
with deterministic timestamp/ID ordering; an insufficient boundary remains
unresolved. `intent_type`, names, and fallback selection are not mapping inputs.
Current company departments are directory state, not a constraint that
invalidates historical external Requests; a later evaluation may record a new
outcome without rewriting a terminal snapshot.

Issues 0017–0019, 0021–0022 and 0026 implement the durable Request boundary. A terminal eligible cycle
with one confirmed company and a current valid mapped department creates at
most one PostgreSQL operation and one multipart `POST /requests`. Only a
non-empty provider `id` becomes the persisted `SolID`; only an explicitly proven
pre-send transport failure may retry, while ordinary connection, timeout, and
protocol failures require `manual_db` reconciliation. A `429` without a
documented proof of remote non-creation also requires `manual_db` reconciliation;
status and `Retry-After` alone do not authorize a second POST. Request adapter instances
in the same process share the configured Sliding Window by provider endpoint
and configuration before the POST; this transient coordination state contains
no token, header, payload, classification content, or PII. The operation stores no
raw title, description, payload, token, header, provider body, or PII and never
changes the originating classification. There is no public or admin HTTP
Request trigger; the administrative identity-triage GET routes do not invoke
Request operations. Request lifecycle operations remain outside this increment.
The persisted payload is loaded and validated before `post_started_at`; a
pre-provider load or validation failure is sanitized as retryable and retains no
false post-start evidence.
The internal recovery path for the historical `mapping_missing` state is
restricted to operations with no `post_started_at`, `SolID`, attempt evidence,
or reconciliation requirement; it reuses the same preparation sequence before
re-entering the normal Request claim/delivery path.


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
classifications. Parser recovery diagnostics may expose only a safe outcome or
category and bounded structural metadata; they must not log the raw or partial
model response, title, description, reasoning, or conversation content.

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
| `GET /admin/acessorias/identity-links` | Authenticated identity-link triage projection. |
| `GET /admin/acessorias/contacts/{digisac_contact_external_id}/identity` | Authenticated canonical-contact identity projection. |
| `GET /admin/acessorias/companies` | Authenticated present/active company search. |
| `POST /admin/acessorias/contacts/{digisac_contact_external_id}/identity-links/confirm` | Authenticated idempotent confirmation of one explicit pair. |
| `POST /admin/acessorias/contacts/{digisac_contact_external_id}/identity-links/{acessorias_company_external_id}/reject` | Authenticated idempotent rejection with audit history. |
| `POST /admin/acessorias/contacts/{digisac_contact_external_id}/identity-discovery` | Authenticated idempotent deterministic discovery over local facts. |

The mounted conversation and cycle query routes are currently unversioned.
`/v1/` and `/v2/` remain future compatibility policy only; they are not aliases
or implemented routes in this checkout. The first eight rows are the original
operational HTTP surface; the six `/admin/acessorias` rows are internal and
Bearer-protected, not public API operations.

Normal webhook processing returns `202`. Safely ignored events return `200` with
an ignore reason. Missing conversations, cycles, or unavailable results return
`404`; malformed payloads return `400`; invalid webhook signatures return `401`;
and unavailable dependencies cause health failure with `503`.

## 8. Data, reliability, and security requirements

### Durable data

PostgreSQL is the durable source of truth for classifications, ordered message
links, media states/results, assignment history, DigiSac directory data,
persistent cycles, identity/mapping snapshots, and Acessórias Request
operations/reconciliation. All are Alembic-owned durable data. Application
startup must verify the schema and must not create or mutate tables.

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
  extracted webhook message/contact values, raw or partial model responses,
  classification content, secrets, signed URLs, and binary media.
- There is no webhook diagnostic endpoint. Raw request bodies, headers, secrets,
  tokens, signed URLs, and binary media are not returned or logged by supported
  routes. Normal extraction logs retain only safe event, presence/type, and
  source metadata. Operators use structured logs and existing operational
  metrics.
- Persisted snapshots contain safe metadata and extracted text, not download
  credentials or media binaries.
- The implemented Acessórias provider adapters centralize bearer authentication,
  timeout/retry handling, parsing, sanitized logs, and test doubles; the
  provider-neutral `src/core/provider_coordination.py` boundary owns transient
  rate admission and shared in-process Request coordination;
  Authorization headers and real provider tokens must never be persisted or
  logged. No provider credential or production synchronization was used for
  the local implementation evidence.
- Data is retained indefinitely for historical analysis, FAQ-corpus use, and
  tracking conversation evolution. No automatic deletion, archival, cleanup
  job, retention schema change, or LGPD-driven automation is planned. Manual
  deletion, if ever needed, is performed case by case by direct PostgreSQL
  query.

## 9. Current implementation status

The source, migrations, configuration, Compose topology, checked-in tests, and
`scripts/verify.py` establish the implementation baseline. Issues `0001` and
`0002` completed tracked test isolation and the disposable PostgreSQL runner;
issues `0012`–`0022`, `0026`, and `0038`–`0040` added the Acessórias directory, DigiSac contact identity
foundation, complete Contacts backfill, conservative cross-system identity
resolution, stable-ID department mapping, and conservative Request transport
classification. The observed local runner evidence on 2026-08-21 is:

- compileall: passed;
- strict Pyright: 0 errors, 0 warnings, 0 informations;
- offline pytest: 238 passed, 76 skipped (the skips are deliberately absent
  `CAI_TEST_DATABASE_URL` prerequisites in that stage);
- Alembic: `0022_identity_discovery_command` applied and verified on the runner target;
  and
- PostgreSQL pytest: 76 passed, 238 deselected, with no prerequisite skips. The
  additional operational slice covers durable cycle publication recovery,
  due-only media recovery, queue deduplication, dependent image wake-up, and
  stable-ID department mapping with cycle-scoped audited snapshots, plus durable
  Request operation claims, retry classification, reconciliation, and
  concurrency-safe shared rate admission across Request adapter instances,
  canonical ticket-contact provenance, preparation ordering, blocked gates, and
  pre-POST `mapping_missing` recovery.

The runner's offline stage does not select a finalization setting; it isolates
the disposable database credentials and injects the runner-owned URL only for
the PostgreSQL stage. This evidence is local and disposable, not verification
of Redis, DigiSac, Groq, replicas, deployment availability, or production
readiness. There is no hosted CI runner enforcing the matrix. The raw-payload
diagnostic surfaces were removed under issue `0006`; no debug endpoint is part
of the supported HTTP surface. Issue `0024` removes raw Groq classification
response logging from parser diagnostics, and issue `0025` removes raw values
from normal webhook extraction logs; local tests verify sanitized metadata
without making a provider-backed quality or production logging claim.

## 10. Product decisions

The product owner has decided the following policies:

| Decision | Why it matters | Current status |
| --- | --- | --- |
| Retention, archival, deletion, and legal/privacy policy | Determines storage jobs, compliance behavior, and schema/lifecycle changes. | Decided — retain indefinitely; no automatic cleanup or archival; manual direct-PostgreSQL deletion only if needed. |
| API consumer authentication and authorization | Determines whether query endpoints can be exposed beyond trusted internal services. | Decided — existing public queries remain unauthenticated for the single internal operator; SPEC-0012 administrative identity routes require `ADMIN_API_TOKEN`; production secret/network provisioning remains operational. |
| Rate limits and external compatibility guarantees | Determines production API protection and versioning commitments. | No rate limiting. The current query routes are unversioned in source; `/v1/` and `/v2/` compatibility remains a future versioning policy and is not claimed as mounted behavior. Webhooks/operations remain unversioned. |
| SLA targets for webhook acceptance and classification completion | Determines capacity, alerting, and provider fallback design. | Decided — no SLA targets, alerting thresholds, or capacity commitments at this stage. |
| Long-term Redis-buffer posture | Determines whether to retain, migrate, or deprecate the single-worker legacy path. | Completed — the legacy mode, flag, Redis keys, code paths, and legacy test coverage were removed; persistent history remains. |
| Historical assignment interpretation | Determines which assignment events are business-significant and how they are presented. | Decided — preserve all observed transfers chronologically to track departments from open to close; Acessórias mapping uses the assignment applicable to each persisted cycle interval. |
| Canonical CI and release-verification matrix | Determines what evidence is required before release. | Decided — commit tests, use a local canonical runner, compileall, zero-diagnostic Pyright, offline tests, and isolated PostgreSQL 16 tests; external CI is optional later. |
| Business personas and success metrics | Determines product value measurement beyond technical processing success. | Decided — one internal operator; measure classification quality, history completeness, AI evolution/corpus growth, and approved Acessórias integration value when delivered. |
| Acessórias directory and identity foundation | Determines how CAI discovers companies before any external action. | Directory, contact identity, and conservative cross-system resolution are implemented locally under SPEC-0007–0009 and issues 0012–0015; confirmation remains explicit/manual with many-to-many links. |
| Acessórias department and Request flow | Determines safe routing and external side effects. | Department mapping is implemented under SPEC-0010/issues 0016, 0020 and 0026 with cycle-scoped assignment selection; Request creation is implemented under SPEC-0011/issues 0017–0019, 0021–0022 and 0026 with canonical preparation, durable one-cycle uniqueness, pre-POST payload safety, shared in-process rate admission, explicit-proof-only retry, conservative `429` reconciliation, and manual reconciliation. |

SPEC-0013 product authorization is also decided. The approved contract for its
future issue decomposition is:

- adoption of the administrative identity-link review UI is authorized;
- the stack is HTML served by FastAPI, local CSS, modular JavaScript without a
  required bundler, and `fetch` against SPEC-0012 routes; Jinja2 is optional
  only when server rendering is needed, and React/Vite are not used at this
  stage;
- the UI uses an in-process BFF in the same FastAPI process, with a signed
  `HttpOnly` cookie session, `Secure` in production, `SameSite=Strict`, and a
  fixed 60-minute expiration without a sliding window; signing may use
  Starlette `SessionMiddleware` or `itsdangerous`;
- CSRF protection for this version is `SameSite=Strict`; no additional CSRF
  token is required;
- `ADMIN_API_TOKEN`, `ADMIN_UI_PASSWORD`, and `ADMIN_SESSION_SECRET` must all
  be securely provisioned with the same level of care and never exposed to the
  browser, logs, metrics, or cache;
- session bootstrap is a single operator-password login compared securely
  with `ADMIN_UI_PASSWORD`, with no user registration, RBAC, or IdP at this
  stage.

## 11. Source traceability

The PRD is derived from:

- API and webhook behavior: `src/api/routes.py`,
  `src/api/webhook_adapter.py`, and `src/api/middleware.py`;
- context, filtering, media, and finalization: `src/core/models.py`,
  `src/core/message_filter.py`, `src/core/media.py`, and
  `src/core/finalization.py`;
- persistence and durable state: `src/core/db.py` and Alembic revisions
  `0001_initial` through `0022_identity_discovery_command`;
- authenticated identity administration: `src/api/admin_routes.py`,
  `src/core/identity_admin.py`, `src/core/identity_resolution.py`, Alembic
  revisions `0021_identity_admin_commands` and `0022_identity_discovery_command`,
  and SPEC-0012;
- Acessórias provider adapters and transient coordination:
  `src/core/acessorias_directory.py`, `src/core/acessorias_requests.py`, and
  `src/core/provider_coordination.py`;
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
