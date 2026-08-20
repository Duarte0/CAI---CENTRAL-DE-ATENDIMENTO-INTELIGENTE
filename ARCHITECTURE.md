# CAI System Architecture

**Status:** implementation-derived architecture baseline  
**Reference date:** 2026-08-14
**Audience:** engineering and operations

This document describes the architecture implemented in this repository. Source
code, Alembic migrations, and tests are authoritative. \`README.md\`,
\`IMPLEMENTATION_PLAN.md\`, and the specifications provide supporting context;
approved but not-yet-implemented behavior is marked as such.

## 1. System purpose and boundaries

CAI receives DigiSac webhook events, reconstructs ticket conversations,
enriches audio and image messages with Groq, classifies the customer's intent,
and exposes the result through an HTTP API.

The implemented CAI does not open tickets, reply to customers, transfer tickets,
or make department/agent decisions through the language model. Its current
runtime includes the internal Acessórias directory, DigiSac contact identity,
cross-system identity resolution, department mapping, and durable Acessórias
Request creation capabilities. Request lifecycle operations remain a future
increment.

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
| \`ia_worker\` | Consumes persistent cycle jobs, retrieves history, builds context, calls Groq, and persists classifications. | PostgreSQL claims, leases, snapshots, and classifications; Redis queues/results. |
| \`audio_worker\` | Downloads audio from DigiSac, calls Groq transcription, and persists status/result. | PostgreSQL reservation/status; Redis queue and dead letter. |
| \`image_worker\` | Downloads image data, calls Groq vision, and persists extracted text/status. | PostgreSQL reservation/status; Redis queue and dead letter. |
| \`digisac_directory\` | Periodically synchronizes departments and users for assignment-name resolution. | PostgreSQL directory cache and sync state. |
| \`acessorias_directory\` | Acquires and reconciles the Acessórias company, contact, department, and relationship directory. | PostgreSQL snapshot and synchronization executions; provider access is configuration-backed. |
| \`postgres\` | Source of truth for analysis, media state, assignment history, DigiSac/Acessórias directory data, and persistent cycles. | PostgreSQL 16, managed by Alembic. |
| \`redis\` | Lightweight work transport and transient coordination. | Redis 7 with AOF in Compose. |
| \`migrate\` | Applies the configured Alembic revision before application services start. | No application schema creation at runtime. |

The application uses one PostgreSQL connection pool per process. Database
initialization verifies the Alembic schema and does not create or mutate
tables. Redis is not the authoritative store for durable classifications,
media results, or department mapping state. The pool lifecycle and schema
verification remain in `src/core/db.py`; DigiSac contact persistence and
hydration use the same pool through the focused internal
`src/core/digisac_contact_repository.py` boundary. Conversation-cycle
persistence and recovery use the same pool through
`src/core/conversation_cycle_repository.py`. Ticket-assignment history and
assignment-name projection use the same pool through
`src/core/ticket_assignment_repository.py`. `src/core/db.py` retains
compatibility exports for all focused repositories. Shared durable media
reservation, state transitions, reads, recovery claims, publication release,
and pending projections use the same pool through
`src/core/durable_media_repository.py`, with audio/image compatibility exports
retained by the facade. Classification insertion, ordered message association,
protocol metadata, and existence queries use the same pool through
`src/core/classification_repository.py`, with compatibility exports retained by
the facade. DigiSac directory cache persistence, sync state, and user-name
lookup use the same pool through `src/core/digisac_directory_repository.py`,
with compatibility exports retained by the facade. The provider-facing
`src/core/digisac_directory.py` retains authentication, pagination, transient
retry, synchronization locking, and periodic orchestration.

## 2.1 Acessórias architecture and delivery boundary

The implemented Acessórias Directory Foundation (SPEC-0007; issue 0012),
DigiSac Contact Identity Foundation (SPEC-0008; issues 0013–0014 and 0026),
DigiSac–Acessórias identity resolution (SPEC-0009; issues 0015 and 0026), and department
mapping (SPEC-0010; issues 0016, 0020, and 0026) add provider boundaries and durable local
records. Contact snapshots from ticket webhooks are upserted by opaque
`contact.id`; message-only references create a durable, deduplicated need for
individual hydration outside the request path. The full Contacts backfill uses
validated one-page or `page=N` responses. Request delivery is a separate
durable operation after the mapping boundary.

\`\`\`mermaid
flowchart LR
    D[DigiSac ticket webhooks] --> DC[Local DigiSac contact identity]
    DCA[DigiSac Contacts API] -->|individual hydration; future backfill| DC
    AA[Acessórias API] --> AD[Acessórias directory sync]
    AD --> P[(PostgreSQL)]
    DC --> P
    P --> IR[Identity resolver]
    IR --> DM[Department mapping]
    DM --> FR[Durable Request operation]
    FC[Persisted CAI classification/cycle] --> IR
    FC --> FR
\`\`\`

\`contact.id\` is the canonical DigiSac-contact external identity.
Webhook ticket events incrementally upsert complete contact objects; messages
that only carry \`contactId\` do not trigger a Contacts API call unless hydration
is needed. The local Acessórias directory contains companies, company contacts,
departments, and current company-department relationships, including inactive
companies. PostgreSQL is its durable local authority; Redis is not a directory
or identity source of truth.

The identity resolver retains separate match evidence, contact-company links,
link transitions, and conversation/cycle resolution. It models candidate,
confirmed, ambiguous, unresolved, and rejected outcomes and permits a contact
to link to multiple companies. Exact phone/email evidence and the approved
Brazilian mobile variant create candidates but not automatic confirmation;
DigiSac groups remain unresolved absent an explicit confirmed link. Evidence
uses sanitized fingerprints, while raw and normalized directory identifiers
remain in their canonical foundation records.

Department mapping uses the cycle-applicable DigiSac department from assignment
history, selected only within the persisted `cycle_started_at` through
`ticket_closed_at` interval and ordered by timestamp/ID, with an explicit
unresolved result when the boundary is insufficient. It then uses an explicitly
administered PostgreSQL rule keyed by stable provider IDs and the confirmed
company's current Acessórias relationship. It persists an append-only cycle
evaluation and does not use IA \`intent_type\`, names, or fallback selection. The
Request operation runs only after a persisted valid
classification, confirmed identity, and mapping; issues 0017–0019, 0021–0022,
0026, and structural boundary issues 0034 and 0036 give it durable one-cycle
uniqueness, claim/reconciliation, failure state, and shared in-process
Sliding Window admission by provider endpoint/configuration. The adapter retries only
when a transport boundary explicitly proves that the POST did not start; the
persisted payload is loaded and validated before `post_started_at`, and a
pre-provider payload failure remains retryable without a false marker;
ordinary connection, timeout, and protocol failures remain uncertain and cannot
roll back a completed classification or trigger a second POST. The provider
payload/outcome contract and HTTP adapter are owned by
`src/core/acessorias_request_provider.py`; `src/core/acessorias_requests.py`
retains only durable Request operation orchestration and compatibility exports.

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
    else text, document, or ticket lifecycle
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
  unsupported message types are ignored without queueing or cycle work.
- Normalized file data keeps safe metadata such as ID, name, public name,
  extension, and MIME type. Download URLs, tokens, and binary data do not enter
  context snapshots, queue payloads, or classification records.
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

Persistent jobs use PostgreSQL publication markers and leases so a failure
between database persistence and Redis publication can be recovered. The worker
does not require a second Redis processing queue: a popped job is recovered from
its durable cycle marker by reconciliation after a process failure.

### Transient keys

Important key families include:

- \`processed:*\` for temporary webhook idempotency;
- TTL-based \`ia_status:*\` and \`ia_result:*\` compatibility views.

## 5. Finalization modes

Persistent DigiSac-history finalization is the only supported mode. Ticket
open/reopen events create durable cycles, and closure persists a cycle before
publishing its job.

### 5.1 Persistent DigiSac-history mode

Each ticket opening/reopening creates or reuses a persistent
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
12. resolves the canonical ticket contact identity and persists the cycle outcome;
13. evaluates and persists the cycle-scoped department-mapping snapshot;
14. claims or creates the durable Acessórias Request operation only when both
    preparation stages are ready.

Persistent cycle publication is guarded by \`enqueued_at\`. Reconcilers only
republish due work and recover jobs whose publication/lease has expired.

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
failures honor \`Retry-After\` and durable backoff, remaining \`pending\` beyond
the classification worker's attempt limit. Permanent audio failures go to the
dead-letter and only persisted transient failures are recovered automatically.
Queue and dead-letter entries are deduplicated by message ID; recovery retains
one safety copy until a non-empty transcription is persisted. Failure logs and
retry metadata use sanitized categories rather than provider bodies or signed
URLs.
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
\`ia_worker.py\`. The model-facing classification contract is isolated in
\`src/core/ia_classification.py\`; it contains only prompt construction,
response recovery, validation, normalization, and sanitized parser diagnostics.
The worker remains the provider invocation and lifecycle orchestration boundary.

- Client and attendant messages remain in chronological order.
- Attendant messages provide context but are not the classification target.
- Bot messages are excluded.
- Quoted replies use bounded excerpts.
- URLs, signed download links, tokens, and binary media are excluded from
  persisted snapshots.
- Groq parser diagnostics expose only a safe outcome/category and bounded
  structural metadata; raw or partial model responses and classification fields
  are not logged.
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

Alembic owns the schema through \`0020_cycle_contact_provenance\`. The main data groups
are:

| Data group | Tables | Purpose |
| --- | --- | --- |
| Analysis | \`ia_classifications\`, \`classification_messages\` | Classification identity, result, full context, and ordered supporting messages. |
| Media | \`message_transcriptions\`, \`message_image_extractions\` | Durable reservation, attempts, provider model, status, retry schedule, error, and extracted text. |
| Assignment | \`ticket_assignment_history\`, \`ticket_assignment_event_keys\` | Chronological, idempotent ticket assignment history. |
| DigiSac directory | \`digisac_departments\`, \`digisac_users\`, \`digisac_directory_sync_state\` | Local lookup cache and synchronization state. |
| DigiSac contact identity | \`digisac_contacts\`, \`digisac_contact_hydrations\` | Opaque contact identity, approved provider metadata including normalized email, timestamp-aware observation, and durable individual hydration claims/retries. |
| Acessórias directory | \`acessorias_companies\`, \`acessorias_company_contacts\`, \`acessorias_departments\`, \`acessorias_company_departments\`, \`acessorias_directory_sync_executions\` | Durable provider snapshot, presence/activity state, relationships, and sanitized refresh outcomes. |
| Identity resolution | \`identity_match_evidence\`, \`identity_company_links\`, \`identity_company_link_transitions\`, \`conversation_cycle_identity_resolutions\` | Sanitized match evidence, many-to-many candidate/confirmed links, audit transitions, and immutable per-cycle outcomes. |
| Department mapping | \`department_mapping_rules\`, \`department_mapping_transitions\`, \`conversation_cycle_department_mappings\` | Stable-ID global rules, auditable lifecycle transitions, and append-only per-cycle validation snapshots. |
| Cycles | \`conversation_processing_cycles\`, \`conversation_cycle_messages\` | Persistent finalization state, sequence, lease, snapshot, scheduling, status, ordered membership, canonical ticket-contact provenance, and identity-resolution linkage. |
| Acessórias Request | \`acessorias_request_operations\`, \`acessorias_request_reconciliations\` | One durable external Request operation per cycle, safe payload metadata/fingerprint, claims, `SolID`, preparation-recovery audit, sanitized outcomes, and controlled `manual_db` reconciliation. |

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
| \`GET /health\` | PostgreSQL and Redis readiness. |
| \`GET /queues\` | Redis queue and dead-letter metrics. |
| \`GET /conversations/{conversation_id}/status\` | Conversation processing status. |
| \`GET /conversations/{conversation_id}/result\` | Latest conversation result. |
| \`GET /conversations/{conversation_id}/cycles\` | Persistent cycle list. |
| \`GET /cycles/{cycle_id}/status\` | Persistent cycle status. |
| \`GET /cycles/{cycle_id}/result\` | Persistent cycle result. |

The webhook normally returns \`202\`; safely ignored events return \`200\` with an
ignore reason. Missing persisted entities return \`404\`. There is no webhook
diagnostic route. Logs and normal operational responses expose IDs and status
without raw bodies, model responses, classification content, headers, secrets,
tokens, signed URLs, binary media, or extracted webhook values. Normal webhook
extraction retains only safe event, presence/type, and source metadata. Parser
recovery retains only bounded structural metadata and a safe outcome/category.
The query routes above are currently unversioned in the mounted FastAPI app;
future `/v1/` and `/v2/` compatibility are policy/reference targets, not
implemented aliases or prefixes in this checkout.

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

The persistent-cycle schema must be migrated before API and worker rollout. The
application verifies that schema at startup and does not create or mutate it.

## 12. Reliability and recovery invariants

- PostgreSQL persistence precedes media/cycle Redis publication.
- A failed Redis publication clears the corresponding PostgreSQL publication
  marker so reconciliation can retry it.
- Persistent claims and leases recover work abandoned by process failure.
- Media reservations are idempotent by message ID.
- DigiSac contact identity is keyed only by provider \`contact.id\`; hydration
  claims and retry state are durable in PostgreSQL and deduplicated under row
  locks.
- Identity resolution stores evidence, links, transitions, and cycle outcomes
  in PostgreSQL; exact matching never confirms automatically, and a manual
  confirmation is serialized per contact.
- Cycle and classification identity prevent duplicate terminal analysis.
- Retry scheduling respects provider timing and does not broadly republish
  future work.
- Terminal image failures block classification; terminal audio failures preserve
  a warning marker.
- Request adapters for the same provider endpoint/configuration share a
  concurrency-safe in-process Sliding Window before each POST; its transient
  state contains no credentials, headers, payload, classification content, or
  PII.
- A terminal cycle must pass canonical-contact identity and cycle-scoped mapping
  preparation before a Request operation can reach the provider boundary.
- A Request operation is persisted before every provider POST; only a non-empty
  provider `id` confirms completion, and uncertain transport outcomes cannot
  auto-post a second Request. Only an explicit pre-send boundary marker may
  enter the bounded retry path; an unproven `429` is reconciliation-required
  even when `Retry-After` is present.
- Unrelated dead-letter entries are not removed during targeted recovery.
- No retention, archival, or deletion policy is currently implemented.

## 13. Current gaps and delivery follow-up

The architecture is implemented, but the repository's implementation plan
records these delivery limitations:

- The local canonical runner proves the tracked static, offline, migration, and
  PostgreSQL baseline on a disposable target. Its observed 2026-08-17 evidence
  was **203 passed, 68 skipped** offline and **68 passed, 203 deselected** in
  PostgreSQL; static/local evidence does not prove Redis, DigiSac, Groq,
  replica, deployment, or production availability or release readiness.
- The runner's offline stage does not select a finalization setting and removes
  the test database prerequisite before execution; only the PostgreSQL stage
  receives the runner-owned disposable database URL.
- Issue `0004` has focused disposable-runner coverage for durable cycle
  publication recovery, due-only media recovery, queue deduplication, and
  dependent image wake-up; no hosted CI runner currently enforces the matrix.
- Data retention is indefinite; no automatic deletion or archival is planned.
- Historical assignment events are preserved chronologically to track department
  routing from ticket open to close and support future Acessórias integration.
- Persistent DigiSac-history finalization is the only supported mode; the former
  Redis buffer, debounce, feature flag, and legacy worker branch were removed.
- The raw-payload diagnostic surfaces were removed under issue `0006`; no
  replacement debug route or raw-payload contract exists.
- Issue `0025` removes extracted webhook values from normal logs while
  preserving safe event, presence/type, and source metadata.
- DigiSac Contacts full backfill is implemented under issue `0014` as an
  internal, typed acquisition and transactional PostgreSQL publication. It
  supports a validated one-page response and `page=N` fallback with global
  `contact.id` deduplication; it does not add an HTTP route or Redis directory
  authority.
- DigiSac–Acessórias identity resolution is implemented under issue `0015` as
  an internal PostgreSQL capability. It preserves sanitized evidence, audited
  many-to-many links, immutable cycle outcomes, and controlled `manual_db`
  confirmation; it does not add an HTTP route, fuzzy matching, or provider
  writes.
- DigiSac–Acessórias department mapping is implemented under issues `0016` and
  `0020` as an internal PostgreSQL capability. It preserves stable-ID rule
  versions and transitions, validates the current company-department relation
  only after a confirmed cycle identity, selects assignment history within the
  cycle's persisted boundaries, and appends sanitized cycle snapshots; it does
  not add an HTTP route, IA routing, or fallback selection. Request creation is
  implemented separately under issue `0017`.

- Durable Acessórias Request creation and preparation are implemented under issues
  `0017`–`0019`, `0021`–`0022`, `0026`, and structural boundary issues `0034` and
  `0036`.
  They resolve the canonical ticket
  contact and cycle-scoped mapping before any provider call, and recover
  `mapping_missing` only with durable proof that no POST started. The existing
  operation then
  validates terminal-cycle, confirmed-identity, and current mapping facts,
  admits provider calls through the shared in-process rate limiter, sends only
  the approved multipart fields, persists `SolID` after a successful local
  commit, leaves uncertain provider outcomes (including unproven `429` responses)
  for manual reconciliation, and keeps
  payload-load failures before `post_started_at` retryable without provider calls.

These items are not architectural failures of the current implementation, but
they limit release verification and future evolution decisions.

## 14. Source map

- API and webhook lifecycle: \`src/api/routes.py\`, \`src/api/webhook_adapter.py\`,
  \`src/api/middleware.py\`.
- Shared message and context rules: \`src/core/models.py\`,
  \`src/core/message_filter.py\`, \`src/core/media.py\`,
  \`src/core/finalization.py\`.
- PostgreSQL lifecycle, schema verification, and shared persistence primitives:
  \`src/core/db.py\`; it remains the single pool/lifecycle and schema-capability
  owner for the focused persistence boundaries below.
- DigiSac contact persistence and hydration: \`src/core/digisac_contact_repository.py\`,
  with compatibility exports retained by \`src/core/db.py\`.
- Conversation-cycle persistence, membership, metrics, result projection, and
  selective media unblocking: \`src/core/conversation_cycle_repository.py\`,
  with compatibility exports retained by \`src/core/db.py\`.
- Ticket-assignment history and assignment-name projection:
  \`src/core/ticket_assignment_repository.py\`, with compatibility exports
  retained by \`src/core/db.py\`.
- Durable transcription and image-extraction persistence:
  \`src/core/durable_media_repository.py\`, with compatibility exports retained
  by \`src/core/db.py\`.
- Classification persistence, ordered message association, protocol metadata,
  and existence queries: \`src/core/classification_repository.py\`, with
  compatibility exports retained by \`src/core/db.py\`.
- IA model-facing classification contract: \`src/core/ia_classification.py\`;
  provider invocation, retry/cooldown behavior, cycle orchestration, and
  result enrichment remain in \`src/workers/ia_worker.py\`.
- DigiSac directory cache persistence, sync state, and user-name lookup:
  \`src/core/digisac_directory_repository.py\`, with compatibility exports
  retained by \`src/core/db.py\`; provider synchronization remains in
  \`src/core/digisac_directory.py\`.
- PostgreSQL access and department mapping: \`src/core/department_mapping.py\`, and
  \`alembic/versions/0001_initial.py\` through
  \`0019_acessorias_request_creation.py\` through
  \`0020_cycle_contact_provenance.py\`.
- DigiSac contact acquisition and backfill: \`src/core/digisac_client.py\`,
  \`src/core/digisac_contact_backfill.py\`, and
  \`src/utils/backfill_digisac_contacts.py\`.
- DigiSac–Acessórias identity resolution: \`src/core/identity_resolution.py\`
  and Alembic \`0017_digisac_acessorias_identity.py\`.
- DigiSac–Acessórias department mapping: \`src/core/department_mapping.py\`
  and Alembic \`0018_department_mapping.py\`.
- Durable Acessórias Request creation: \`src/core/acessorias_requests.py\`,
  \`src/core/acessorias_preparation.py\`, \`src/workers/ia_worker.py\`, and
  Alembic \`0019_acessorias_request_creation.py\` plus
  \`0020_cycle_contact_provenance.py\`.
- Acessórias provider adapters and transient coordination:
  \`src/core/acessorias_directory.py\`,
  \`src/core/acessorias_request_provider.py\`, and
  \`src/core/provider_coordination.py\`.
- Worker behavior: \`src/workers/ia_worker.py\`,
  \`src/workers/audio_worker.py\`, and \`src/workers/image_worker.py\`.
- Configuration and deployment: \`src/core/config.py\`, \`.env.example\`,
  \`docker-compose.yml\`, and \`Dockerfile\`.
- Baseline contracts and known gaps: \`IMPLEMENTATION_PLAN.md\` and \`specs/\`.
