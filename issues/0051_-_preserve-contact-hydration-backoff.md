---
id: 0051
title: "Preserve contact hydration backoff on repeated message references"
type: bug
status: open
priority: medium
phase: 5
created_at: 2026-09-02
updated_at: 2026-09-02
closed_at: ~
related_issues: ["0013", "0048"]
blocked_by: []
affects:
  - src/api/routes.py
  - src/core/digisac_contact_repository.py
  - src/core/digisac_contact_hydration.py
  - tests/test_digisac_contact_identity.py
  - tests/test_webhook.py
  - README.md
  - ARCHITECTURE.md
---

## Description

Contact hydration is already a PostgreSQL-backed worker and is not a Redis queue problem. It has a claim/lease loop and deduplicates concurrent work for the same DigiSac contact. However, `request_digisac_contact_hydration()` can reset a failed row to `pending` with `next_attempt_at = NULL` whenever a new message references the same contact.

The webhook can receive repeated messages or redeliveries while a provider failure is still inside its backoff window. Re-requesting hydration in that period can therefore make the contact due immediately, bypass the persisted retry schedule and call DigiSac earlier than intended. This is a separate defect from physical Redis queue duplication and must be fixed without introducing another queue.

### Confirmed current behavior

- The webhook requests hydration for message senders before/around event idempotency handling.
- The repository already serializes the contact row and returns no new request while it is pending or running.
- When the row is failed, a new request can transition it to pending and clear `next_attempt_at`, even if the previous failure scheduled a future retry.
- The hydration loop claims due rows with `FOR UPDATE SKIP LOCKED`, a lease and an attempt count.
- Existing tests cover concurrent claim/deduplication, but do not prove that a repeated message preserves a future failed-row backoff.

**Root cause:** the normal message-triggered request path treats a failed hydration row as immediately requestable and clears `next_attempt_at`, even when the failure explicitly scheduled a future retry.

**Reproduction:**

1. Persist a failed contact-hydration row with a future `next_attempt_at`.
2. Deliver a new message or duplicate webhook event for the same sender.
3. Call `request_digisac_contact_hydration()` and inspect the row before the scheduled time.

**Actual behaviour:** the request can reset the row to pending with no scheduled time, allowing the provider call before the backoff expires.

**Expected behaviour:** a normal repeated message is suppressed while the retry is in the future. Only the scheduled poller or an explicit audited operator retry can make it immediately eligible.

### Goals and invariants

- A repeated reference to a contact must not move a future retry earlier.
- Only an explicit, auditable operator retry or a naturally due scheduled retry may clear the backoff.
- Pending, running and future-failed rows remain single-flight per contact.
- Lease expiry remains the recovery mechanism for a crashed hydration worker.
- A successful or already-current hydration remains a no-op until the configured refresh policy says otherwise.
- This issue must not add Redis state or broaden the unrelated queue migration.

### Scope

### Included

- Define and implement the request decision table for pending, running, failed-with-future-backoff, failed-due, succeeded/current and explicitly forced requests.
- Change the failed-row request path so a normal message reference preserves `next_attempt_at` when it is in the future.
- If the product requires immediate retry, expose it as a separate explicit operation with authorization/audit metadata rather than overloading the webhook request.
- Keep the database row lock and claim lease as the concurrency boundary.
- Add metrics/logging for suppressed duplicate requests, preserved backoff and explicit operator-forced retries.
- Update identity/hydration documentation and runbooks.

### Explicitly out of scope

- Migrating any Redis queue; hydration already does not depend on Redis.
- Changing DigiSac contact fields, identity-link policy or provider API behavior.
- Changing directory synchronization or Acessórias mapping.
- Replaying every failed contact automatically.

## Implementation Plan

1. Write the state-transition table and decide whether a failed row that is already due can be re-requested immediately or should be left for the hydration poller.
2. Make `request_digisac_contact_hydration()` distinguish a normal message-triggered request from an explicit force/retry request. A normal request must not clear a future `next_attempt_at`.
3. Preserve row-level locking and make the returned “requested/not requested/suppressed” result observable to callers without exposing provider details.
4. Verify that claim/release/failure transitions do not accidentally clear or shorten backoff when duplicate webhook events arrive.
5. Add an operator-only recovery path if immediate retry is required, with reason, actor, timestamp and previous schedule recorded in the audit trail.
6. Update the webhook and identity documentation to explain that event deduplication and hydration backoff are separate protections.

## Tests

### Repository state-transition tests

- A new contact with no row creates one pending hydration request.
- A pending row is not reset or duplicated by a second request.
- A running row with an unexpired lease is not reset or duplicated.
- A failed row with `next_attempt_at` in the future remains failed or remains scheduled according to the chosen state contract; its timestamp is unchanged by a normal repeated message reference.
- A failed row whose `next_attempt_at` is due becomes eligible exactly once and does not create a second row.
- A succeeded/current row is not requested again before the refresh policy.
- An explicitly forced retry can bypass backoff only with the required authorization and audit metadata.
- Concurrent requests for the same `contact_id` result in one state transition and one eventual provider claim.
- A lease-expired running row is recoverable by the hydration loop and duplicate requests do not create another claim.

### Webhook and integration tests

- Two identical webhook deliveries during a failed-row backoff do not cause an early provider call.
- Many messages from the same sender during the backoff window result in one durable row and preserve the original `next_attempt_at`.
- Event idempotency and hydration deduplication both hold when requests arrive in either ordering supported by the webhook contract.
- A database outage fails closed without reporting a hydration request as successfully queued.
- The hydration worker processes the contact when the persisted due time arrives and records success/failure without Redis.

### Regression and observability tests

- Existing contact identity and concurrent-claim tests remain green.
- Metrics distinguish a new request, duplicate suppression, backoff preservation and explicit force.
- Logs include contact/request identifiers and decision reason without logging sensitive contact payloads.
- No Redis key or list is created by contact hydration tests.

## Acceptance Criteria

- [ ] Repeated message references cannot bypass a future contact-hydration backoff.
- [ ] Pending/running/failed-due/succeeded/current/forced decisions are documented and tested.
- [ ] Concurrent requests still produce one durable row and one active lease per contact.
- [ ] An immediate retry, if retained, is explicit, authorized and auditable.
- [ ] Existing identity, webhook and hydration behavior remains compatible except for the corrected backoff protection.
- [ ] No Redis queue is introduced for hydration.

## References

- `src/api/routes.py`: webhook/lifecycle startup and sender hydration request path.
- `src/core/digisac_contact_repository.py`: request state transition and claim/lease implementation.
- `src/core/digisac_contact_hydration.py`: database-only hydration worker loop.
- `tests/test_digisac_contact_identity.py`: current concurrent claim and deduplication tests.
- `issues/0013_-_implement-digisac-contact-identity-foundation.md`: existing identity/hydration context.
- `issues/0048_-_prevent-duplicate-ia-cycle-queue-republication.md`: coordinated queue migration; this issue remains separate because hydration is already DB-only.

## Resolution

<!-- Complete with the state-transition decision, migration/test evidence and operator recovery audit example. -->
