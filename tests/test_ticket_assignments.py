import logging

import psycopg
import pytest

from src.api.routes import capture_ticket_assignment
from src.core.config import settings
from src.core.db import (
    insert_classification,
    record_ticket_assignment,
    resolve_ticket_assignments,
    upsert_digisac_directory,
)
from src.workers.ia_worker import IAWorker

pytestmark = pytest.mark.postgres


async def _prepare():
    synced_at = "2026-07-24T10:00:00+00:00"
    await upsert_digisac_directory(
        "departments",
        [
            {"id": "dep-service", "name": "Atendimento"},
            {"id": "dep-fiscal", "name": "Departamento Fiscal"},
            {"id": "dep-ti", "name": "T.I."},
            {"id": "dep-people", "name": "Departamento Pessoal"},
        ],
        synced_at,
    )
    await upsert_digisac_directory(
        "users",
        [
            {"id": "user-jaqueline", "name": "Jaqueline Oliveira"},
            {"id": "user-carlos", "name": "Carlos Silva"},
            {"id": "user-maria", "name": "Maria Souza"},
            {"id": "user-guilherme", "name": "Guilherme Duarte"},
            {"id": "user-ana", "name": "Ana"},
        ],
        synced_at,
    )


async def _record(conversation_id, index, department_id, user_id, *, event_key=None):
    return await record_ticket_assignment(
        conversation_id=conversation_id,
        department_id=department_id,
        user_id=user_id,
        event_timestamp=f"2026-07-24T10:0{index}:00+00:00",
        event_key=event_key or f"{conversation_id}-{index}",
        ticket_transfer_count=index,
    )


@pytest.mark.asyncio
async def test_transfers_preserve_chronological_names():
    await _prepare()
    await _record("ticket-1", 1, "dep-service", "user-jaqueline")
    await _record("ticket-1", 2, "dep-fiscal", "user-carlos")
    await _record("ticket-1", 3, "dep-ti", "user-guilherme")
    assert await resolve_ticket_assignments("ticket-1") == (
        ["Atendimento", "Departamento Fiscal", "T.I."],
        ["Jaqueline Oliveira", "Carlos Silva", "Guilherme Duarte"],
        [],
        [],
    )


@pytest.mark.asyncio
async def test_duplicate_and_same_pair_events_are_idempotent():
    await _prepare()
    assert await _record("ticket-2", 1, "dep-fiscal", "user-carlos", event_key="same")
    assert not await _record("ticket-2", 1, "dep-fiscal", "user-carlos", event_key="same")
    assert not await _record("ticket-2", 2, "dep-fiscal", "user-carlos", event_key="new")
    await _record("ticket-2", 3, "dep-ti", "user-guilherme")
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM ticket_assignment_history WHERE conversation_id = 'ticket-2'"
        ).fetchone() == (2,)


@pytest.mark.asyncio
async def test_unresolved_ids_are_not_returned_as_names(monkeypatch, caplog):
    await _prepare()
    await _record("ticket-3", 1, "unknown-department", "unknown-user")
    worker = object.__new__(IAWorker)

    async def no_refresh(*, force=False):
        return False

    monkeypatch.setattr("src.workers.ia_worker.sync_digisac_directories", no_refresh)
    with caplog.at_level(logging.WARNING):
        departments, agents = await worker._resolve_assignment_names("ticket-3")
    assert departments == []
    assert agents == []
    assert "unknown-department" in caplog.text
    assert "unknown-user" in caplog.text


@pytest.mark.asyncio
async def test_webhook_assignment_and_jsonb_classification():
    await _prepare()
    payload = {
        "event": "ticket.updated",
        "eventId": "webhook-1",
        "data": {
            "id": "ticket-open",
            "isOpen": True,
            "departmentId": "dep-service",
            "userId": "user-ana",
            "updatedAt": "2026-07-24T10:00:00Z",
            "metrics": {"ticketTransferCount": 2},
        },
    }
    assert await capture_ticket_assignment(payload, payload["data"], "ticket-open")
    assert not await capture_ticket_assignment(payload, payload["data"], "ticket-open")
    await insert_classification(
        conversation_id="ticket-open",
        message_ids=[],
        created_at="2026-07-24T10:00:00+00:00",
        full_context="Cliente: dúvida",
        message_count=1,
        result={
            "intent_type": "question",
            "department": ["Atendimento"],
            "agent": ["Ana"],
        },
        model="test",
        processing_time_ms=1,
        prompt_version="test",
    )
    with psycopg.connect(settings.database_url) as connection:
        row = connection.execute(
            "SELECT ticket_transfer_count FROM ticket_assignment_history"
        ).fetchone()
        json_types = connection.execute(
            "SELECT pg_typeof(department)::text, pg_typeof(agent)::text FROM ia_classifications"
        ).fetchone()
    assert row == (2,)
    assert json_types == ("jsonb", "jsonb")
