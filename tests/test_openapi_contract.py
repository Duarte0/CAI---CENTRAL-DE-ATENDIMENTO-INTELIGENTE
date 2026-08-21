"""Focused tests for the generated HTTP documentation contract."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from fastapi.testclient import TestClient

from main import app


BUSINESS_PATHS = {
    "/health",
    "/queues",
    "/webhook/digisac",
    "/conversations/{conversation_id}/status",
    "/conversations/{conversation_id}/result",
    "/conversations/{conversation_id}/cycles",
    "/cycles/{cycle_id}/status",
    "/cycles/{cycle_id}/result",
}
ADMIN_GET_PATHS = {
    "/admin/acessorias/identity-links",
    "/admin/acessorias/contacts/{digisac_contact_external_id}/identity",
    "/admin/acessorias/companies",
}
ADMIN_POST_PATHS = {
    "/admin/acessorias/contacts/{digisac_contact_external_id}/identity-links/confirm",
    "/admin/acessorias/contacts/{digisac_contact_external_id}/identity-links/{acessorias_company_external_id}/reject",
    "/admin/acessorias/contacts/{digisac_contact_external_id}/identity-discovery",
}
ADMIN_PATHS = ADMIN_GET_PATHS | ADMIN_POST_PATHS
TAGS = {"Webhook DigiSac", "Operações", "Conversas", "Ciclos", "Administração"}


def _resolve(
    schema: Mapping[str, Any], document: Mapping[str, Any]
) -> Mapping[str, Any]:
    reference = schema.get("$ref")
    if not isinstance(reference, str):
        return schema
    name = reference.rsplit("/", 1)[-1]
    return document["components"]["schemas"][name]


def _matches_schema(
    value: Any, schema: Mapping[str, Any], document: Mapping[str, Any]
) -> bool:
    schema = _resolve(schema, document)
    if "oneOf" in schema or "anyOf" in schema:
        alternatives = schema.get("oneOf", schema.get("anyOf", []))
        return any(
            _matches_schema(value, alternative, document)
            for alternative in alternatives
        )
    if "enum" in schema and value not in schema["enum"]:
        return False

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        if "null" in schema_type and value is None:
            return True
        schema_type = next((item for item in schema_type if item != "null"), None)
    if schema_type == "null":
        return value is None
    if schema_type == "object":
        if not isinstance(value, dict):
            return False
        if any(key not in value for key in schema.get("required", [])):
            return False
        properties = schema.get("properties", {})
        return all(
            key not in properties or _matches_schema(item, properties[key], document)
            for key, item in value.items()
        )
    if schema_type == "array":
        return isinstance(value, list) and all(
            _matches_schema(item, schema.get("items", {}), document) for item in value
        )
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    return True


def _document() -> dict[str, Any]:
    app.openapi_schema = None
    return app.openapi()


def test_openapi_describes_only_the_mounted_business_surface() -> None:
    document = _document()

    assert document["openapi"].startswith("3.")
    assert document["info"]["title"] == app.title
    assert document["info"]["version"] == app.version
    assert document["servers"] == [
        {"url": "http://localhost:8000", "description": "Desenvolvimento local"}
    ]
    assert {tag["name"] for tag in document["tags"]} == TAGS
    assert set(document["paths"]) == BUSINESS_PATHS | ADMIN_PATHS
    assert all(
        len(methods) == 1 and next(iter(methods.values())).get("summary")
        for methods in document["paths"].values()
    )
    assert "/v1/" not in document["paths"]
    assert "/v2/" not in document["paths"]
    assert "/webhook/debug" not in document["paths"]


def test_openapi_security_and_webhook_contract() -> None:
    document = _document()
    webhook = document["paths"]["/webhook/digisac"]["post"]
    scheme = document["components"]["securitySchemes"]["DigisacWebhookHMAC"]

    assert scheme == {
        "type": "apiKey",
        "in": "header",
        "name": "X-Digisac-Signature",
        "description": webhook["description"].split("\n", 1)[0],
    }
    assert webhook["security"] == [{"DigisacWebhookHMAC": []}]
    assert document["components"]["securitySchemes"]["AdminBearer"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "opaque",
        "description": "Opaque ADMIN_API_TOKEN for internal administrative routes.",
    }
    assert all(
        document["paths"][path]["get"]["security"] == [{"AdminBearer": []}]
        for path in ADMIN_GET_PATHS
    )
    assert all(
        document["paths"][path]["post"]["security"] == [{"AdminBearer": []}]
        for path in ADMIN_POST_PATHS
    )
    assert all(
        "security" not in document["paths"][path]["get"]
        for path in BUSINESS_PATHS - {"/webhook/digisac"}
    )
    assert webhook["requestBody"]["required"] is True
    assert set(webhook["requestBody"]["content"]["application/json"]["examples"]) == {
        "ticketCreated",
        "messageCreated",
        "imageDocument",
    }
    assert {"200", "202", "400", "401"}.issubset(webhook["responses"])
    assert "WEBHOOK_SECRET" in webhook["description"]
    assert "sha256=" in webhook["description"]


def test_openapi_describes_admin_projection_contract() -> None:
    document = _document()
    schemas = document["components"]["schemas"]
    assert {
        "IdentityLinkListResponse",
        "IdentityContactDetail",
        "CompanyListResponse",
        "IdentityCommandRequest",
        "IdentityLinkConfirmRequest",
        "IdentityLinkRejectRequest",
        "IdentityLinkCommandResponse",
        "IdentityDiscoveryRequest",
        "IdentityDiscoveryLinkProjection",
        "IdentityDiscoveryResponse",
    }.issubset(schemas)
    links = document["paths"]["/admin/acessorias/identity-links"]["get"]
    state = next(parameter for parameter in links["parameters"] if parameter["name"] == "state")
    limit = next(parameter for parameter in links["parameters"] if parameter["name"] == "limit")
    assert state["schema"]["enum"] == [
        "candidate",
        "confirmed",
        "rejected",
        "ambiguous",
        "unresolved",
        "conflict",
    ]
    assert limit["schema"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "default": 50,
    }
    assert set(links["responses"]) == {"200", "400", "401"}
    assert set(
        document["paths"]["/admin/acessorias/contacts/{digisac_contact_external_id}/identity"]["get"]["responses"]
    ) == {"200", "401", "404"}
    assert set(document["paths"]["/admin/acessorias/companies"]["get"]["responses"]) == {
        "200",
        "400",
        "401",
    }
    confirm_operation = document["paths"][
        "/admin/acessorias/contacts/{digisac_contact_external_id}/identity-links/confirm"
    ]["post"]
    reject_operation = document["paths"][
        "/admin/acessorias/contacts/{digisac_contact_external_id}/identity-links/{acessorias_company_external_id}/reject"
    ]["post"]
    assert set(confirm_operation["responses"]) == {
        "200",
        "201",
        "400",
        "401",
        "404",
        "409",
        "422",
    }
    assert set(reject_operation["responses"]) == {
        "200",
        "201",
        "400",
        "401",
        "404",
        "409",
    }
    discovery_operation = document["paths"][
        "/admin/acessorias/contacts/{digisac_contact_external_id}/identity-discovery"
    ]["post"]
    assert set(discovery_operation["responses"]) == {"200", "400", "401", "404", "409"}
    assert discovery_operation["requestBody"]["required"] is True
    assert confirm_operation["requestBody"]["required"] is True
    assert reject_operation["requestBody"]["required"] is True


def test_openapi_projects_queries_and_operational_errors() -> None:
    document = _document()
    schemas = document["components"]["schemas"]

    assert {
        "HealthResponse",
        "QueueMetrics",
        "ClassificationResult",
        "CycleRecord",
        "HTTPExceptionDetail",
    }.issubset(schemas)
    assert set(document["paths"]["/health"]["get"]["responses"]) == {"200", "503"}
    assert set(document["paths"]["/queues"]["get"]["responses"]) == {"200"}

    cycles = document["paths"]["/conversations/{conversation_id}/cycles"]["get"]
    limit = next(
        parameter for parameter in cycles["parameters"] if parameter["name"] == "limit"
    )
    assert limit["schema"]["default"] == 50
    assert "1-100" in limit["description"]
    assert "422" in cycles["responses"]

    result_properties = schemas["ClassificationResult"]["properties"]
    assert set(result_properties) == {
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
    }
    cycle_properties = schemas["CycleRecord"]["properties"]
    assert set(cycle_properties) == {
        "id",
        "public_id",
        "conversation_id",
        "sequence_number",
        "protocol",
        "cycle_started_at",
        "ticket_closed_at",
        "cycle_start_strategy",
        "open_event_key",
        "close_event_key",
        "status",
        "attempt_count",
        "transient_retry_count",
        "error_phase",
        "error_message",
        "warning_count",
        "snapshot_json",
        "rendered_context",
        "model_context",
        "context_reduction_applied",
        "context_reduction_json",
        "history_recovery_attempt",
        "history_page_count",
        "processing_time_ms",
        "classification_id",
        "next_attempt_at",
        "enqueued_at",
        "lease_owner",
        "lease_expires_at",
        "created_at",
        "updated_at",
        "completed_at",
    }
    assert "processing" not in schemas["ProcessingStatus"]["enum"]
    assert "processing" in schemas["ProcessingStatus"]["description"]
    assert "conversation_id" in schemas["ClassificationResult"]["description"]
    assert "cycle_id" in schemas["ClassificationResult"]["description"]


def test_openapi_examples_match_their_declared_schemas_and_are_sanitized() -> None:
    document = _document()
    serialized = json.dumps(document, ensure_ascii=False).lower()
    for forbidden in ("/webhook/debug", "raw-webhook-marker", "authorization: bearer"):
        assert forbidden not in serialized

    for path_item in document["paths"].values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            request_body = operation.get("requestBody", {})
            for media in request_body.get("content", {}).values():
                for example in media.get("examples", {}).values():
                    assert _matches_schema(example["value"], media["schema"], document)
            for response in operation.get("responses", {}).values():
                for media in response.get("content", {}).values():
                    for example in media.get("examples", {}).values():
                        assert _matches_schema(
                            example["value"], media["schema"], document
                        )


def test_fastapi_documentation_endpoints_remain_available() -> None:
    client = TestClient(app)

    openapi_response = client.get("/openapi.json")
    assert openapi_response.status_code == 200
    assert openapi_response.json()["paths"] == _document()["paths"]

    docs_response = client.get("/docs")
    redoc_response = client.get("/redoc")
    assert docs_response.status_code == 200
    assert "swagger-ui" in docs_response.text.lower()
    assert redoc_response.status_code == 200
    assert "redoc" in redoc_response.text.lower()
