import pytest
import psycopg

from src.core.config import settings
from src.core.db import insert_classification
from src.utils.backfill_ticket_assignments import backfill

pytestmark = pytest.mark.postgres


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


async def _classification(conversation_id, department=None, agent=None):
    await insert_classification(
        conversation_id=conversation_id,
        message_ids=[],
        created_at="2026-07-24T10:00:00+00:00",
        full_context="Cliente: dúvida",
        message_count=1,
        result={
            "intent_type": "question",
            "department": department or [],
            "agent": agent or [],
        },
        model="test",
        processing_time_ms=1,
        prompt_version="test",
    )


def _mock_api(monkeypatch, *, user_id="user-1"):
    monkeypatch.setattr(settings, "digisac_api_key", "test-key")
    monkeypatch.setattr(settings, "digisac_directory_max_retries", 1)

    def get(url, *, params, headers, timeout):
        if url.endswith("/departments"):
            return FakeResponse(
                {"data": [{"id": "dep-1", "name": "Fiscal"}], "currentPage": 1, "lastPage": 1}
            )
        if url.endswith("/users"):
            return FakeResponse(
                {"data": [{"id": "user-1", "name": "Carlos"}], "currentPage": 1, "lastPage": 1}
            )
        return FakeResponse({"departmentId": "dep-1", "userId": user_id})

    monkeypatch.setattr("src.utils.backfill_ticket_assignments.requests.get", get)


@pytest.mark.asyncio
async def test_backfill_dry_run_and_apply(monkeypatch):
    await _classification("ticket-empty")
    await _classification("ticket-preserved", ["Fiscal antigo"], ["Ana antiga"])
    _mock_api(monkeypatch)

    report = backfill(settings.database_url)
    assert report.scanned == 2
    assert report.would_update == 1
    assert report.updated == 0

    report = backfill(settings.database_url, apply=True)
    assert report.updated == 1
    with psycopg.connect(settings.database_url) as connection:
        rows = connection.execute(
            "SELECT conversation_id, department, agent FROM ia_classifications ORDER BY conversation_id"
        ).fetchall()
    assert rows == [
        ("ticket-empty", ["Fiscal"], ["Carlos"]),
        ("ticket-preserved", ["Fiscal antigo"], ["Ana antiga"]),
    ]


@pytest.mark.asyncio
async def test_backfill_reports_unassigned_agent_without_repeating_update(monkeypatch):
    await _classification("ticket-partial", ["Fiscal"], [])
    _mock_api(monkeypatch, user_id=None)
    report = backfill(settings.database_url)
    assert report.would_update == 0
    assert report.unresolved == 1
