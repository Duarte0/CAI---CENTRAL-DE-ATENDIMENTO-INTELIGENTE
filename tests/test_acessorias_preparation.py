from __future__ import annotations

from typing import Any

import psycopg
import pytest

from src.api.routes import _canonical_ticket_contact_external_id
import src.core.acessorias_preparation as preparation_module
import src.workers.ia_worker as ia_worker_module
from src.core.config import settings
from src.core.acessorias_requests import (
    AcessoriasRequestOutcome,
    create_request_for_cycle,
    recover_mapping_missing_request,
)
from src.core.db import (
    close_cycle,
    create_open_cycle,
    insert_classification,
    record_ticket_assignment,
    upsert_digisac_contact,
)
from src.core.digisac_client import DigisacContact
from src.core.identity_resolution import confirm_identity_link, discover_identity
from src.workers.ia_worker import IAWorker


def test_group_ticket_uses_ticket_contact_not_message_sender() -> None:
    payload = {
        "contact": {"id": "group-contact", "isGroup": True},
        "messages": [{"contactId": "individual-sender"}],
    }

    assert _canonical_ticket_contact_external_id(payload) == "group-contact"


@pytest.mark.asyncio
async def test_preparation_resolves_identity_before_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, Any]] = []

    async def load_cycle_contact(cycle_public_id: str) -> dict[str, Any]:
        calls.append(("load", cycle_public_id))
        return {
            "status": "completed",
            "digisac_contact_external_id": "ticket-contact",
            "digisac_contact_id": 42,
        }

    async def resolve_cycle_identity(cycle_public_id: str, contact_id: int) -> dict[str, Any]:
        calls.append(("identity", cycle_public_id, contact_id))
        return {
            "digisac_contact_id": contact_id,
            "state": "confirmed",
            "link_id": 7,
        }

    async def evaluate_department_mapping(cycle_public_id: str) -> dict[str, Any]:
        calls.append(("mapping", cycle_public_id))
        return {"state": "resolved", "reason": "mapping_validated", "id": 8}

    monkeypatch.setattr(preparation_module, "_load_cycle_contact", load_cycle_contact)
    monkeypatch.setattr(
        preparation_module, "resolve_cycle_identity", resolve_cycle_identity
    )
    monkeypatch.setattr(
        preparation_module, "evaluate_department_mapping", evaluate_department_mapping
    )

    result = await preparation_module.prepare_cycle_for_request("cycle-1")

    assert result.ready is True
    assert result.stage == "ready"
    assert [item[0] for item in calls] == ["load", "identity", "mapping"]


@pytest.mark.asyncio
async def test_unconfirmed_identity_blocks_mapping_and_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def load_cycle_contact(cycle_public_id: str) -> dict[str, Any]:
        return {
            "status": "completed",
            "digisac_contact_external_id": "ticket-contact",
            "digisac_contact_id": 42,
        }

    async def resolve_cycle_identity(cycle_public_id: str, contact_id: int) -> dict[str, Any]:
        calls.append("identity")
        return {"digisac_contact_id": contact_id, "state": "unresolved"}

    async def evaluate_department_mapping(cycle_public_id: str) -> dict[str, Any]:
        calls.append("mapping")
        raise AssertionError("mapping must not run for an unconfirmed identity")

    monkeypatch.setattr(preparation_module, "_load_cycle_contact", load_cycle_contact)
    monkeypatch.setattr(
        preparation_module, "resolve_cycle_identity", resolve_cycle_identity
    )
    monkeypatch.setattr(
        preparation_module, "evaluate_department_mapping", evaluate_department_mapping
    )

    result = await preparation_module.prepare_cycle_for_request("cycle-2")

    assert result.ready is False
    assert result.stage == "identity"
    assert result.reason == "unresolved"
    assert calls == ["identity"]


@pytest.mark.asyncio
async def test_worker_does_not_create_request_until_preparation_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = object.__new__(IAWorker)
    calls: list[str] = []

    async def blocked(cycle_public_id: str) -> Any:
        calls.append("prepare")
        return preparation_module.RequestPreparation(
            ready=False, stage="mapping", reason="mapping_rule_missing"
        )

    async def should_not_send(cycle_public_id: str) -> dict[str, Any]:
        calls.append("request")
        return {"state": "completed"}

    monkeypatch.setattr(ia_worker_module, "prepare_cycle_for_request", blocked)
    monkeypatch.setattr(ia_worker_module, "create_request_for_cycle", should_not_send)

    result = await worker._prepare_and_create_request("cycle-3")

    assert result is None
    assert calls == ["prepare"]


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_cycle_keeps_canonical_ticket_contact_provenance() -> None:
    await upsert_digisac_contact(
        DigisacContact(external_id="ticket-contact"), source="ticket_webhook"
    )
    cycle, _ = await create_open_cycle(
        conversation_id="provenance-ticket",
        started_at="2026-08-17T10:00:00Z",
        open_event_key="provenance-open",
        start_strategy="ticket_created_event",
        contact_external_id="ticket-contact",
    )

    closed, _ = await close_cycle(
        conversation_id="provenance-ticket",
        protocol="P-1",
        closed_at="2026-08-17T11:00:00Z",
        close_event_key="provenance-close",
        contact_external_id="ticket-contact",
    )

    assert closed["public_id"] == cycle["public_id"]
    assert closed["digisac_contact_external_id"] == "ticket-contact"


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_mapping_missing_recovery_prepares_then_uses_normal_request_path() -> None:
    conversation_id = "preparation-recovery"
    contact = await upsert_digisac_contact(
        DigisacContact(
            external_id="recovery-ticket-contact",
            raw_number="5511987654321",
            normalized_number="5511987654321",
            is_group=False,
        ),
        source="ticket_webhook",
    )
    await create_open_cycle(
        conversation_id=conversation_id,
        started_at="2026-08-17T09:00:00Z",
        open_event_key="preparation-recovery-open",
        start_strategy="ticket_created_event",
        contact_external_id="recovery-ticket-contact",
    )
    await record_ticket_assignment(
        conversation_id=conversation_id,
        department_id="recovery-digisac-department",
        user_id=None,
        event_timestamp="2026-08-17T10:00:00Z",
        event_key="preparation-recovery-assignment",
    )
    cycle, _ = await close_cycle(
        conversation_id=conversation_id,
        protocol="P-recovery",
        closed_at="2026-08-17T11:00:00Z",
        close_event_key="preparation-recovery-close",
        contact_external_id="recovery-ticket-contact",
    )
    classification = await insert_classification(
        conversation_id=conversation_id,
        message_ids=[],
        created_at="2026-08-17T11:01:00Z",
        full_context="safe context",
        message_count=0,
        result={
            "intent_type": "request",
            "confidence": 0.9,
            "title": "Recovery title",
            "description": "Recovery description",
        },
        model="test-model",
        processing_time_ms=1,
        prompt_version="test",
        idempotency_key="preparation-recovery-classification",
    )
    with psycopg.connect(settings.database_url) as connection:
        company = connection.execute(
            """
            INSERT INTO acessorias_companies (
                external_id, provider_id, legal_name, trade_name,
                is_present, is_active, synced_at
            ) VALUES ('recovery-company', 'recovery-provider', '', '', TRUE, TRUE, CURRENT_TIMESTAMP)
            RETURNING id
            """
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO acessorias_company_contacts (
                company_id, external_key, raw_mobile, normalized_mobile,
                is_present, is_active, synced_at
            ) VALUES (%s, 'recovery-contact', '5511987654321', '5511987654321', TRUE, TRUE, CURRENT_TIMESTAMP)
            """,
            (company,),
        )
        connection.execute(
            """
            INSERT INTO acessorias_departments (
                external_id, name, is_present, is_active, synced_at
            ) VALUES ('9091', 'Recovery', TRUE, TRUE, CURRENT_TIMESTAMP)
            """
        )
        department = connection.execute(
            """
            SELECT id FROM acessorias_departments
            WHERE external_id = '9091'
            """
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO acessorias_company_departments (
                company_id, department_id, is_present, is_active, synced_at
            ) VALUES (%s, %s, TRUE, TRUE, CURRENT_TIMESTAMP)
            """,
            (company, department),
        )
        connection.execute(
            """
            INSERT INTO digisac_departments (id, name, synced_at)
            VALUES ('recovery-digisac-department', 'Recovery', CURRENT_TIMESTAMP)
            """
        )
        connection.execute(
            """
            INSERT INTO department_mapping_rules (
                digisac_department_external_id,
                acessorias_department_external_id,
                version, state, source, reason, effective_at
            ) VALUES (
                'recovery-digisac-department',
                '9091',
                1, 'active', 'manual_db', 'approved_route', CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            UPDATE conversation_processing_cycles
            SET classification_id = %s, status = 'completed',
                completed_at = CURRENT_TIMESTAMP
            WHERE public_id = %s
            """,
            (classification.id, cycle["public_id"]),
        )
    await discover_identity(int(contact["id"]))
    await confirm_identity_link(int(contact["id"]), int(company), confirmed_at="2026-08-17T12:00:00Z")

    class Provider:
        calls = 0

        def create_request(self, payload: Any) -> AcessoriasRequestOutcome:
            self.calls += 1
            return AcessoriasRequestOutcome.success("SOL-recovered")

    provider = Provider()
    blocked = await create_request_for_cycle(str(cycle["public_id"]), provider=provider)
    assert blocked["state"] == "definitive_failure"
    assert blocked["failure_category"] == "mapping_missing"
    assert provider.calls == 0

    recovered = await recover_mapping_missing_request(
        str(cycle["public_id"]), provider=provider, owner="recovery-test"
    )

    assert recovered is not None
    assert recovered["state"] == "completed"
    assert recovered["sol_id"] == "SOL-recovered"
    assert provider.calls == 1
    with psycopg.connect(settings.database_url) as connection:
        recovery = connection.execute(
            "SELECT preparation_recovery_json FROM acessorias_request_operations"
        ).fetchone()
    assert recovery is not None
    assert recovery[0]["status"] == "prepared"

    replay = await recover_mapping_missing_request(
        str(cycle["public_id"]), provider=provider, owner="recovery-replay"
    )
    assert replay is not None
    assert replay["state"] == "completed"
    assert provider.calls == 1
