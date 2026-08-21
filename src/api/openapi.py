"""Generated OpenAPI composition for the public HTTP contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


JsonSchema = dict[str, Any]

PERSISTED_STATUSES = [
    "open",
    "pending",
    "recovering_messages",
    "waiting_media",
    "media_blocked",
    "building_context",
    "summarizing",
    "classifying",
    "completed",
    "completed_with_warnings",
    "retryable_failure",
    "failed",
]
INTENT_TYPES = [
    "question",
    "problem",
    "request",
    "complaint",
    "payment",
    "billing",
    "financial",
    "document",
    "protocol",
    "other",
]


def _ref(name: str) -> JsonSchema:
    return {"$ref": f"#/components/schemas/{name}"}


def _nullable(schema: JsonSchema) -> JsonSchema:
    return {"anyOf": [schema, {"type": "null"}]}


def _json_value() -> JsonSchema:
    return {
        "anyOf": [
            {"type": "object", "additionalProperties": True},
            {"type": "array", "items": {}},
            {"type": "string"},
            {"type": "number"},
            {"type": "boolean"},
            {"type": "null"},
        ]
    }


def _json_response(
    description: str,
    schema: JsonSchema,
    examples: Mapping[str, Any] | None = None,
) -> JsonSchema:
    content: JsonSchema = {"application/json": {"schema": schema}}
    if examples:
        content["application/json"]["examples"] = {
            name: {"summary": name, "value": value} for name, value in examples.items()
        }
    return {"description": description, "content": content}


def _detail_response(description: str, detail: str) -> JsonSchema:
    return _json_response(
        description,
        _ref("HTTPExceptionDetail"),
        {detail: {"detail": detail}},
    )


def _schemas() -> dict[str, JsonSchema]:
    return {
        "HTTPExceptionDetail": {
            "title": "HTTP exception detail",
            "description": "The detail object used by known HTTPException responses.",
            "type": "object",
            "required": ["detail"],
            "properties": {"detail": {"type": "string"}},
            "additionalProperties": False,
        },
        "HealthResponse": {
            "title": "Health response",
            "type": "object",
            "required": ["status"],
            "properties": {"status": {"type": "string", "enum": ["ok"]}},
            "additionalProperties": False,
            "example": {"status": "ok"},
        },
        "QueueMetrics": {
            "title": "Queue metrics",
            "description": (
                "Queue and dead-letter counts returned by the operational view. "
                "conversation_cycles is an empty map when cycle metrics are unavailable."
            ),
            "type": "object",
            "required": [
                "ia_queue",
                "ia_dead_letter",
                "audio_transcription_queue",
                "audio_transcription_dead_letter",
                "image_extraction_queue",
                "image_extraction_dead_letter",
                "conversation_cycles",
            ],
            "properties": {
                name: {"type": "integer", "minimum": 0}
                for name in (
                    "ia_queue",
                    "ia_dead_letter",
                    "audio_transcription_queue",
                    "audio_transcription_dead_letter",
                    "image_extraction_queue",
                    "image_extraction_dead_letter",
                )
            }
            | {
                "conversation_cycles": {
                    "type": "object",
                    "additionalProperties": {"type": "integer", "minimum": 0},
                }
            },
            "additionalProperties": False,
            "example": {
                "ia_queue": 2,
                "ia_dead_letter": 0,
                "audio_transcription_queue": 1,
                "audio_transcription_dead_letter": 0,
                "image_extraction_queue": 0,
                "image_extraction_dead_letter": 1,
                "conversation_cycles": {"pending": 3, "completed": 12},
            },
        },
        "WebhookRequest": {
            "title": "DigiSac webhook envelope",
            "description": (
                "Permissive DigiSac JSON envelope. The event and data shapes shown "
                "are supported examples, not an exhaustive event list. File metadata "
                "is limited to safe descriptive fields; file contents and download "
                "locations are not part of this contract."
            ),
            "type": "object",
            "required": ["event", "data"],
            "properties": {
                "event": {"type": "string"},
                "data": {"type": "object", "additionalProperties": True},
            },
            "additionalProperties": True,
        },
        "WebhookReceivedResponse": {
            "type": "object",
            "required": [
                "status",
                "conversation_id",
                "transcription_queued",
                "image_extraction_queued",
            ],
            "properties": {
                "status": {"type": "string", "enum": ["received"]},
                "conversation_id": {"type": "string"},
                "transcription_queued": {"type": "boolean"},
                "image_extraction_queued": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "WebhookDuplicateResponse": {
            "type": "object",
            "required": ["status", "conversation_id"],
            "properties": {
                "status": {"type": "string", "enum": ["duplicate"]},
                "conversation_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "WebhookTicketResponse": {
            "type": "object",
            "required": ["status", "conversation_id"],
            "properties": {
                "status": {
                    "type": "string",
                    "enum": [
                        "ticket_created",
                        "ticket_reopened",
                        "ticket_updated",
                        "ticket_closed",
                        "ticket_already_closed",
                    ],
                },
                "conversation_id": {"type": "string"},
                "cycle_id": {"type": "string", "format": "uuid"},
                "cycle_created": {"type": "boolean"},
                "queued": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "WebhookIgnoredResponse": {
            "type": "object",
            "required": ["status", "reason"],
            "properties": {
                "status": {"type": "string", "enum": ["ignored"]},
                "reason": {
                    "type": "string",
                    "enum": [
                        "unsupported_event",
                        "missing_ticket_id",
                        "missing_protocol",
                        "is_from_bot",
                        "bot_origin_fallback",
                        "missing_is_from_me",
                        "unsupported_message_type",
                        "empty_message_text",
                        "missing_or_invalid_data",
                    ],
                },
                "conversation_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "ProcessingStatus": {
            "title": "Persisted processing status",
            "description": (
                "Statuses emitted from persisted conversation cycles. The source "
                "ProcessingStatus model also declares processing for compatibility, "
                "but the persisted cycle constraint does not emit processing."
            ),
            "type": "string",
            "enum": PERSISTED_STATUSES,
        },
        "ClassificationResult": {
            "title": "Classification result projection",
            "description": (
                "Result projection distinguishing the external conversation_id, "
                "the cycle_id UUID, and the classification_public_id UUID. Joined "
                "classification fields are nullable before the handler availability guard."
            ),
            "type": "object",
            "required": [
                "cycle_id",
                "conversation_id",
                "sequence_number",
                "status",
                "warning_count",
                "classification_public_id",
                "intent_type",
                "confidence",
                "title",
                "protocol",
                "description",
                "department",
                "agent",
                "message_count",
                "processed_at",
            ],
            "properties": {
                "cycle_id": {"type": "string", "format": "uuid"},
                "conversation_id": {"type": "string"},
                "sequence_number": {"type": "integer"},
                "status": {"type": "string", "enum": PERSISTED_STATUSES},
                "warning_count": {"type": "integer", "minimum": 0},
                "classification_public_id": _nullable(
                    {"type": "string", "format": "uuid"}
                ),
                "intent_type": _nullable({"type": "string", "enum": INTENT_TYPES}),
                "confidence": _nullable({"type": "number"}),
                "title": _nullable({"type": "string"}),
                "protocol": _nullable({"type": "string"}),
                "description": _nullable({"type": "string"}),
                "department": _nullable({"type": "array", "items": {"type": "string"}}),
                "agent": _nullable({"type": "array", "items": {"type": "string"}}),
                "message_count": _nullable({"type": "integer", "minimum": 0}),
                "processed_at": _nullable({"type": "string", "format": "date-time"}),
            },
            "additionalProperties": False,
        },
        "CycleRecord": {
            "title": "Persisted conversation cycle",
            "description": (
                "The serialized conversation_processing_cycles row returned by the "
                "cycle status and history endpoints. cycle_id paths use public_id; "
                "classification_id is the internal database foreign key included by SELECT *."
            ),
            "type": "object",
            "required": [
                "id",
                "public_id",
                "conversation_id",
                "sequence_number",
                "cycle_start_strategy",
                "status",
                "attempt_count",
                "warning_count",
                "context_reduction_applied",
                "context_reduction_json",
                "history_recovery_attempt",
                "history_page_count",
                "created_at",
                "updated_at",
            ],
            "properties": {
                "id": {"type": "integer", "format": "int64"},
                "public_id": {"type": "string", "format": "uuid"},
                "conversation_id": {"type": "string"},
                "sequence_number": {"type": "integer"},
                "protocol": _nullable({"type": "string"}),
                "cycle_started_at": _nullable(
                    {"type": "string", "format": "date-time"}
                ),
                "ticket_closed_at": _nullable(
                    {"type": "string", "format": "date-time"}
                ),
                "cycle_start_strategy": {"type": "string"},
                "open_event_key": _nullable({"type": "string"}),
                "close_event_key": _nullable({"type": "string"}),
                "status": {"type": "string", "enum": PERSISTED_STATUSES},
                "attempt_count": {"type": "integer", "minimum": 0},
                "transient_retry_count": {"type": "integer", "minimum": 0},
                "error_phase": _nullable({"type": "string"}),
                "error_message": _nullable({"type": "string"}),
                "warning_count": {"type": "integer", "minimum": 0},
                "snapshot_json": _json_value(),
                "rendered_context": _nullable({"type": "string"}),
                "model_context": _nullable({"type": "string"}),
                "context_reduction_applied": {"type": "boolean"},
                "context_reduction_json": {"type": "array", "items": {}},
                "history_recovery_attempt": {"type": "integer", "minimum": 0},
                "history_page_count": {"type": "integer", "minimum": 0},
                "processing_time_ms": _nullable({"type": "integer", "minimum": 0}),
                "classification_id": _nullable({"type": "integer", "format": "int64"}),
                "next_attempt_at": _nullable({"type": "string", "format": "date-time"}),
                "enqueued_at": _nullable({"type": "string", "format": "date-time"}),
                "lease_owner": _nullable({"type": "string"}),
                "lease_expires_at": _nullable(
                    {"type": "string", "format": "date-time"}
                ),
                "created_at": {"type": "string", "format": "date-time"},
                "updated_at": {"type": "string", "format": "date-time"},
                "completed_at": _nullable({"type": "string", "format": "date-time"}),
            },
            "additionalProperties": False,
        },
        "IdentityCommandRequest": {
            "title": "Administrative identity command request",
            "description": (
                "A bounded command reason and opaque idempotency key. The key is "
                "never returned or included in administrative audit projections."
            ),
            "type": "object",
            "required": ["reason", "idempotency_key"],
            "properties": {
                "reason": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "pattern": "^[a-z0-9_:-]{1,120}$",
                },
                "idempotency_key": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200,
                    "description": "Opaque client command key; never echoed.",
                },
            },
            "additionalProperties": False,
        },
        "IdentityLinkConfirmRequest": {
            "title": "Identity-link confirmation request",
            "type": "object",
            "required": [
                "reason",
                "idempotency_key",
                "acessorias_company_external_id",
            ],
            "properties": {
                "reason": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "pattern": "^[a-z0-9_:-]{1,120}$",
                },
                "idempotency_key": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200,
                    "description": "Opaque client command key; never echoed.",
                },
                "acessorias_company_external_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200,
                    "description": "Opaque Acessórias company external ID.",
                },
            },
            "additionalProperties": False,
        },
        "IdentityLinkRejectRequest": {
            "title": "Identity-link rejection request",
            "type": "object",
            "required": ["reason", "idempotency_key"],
            "properties": {
                "reason": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "pattern": "^[a-z0-9_:-]{1,120}$",
                },
                "idempotency_key": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200,
                    "description": "Opaque client command key; never echoed.",
                },
            },
            "additionalProperties": False,
        },
        "IdentityLinkCommandResponse": {
            "title": "Administrative identity-link command result",
            "description": (
                "Sanitized stable external IDs, state, safe source metadata, and "
                "server timestamps. It does not contain local database IDs, evidence, "
                "contact data, command keys, or provider payloads."
            ),
            "type": "object",
            "required": [
                "digisac_contact_external_id",
                "acessorias_company_external_id",
                "state",
                "source",
                "confirmation_source",
                "confirmed_at",
                "rejection_reason",
                "created_at",
                "updated_at",
            ],
            "properties": {
                "digisac_contact_external_id": {"type": "string"},
                "acessorias_company_external_id": {"type": "string"},
                "state": {
                    "type": "string",
                    "enum": ["candidate", "confirmed", "rejected"],
                },
                "source": {"type": "string"},
                "confirmation_source": {"type": ["string", "null"]},
                "confirmed_at": {"type": ["string", "null"], "format": "date-time"},
                "rejection_reason": {"type": ["string", "null"]},
                "created_at": {"type": "string", "format": "date-time"},
                "updated_at": {"type": "string", "format": "date-time"},
            },
            "additionalProperties": False,
        },
    }


def _response(
    description: str,
    schema: JsonSchema,
    examples: Mapping[str, Any] | None = None,
) -> JsonSchema:
    return _json_response(description, schema, examples)


def _decorate_operations(document: dict[str, Any]) -> None:
    paths = cast(dict[str, JsonSchema], document["paths"])

    def operation(
        path: str,
        method: str,
        *,
        tag: str,
        summary: str,
        description: str,
        responses: dict[str, JsonSchema],
        security: list[JsonSchema] | None = None,
    ) -> JsonSchema:
        current = cast(JsonSchema, paths[path][method])
        current.update(
            {
                "tags": [tag],
                "summary": summary,
                "description": description,
                "responses": responses,
            }
        )
        if security is not None:
            current["security"] = security
        return current

    operation(
        "/health",
        "get",
        tag="Operações",
        summary="Check service readiness",
        description=(
            "Checks Redis first and then PostgreSQL. A database readiness failure "
            "is returned as 503 with a known detail object; an unmapped Redis failure "
            "has no stable response contract."
        ),
        responses={
            "200": _response(
                "Both dependencies are ready.",
                _ref("HealthResponse"),
                {"healthy": {"status": "ok"}},
            ),
            "503": _detail_response(
                "PostgreSQL readiness is unavailable.", "database unavailable"
            ),
        },
    )
    operation(
        "/queues",
        "get",
        tag="Operações",
        summary="Inspect queue counts",
        description=(
            "Returns integer queue/dead-letter counts and grouped cycle status counts. "
            "No authentication is currently configured for internal query operations, "
            "and unmapped Redis failures remain server failures without a stable body."
        ),
        responses={
            "200": _response(
                "Current queue metrics.",
                _ref("QueueMetrics"),
                {
                    "metrics": {
                        "ia_queue": 2,
                        "ia_dead_letter": 0,
                        "audio_transcription_queue": 1,
                        "audio_transcription_dead_letter": 0,
                        "image_extraction_queue": 0,
                        "image_extraction_dead_letter": 1,
                        "conversation_cycles": {"pending": 3, "completed": 12},
                    }
                },
            )
        },
    )

    hmac_description = (
        "Conditional HMAC-SHA256 header; WEBHOOK_SECRET enables validation."
    )
    webhook_description = (
        f"{hmac_description}\n\n"
        "The X-Digisac-Signature value may be a hexadecimal digest or sha256=<digest>. "
        "The digest is computed over the raw request body, and validation occurs "
        "before JSON parsing when WEBHOOK_SECRET is configured; "
        "without that setting the current dependency does not require the header. "
        "A 202 is asynchronous acceptance, not classification completion. Durable "
        "cycle and media reservations prevent duplicate work on retries."
    )
    webhook = operation(
        "/webhook/digisac",
        "post",
        tag="Webhook DigiSac",
        summary="Accept a DigiSac event",
        description=webhook_description,
        responses={
            "200": _response(
                "The event was ignored after parsing.",
                _ref("WebhookIgnoredResponse"),
                {
                    "unsupportedEvent": {
                        "status": "ignored",
                        "reason": "unsupported_event",
                    },
                    "missingProtocol": {
                        "status": "ignored",
                        "reason": "missing_protocol",
                        "conversation_id": "ticket-4821",
                    },
                },
            ),
            "202": _response(
                "The event was accepted for asynchronous processing or was a duplicate.",
                {
                    "oneOf": [
                        _ref("WebhookReceivedResponse"),
                        _ref("WebhookDuplicateResponse"),
                        _ref("WebhookTicketResponse"),
                    ]
                },
                {
                    "received": {
                        "status": "received",
                        "conversation_id": "ticket-4821",
                        "transcription_queued": False,
                        "image_extraction_queued": False,
                    },
                    "duplicate": {
                        "status": "duplicate",
                        "conversation_id": "ticket-4821",
                    },
                    "ticketCreated": {
                        "status": "ticket_created",
                        "conversation_id": "ticket-4821",
                        "cycle_id": "018f2b48-9f30-7b18-8d4e-4a6f23c1de01",
                        "cycle_created": True,
                    },
                    "ticketClosed": {
                        "status": "ticket_closed",
                        "conversation_id": "ticket-4821",
                        "cycle_id": "018f2b48-9f30-7b18-8d4e-4a6f23c1de01",
                        "queued": True,
                    },
                },
            ),
            "400": _response(
                "The request body is malformed or is not valid JSON.",
                _ref("HTTPExceptionDetail"),
                {
                    "invalidJson": {"detail": "Invalid JSON payload"},
                    "nonObject": {"detail": "Webhook payload must be an object"},
                    "invalidData": {
                        "detail": "Malformed Digisac payload: 'data' must be an object"
                    },
                },
            ),
            "401": _response(
                "The conditional HMAC header is missing or invalid.",
                _ref("HTTPExceptionDetail"),
                {
                    "missingSignature": {"detail": "Missing webhook signature"},
                    "invalidSignature": {"detail": "Invalid webhook signature"},
                },
            ),
        },
        security=[{"DigisacWebhookHMAC": []}],
    )
    request_body = cast(JsonSchema, webhook.setdefault("requestBody", {}))
    request_body.update(
        {
            "required": True,
            "description": "DigiSac event envelope; successful processing requires object data.",
            "content": {
                "application/json": {
                    "schema": _ref("WebhookRequest"),
                    "examples": {
                        "ticketCreated": {
                            "summary": "Ticket event",
                            "value": {
                                "event": "ticket.created",
                                "data": {
                                    "id": "ticket-4821",
                                    "isOpen": True,
                                    "protocol": "PR-2026-004821",
                                    "departmentId": "department-7",
                                },
                            },
                        },
                        "messageCreated": {
                            "summary": "Text message event",
                            "value": {
                                "event": "message.created",
                                "data": {
                                    "id": "message-193",
                                    "ticketId": "ticket-4821",
                                    "type": "chat",
                                    "text": "I need help with my invoice.",
                                    "isFromMe": False,
                                    "isFromBot": False,
                                },
                            },
                        },
                        "imageDocument": {
                            "summary": "Image document metadata",
                            "value": {
                                "event": "message.created",
                                "data": {
                                    "id": "message-194",
                                    "ticketId": "ticket-4821",
                                    "type": "document",
                                    "file": {
                                        "mimetype": "image/jpeg",
                                        "name": "invoice.jpg",
                                    },
                                    "isFromMe": False,
                                    "isFromBot": False,
                                },
                            },
                        },
                    },
                }
            },
        }
    )

    conversation_status_description = (
        "Returns the latest persisted cycle state. conversation_id is the external "
        "DigiSac identifier and cycle_id is its public UUID. An accepted webhook does "
        "not imply a terminal state or an available result."
    )
    operation(
        "/conversations/{conversation_id}/status",
        "get",
        tag="Conversas",
        summary="Get the latest conversation status",
        description=conversation_status_description,
        responses={
            "200": _response(
                "Latest persisted processing state.",
                _ref("ConversationProcessing"),
                {
                    "processing": {
                        "conversation_id": "ticket-4821",
                        "cycle_id": "018f2b48-9f30-7b18-8d4e-4a6f23c1de01",
                        "status": "classifying",
                        "started_at": "2026-08-13T12:00:00Z",
                        "completed_at": None,
                        "error_message": None,
                        "result": None,
                        "retry_count": 0,
                        "transient_retry_count": 0,
                        "max_retries": 3,
                    },
                    "completed": {
                        "conversation_id": "ticket-4821",
                        "cycle_id": "018f2b48-9f30-7b18-8d4e-4a6f23c1de01",
                        "status": "completed",
                        "started_at": "2026-08-13T12:00:00Z",
                        "completed_at": "2026-08-13T12:00:08Z",
                        "error_message": None,
                        "result": None,
                        "retry_count": 0,
                        "transient_retry_count": 0,
                        "max_retries": 3,
                    },
                },
            ),
            "404": _detail_response(
                "No cycle exists for the conversation.", "Conversation not found"
            ),
        },
    )
    result_description = (
        "Returns the latest classification projection when classification_public_id "
        "is available. A terminal cycle and a classification result are separate facts."
    )
    operation(
        "/conversations/{conversation_id}/result",
        "get",
        tag="Conversas",
        summary="Get the latest available conversation result",
        description=result_description,
        responses={
            "200": _response(
                "Latest available classification projection.",
                _ref("ClassificationResult"),
                {"classification": _classification_example()},
            ),
            "404": _detail_response(
                "No classification is available.", "Result not available"
            ),
        },
    )
    cycles = operation(
        "/conversations/{conversation_id}/cycles",
        "get",
        tag="Conversas",
        summary="List persisted conversation cycles",
        description=(
            "Lists cycles newest first. The limit defaults to 50 and the database "
            "projection clamps the effective value to 1-100; this operational clamp "
            "does not reject values outside that range. A non-integer limit receives "
            "FastAPI's standard 422 validation response."
        ),
        responses={
            "200": _response(
                "Persisted cycles, possibly an empty list.",
                {"type": "array", "items": _ref("CycleRecord")},
                {"cycles": [_cycle_example()]},
            ),
            "422": _response(
                "FastAPI query-parameter validation failed.",
                _ref("HTTPValidationError"),
                {
                    "invalidLimit": {
                        "detail": [
                            {
                                "loc": ["query", "limit"],
                                "msg": "Input should be a valid integer",
                                "type": "int_parsing",
                            }
                        ]
                    }
                },
            ),
        },
    )
    for parameter in cast(list[JsonSchema], cycles.get("parameters", [])):
        if parameter.get("name") == "conversation_id":
            parameter[
                "description"
            ] = "External textual DigiSac ticket/conversation identifier; not declared as UUID."
        elif parameter.get("name") == "limit":
            parameter[
                "description"
            ] = "Maximum cycles to return; default 50 and effective database clamp 1-100. Type errors return 422."

    cycle_status_description = (
        "Returns the persisted cycle row identified by public_id. The path value is "
        "named cycle_id but the handler does not validate its UUID format."
    )
    operation(
        "/cycles/{cycle_id}/status",
        "get",
        tag="Ciclos",
        summary="Get a persisted cycle",
        description=cycle_status_description,
        responses={
            "200": _response(
                "Serialized persisted cycle row.",
                _ref("CycleRecord"),
                {"cycle": _cycle_example()},
            ),
            "404": _detail_response(
                "The cycle public UUID was not found.", "Cycle not found"
            ),
        },
    )
    operation(
        "/cycles/{cycle_id}/result",
        "get",
        tag="Ciclos",
        summary="Get a cycle classification result",
        description=(
            "Returns the classification projection for a cycle when its "
            "classification_public_id is available. completed and "
            "completed_with_warnings are terminal states, but status alone does "
            "not guarantee result availability."
        ),
        responses={
            "200": _response(
                "Classification projection for the cycle.",
                _ref("ClassificationResult"),
                {"classification": _classification_example()},
            ),
            "404": {
                "description": "The cycle or its classification is unavailable.",
                "content": {
                    "application/json": {
                        "schema": _ref("HTTPExceptionDetail"),
                        "examples": {
                            "cycleNotFound": {
                                "summary": "Cycle not found",
                                "value": {"detail": "Cycle not found"},
                            },
                            "resultNotAvailable": {
                                "summary": "Result unavailable",
                                "value": {"detail": "Cycle result not available"},
                            },
                        },
                    }
                },
            },
        },
    )
    admin_security: list[JsonSchema] = [{"AdminBearer": []}]
    operation(
        "/admin/acessorias/identity-links",
        "get",
        tag="Administração",
        summary="List identity-link triage projections",
        description=(
            "Returns PostgreSQL-authoritative identity and candidate projections. "
            "The optional state filter accepts candidate, confirmed, rejected, "
            "ambiguous, unresolved, or conflict. Cursors are opaque and bound to "
            "their filter scope; no phone, email, or evidence value is returned."
        ),
        security=admin_security,
        responses={
            "200": _response(
                "A bounded identity-link triage page.",
                _ref("IdentityLinkListResponse"),
            ),
            "400": _detail_response("The filter, cursor, or limit is invalid.", "Invalid cursor"),
            "401": _detail_response(
                "The administrative bearer token is missing or invalid.",
                "Invalid administrative credentials",
            ),
        },
    )
    operation(
        "/admin/acessorias/contacts/{digisac_contact_external_id}/identity",
        "get",
        tag="Administração",
        summary="Get one identity-link detail projection",
        description=(
            "Returns a safe projection for an existing canonical DigiSac contact, "
            "including group/no-candidate contacts. The read does not run discovery "
            "or hydration and does not expose evidence values."
        ),
        security=admin_security,
        responses={
            "200": _response(
                "The contact identity projection.", _ref("IdentityContactDetail")
            ),
            "401": _detail_response(
                "The administrative bearer token is missing or invalid.",
                "Invalid administrative credentials",
            ),
            "404": _detail_response(
                "The canonical DigiSac contact does not exist.",
                "DigiSac contact not found",
            ),
        },
    )
    operation(
        "/admin/acessorias/companies",
        "get",
        tag="Administração",
        summary="List active Acessórias companies",
        description=(
            "Searches only present and active local directory companies. The query "
            "is a display-only filter and cannot create identity evidence or links. "
            "Cursors are opaque and bound to the query scope."
        ),
        security=admin_security,
        responses={
            "200": _response(
                "A bounded active-company directory page.", _ref("CompanyListResponse")
            ),
            "400": _detail_response("The query, cursor, or limit is invalid.", "Invalid cursor"),
            "401": _detail_response(
                "The administrative bearer token is missing or invalid.",
                "Invalid administrative credentials",
            ),
        },
    )
    command_responses = {
        "200": _response(
            "The stored result of an idempotent administrative replay.",
            _ref("IdentityLinkCommandResponse"),
        ),
        "201": _response(
            "A newly applied administrative identity-link command.",
            _ref("IdentityLinkCommandResponse"),
        ),
        "400": _detail_response(
            "The command body, reason, or idempotency key is invalid.",
            "Invalid administrative command body",
        ),
        "401": _detail_response(
            "The administrative bearer token is missing or invalid.",
            "Invalid administrative credentials",
        ),
        "404": _detail_response(
            "The canonical contact, company, or identity link does not exist.",
            "Identity reference not found",
        ),
        "409": _detail_response(
            "The command conflicts with an existing confirmation or key use.",
            "Idempotency key conflict",
        ),
    }
    operation(
        "/admin/acessorias/contacts/{digisac_contact_external_id}/identity-links/confirm",
        "post",
        tag="Administração",
        summary="Confirm one identity link",
        description=(
            "Confirms only the requested canonical contact/company pair. The "
            "operation is PostgreSQL-authoritative, serialized by contact, and "
            "idempotent; it does not run discovery, call providers, use Redis, "
            "or change historical cycle resolutions."
        ),
        security=admin_security,
        responses={
            **command_responses,
            "422": _detail_response(
                "The requested company is absent or unavailable in the current directory.",
                "Acessórias company unavailable",
            ),
        },
    )
    operation(
        "/admin/acessorias/contacts/{digisac_contact_external_id}/identity-links/{acessorias_company_external_id}/reject",
        "post",
        tag="Administração",
        summary="Reject one identity link",
        description=(
            "Rejects only the requested existing pair and appends an auditable "
            "administrative transition while preserving prior evidence and history. "
            "It never promotes another company or changes cycle resolution."
        ),
        security=admin_security,
        responses=command_responses,
    )
    discovery_operation = operation(
        "/admin/acessorias/contacts/{digisac_contact_external_id}/identity-discovery",
        "post",
        tag="Administração",
        summary="Run deterministic identity discovery",
        description=(
            "Re-runs the existing conservative identity discovery rules for one "
            "canonical local DigiSac contact. The PostgreSQL-authoritative command "
            "is idempotent, exposes only external IDs and safe metadata, and never "
            "calls providers, Redis, hydration, synchronization, or historical "
            "cycle/Request operations."
        ),
        security=admin_security,
        responses={
            "200": _response(
                "The deterministic discovery result, including an idempotent replay.",
                _ref("IdentityDiscoveryResponse"),
            ),
            "400": _detail_response(
                "The command body or idempotency key is invalid.",
                "Invalid administrative command body",
            ),
            "401": _detail_response(
                "The administrative bearer token is missing or invalid.",
                "Invalid administrative credentials",
            ),
            "404": _detail_response(
                "The canonical DigiSac contact does not exist.",
                "Identity reference not found",
            ),
            "409": _detail_response(
                "The idempotency key conflicts with another command or execution.",
                "Idempotency key conflict",
            ),
        },
    )
    confirm_operation = paths[
        "/admin/acessorias/contacts/{digisac_contact_external_id}/identity-links/confirm"
    ]["post"]
    confirm_operation["requestBody"] = {
        "required": True,
        "content": {
            "application/json": {
                "schema": _ref("IdentityLinkConfirmRequest"),
                "description": "Reason category, opaque command key, and target company ID.",
            }
        },
    }
    reject_operation = paths[
        "/admin/acessorias/contacts/{digisac_contact_external_id}/identity-links/{acessorias_company_external_id}/reject"
    ]["post"]
    reject_operation["requestBody"] = {
        "required": True,
        "content": {
            "application/json": {
                "schema": _ref("IdentityLinkRejectRequest"),
                "description": "Reason category and opaque command key; neither is echoed.",
            }
        },
    }
    discovery_operation["requestBody"] = {
        "required": True,
        "content": {
            "application/json": {
                "schema": _ref("IdentityDiscoveryRequest"),
                "description": "Opaque command key; it is never echoed.",
            }
        },
    }
    for path in (
        "/admin/acessorias/identity-links",
        "/admin/acessorias/companies",
    ):
        for parameter in cast(
            list[JsonSchema], paths[path]["get"].get("parameters", [])
        ):
            if parameter.get("name") == "limit":
                parameter["description"] = "Page size from 1 through 100; invalid values return 400."
                parameter["schema"] = {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 50,
                }
    for parameter in cast(
        list[JsonSchema],
        paths["/admin/acessorias/identity-links"]["get"].get("parameters", []),
    ):
        if parameter.get("name") == "state":
            parameter["description"] = "Optional current/link state filter."
            parameter["schema"] = {
                "type": "string",
                "enum": [
                    "candidate",
                    "confirmed",
                    "rejected",
                    "ambiguous",
                    "unresolved",
                    "conflict",
                ],
            }
        elif parameter.get("name") == "cursor":
            parameter["description"] = "Opaque cursor bound to the filter scope."
    for parameter in cast(
        list[JsonSchema], paths["/admin/acessorias/companies"]["get"].get("parameters", [])
    ):
        if parameter.get("name") == "query":
            parameter["description"] = "Optional display-only company search filter."
        elif parameter.get("name") == "cursor":
            parameter["description"] = "Opaque cursor bound to the display filter."
    for path in (
        "/conversations/{conversation_id}/status",
        "/conversations/{conversation_id}/result",
    ):
        for parameter in cast(
            list[JsonSchema], paths[path]["get"].get("parameters", [])
        ):
            if parameter.get("name") == "conversation_id":
                parameter[
                    "description"
                ] = "External textual DigiSac ticket/conversation identifier; not declared as UUID."
    for parameter in cast(
        list[JsonSchema],
        paths["/cycles/{cycle_id}/status"]["get"].get("parameters", []),
    ) + cast(
        list[JsonSchema],
        paths["/cycles/{cycle_id}/result"]["get"].get("parameters", []),
    ):
        if parameter.get("name") == "cycle_id":
            parameter[
                "description"
            ] = "Persisted cycle public_id; the handler does not validate UUID format."


def _classification_example() -> dict[str, Any]:
    return {
        "cycle_id": "018f2b48-9f30-7b18-8d4e-4a6f23c1de01",
        "conversation_id": "ticket-4821",
        "sequence_number": 1,
        "status": "completed",
        "warning_count": 0,
        "classification_public_id": "018f2b48-9f30-7b18-8d4e-4a6f23c1de02",
        "intent_type": "question",
        "confidence": 0.98,
        "title": "Invoice question",
        "protocol": "PR-2026-004821",
        "description": "The customer asks how to obtain an invoice.",
        "department": ["Customer care"],
        "agent": ["Agent Example"],
        "message_count": 3,
        "processed_at": "2026-08-13T12:00:08Z",
    }


def _cycle_example() -> dict[str, Any]:
    return {
        "id": 42,
        "public_id": "018f2b48-9f30-7b18-8d4e-4a6f23c1de01",
        "conversation_id": "ticket-4821",
        "sequence_number": 1,
        "protocol": "PR-2026-004821",
        "cycle_started_at": "2026-08-13T12:00:00Z",
        "ticket_closed_at": "2026-08-13T12:00:05Z",
        "cycle_start_strategy": "ticket_created_event",
        "open_event_key": "open-event-key",
        "close_event_key": "close-event-key",
        "status": "completed",
        "attempt_count": 1,
        "transient_retry_count": 0,
        "error_phase": None,
        "error_message": None,
        "warning_count": 0,
        "snapshot_json": {"message_count": 3},
        "rendered_context": None,
        "model_context": None,
        "context_reduction_applied": False,
        "context_reduction_json": [],
        "history_recovery_attempt": 1,
        "history_page_count": 1,
        "processing_time_ms": 8000,
        "classification_id": 7,
        "next_attempt_at": None,
        "enqueued_at": "2026-08-13T12:00:05Z",
        "lease_owner": None,
        "lease_expires_at": None,
        "created_at": "2026-08-13T12:00:00Z",
        "updated_at": "2026-08-13T12:00:08Z",
        "completed_at": "2026-08-13T12:00:08Z",
    }


def build_openapi_contract(app: FastAPI) -> dict[str, Any]:
    """Build the OpenAPI document from FastAPI routes and source projections."""
    document = get_openapi(
        title=app.title,
        version=app.version,
        description=(
            "CAI's supported HTTP surface is intentionally unversioned. /v1/ and "
            "/v2/ are future compatibility policy only, not mounted routes. Existing "
            "public query operations have no authentication scheme; administrative "
            "identity projections, identity-link commands, and identity discovery "
            "use the AdminBearer scheme."
        ),
        routes=app.routes,
    )
    document["servers"] = [
        {"url": "http://localhost:8000", "description": "Desenvolvimento local"}
    ]
    document["tags"] = [
        {"name": "Webhook DigiSac", "description": "DigiSac event ingestion."},
        {"name": "Operações", "description": "Readiness and queue metrics."},
        {
            "name": "Conversas",
            "description": "Conversation status, results, and cycle history.",
        },
        {"name": "Ciclos", "description": "Persisted cycle status and results."},
        {
            "name": "Administração",
            "description": "Authenticated identity triage projections and commands.",
        },
    ]
    components = cast(dict[str, Any], document.setdefault("components", {}))
    schemas = cast(dict[str, JsonSchema], components.setdefault("schemas", {}))
    schemas.update(_schemas())
    components["securitySchemes"] = {
        "DigisacWebhookHMAC": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Digisac-Signature",
            "description": "Conditional HMAC-SHA256 header; WEBHOOK_SECRET enables validation.",
        },
        "AdminBearer": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "opaque",
            "description": "Opaque ADMIN_API_TOKEN for internal administrative routes.",
        },
    }
    _decorate_operations(document)
    return document


def install_openapi_contract(app: FastAPI) -> None:
    """Install the generated contract while retaining FastAPI's cache behavior."""

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        app.openapi_schema = build_openapi_contract(app)
        return app.openapi_schema

    app.openapi = custom_openapi
