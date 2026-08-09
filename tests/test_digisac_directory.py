import pytest
import requests

from src.core.config import settings
from src.core.db import resolve_ticket_assignments, upsert_digisac_directory
from src.core.digisac_directory import sync_digisac_directories
from src.core.db import record_ticket_assignment

pytestmark = pytest.mark.postgres


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


@pytest.mark.asyncio
async def test_directory_sync_paginates_and_updates_names(monkeypatch):
    monkeypatch.setattr(settings, "digisac_api_key", "test-key")
    names = {"department": "Fiscal", "user": "Carlos"}
    calls = []

    def get(url, *, params, headers, timeout):
        assert headers == {"Authorization": "Bearer test-key"}
        resource = url.rsplit("/", 1)[-1]
        page = params["page"]
        calls.append((resource, page))
        if resource == "departments":
            return FakeResponse(
                {
                    "data": [{"id": "dep-1", "name": names["department"]}],
                    "currentPage": 1,
                    "lastPage": 1,
                }
            )
        return FakeResponse(
            {
                "data": ([{"id": "user-other", "name": "Ana"}]
                         if page == 1 else [{"id": "user-1", "name": names["user"]}]),
                "currentPage": page,
                "lastPage": 2,
            }
        )

    monkeypatch.setattr("src.core.digisac_directory.requests.get", get)
    assert await sync_digisac_directories(force=True)
    assert calls == [("departments", 1), ("users", 1), ("users", 2)]
    await record_ticket_assignment(
        conversation_id="ticket-1",
        department_id="dep-1",
        user_id="user-1",
        event_timestamp="2026-07-24T10:00:00+00:00",
        event_key="event-1",
    )
    assert (await resolve_ticket_assignments("ticket-1"))[:2] == (["Fiscal"], ["Carlos"])
    names.update(department="Departamento Fiscal", user="Carlos Silva")
    assert await sync_digisac_directories(force=True)
    assert (await resolve_ticket_assignments("ticket-1"))[:2] == (
        ["Departamento Fiscal"], ["Carlos Silva"]
    )


@pytest.mark.asyncio
async def test_directory_sync_retries_transient_failure(monkeypatch):
    monkeypatch.setattr(settings, "digisac_api_key", "test-key")
    monkeypatch.setattr(settings, "digisac_directory_max_retries", 2)
    monkeypatch.setattr("src.core.digisac_directory.time.sleep", lambda _delay: None)
    attempts = {"departments": 0}

    def get(url, *, params, headers, timeout):
        resource = url.rsplit("/", 1)[-1]
        if resource == "departments":
            attempts[resource] += 1
            if attempts[resource] == 1:
                raise requests.Timeout("temporary")
        return FakeResponse({"data": [], "currentPage": params["page"], "lastPage": 1})

    monkeypatch.setattr("src.core.digisac_directory.requests.get", get)
    assert await sync_digisac_directories(force=True)
    assert attempts["departments"] == 2


@pytest.mark.asyncio
async def test_directory_upsert_rejects_unknown_resource():
    with pytest.raises(ValueError):
        await upsert_digisac_directory("other", [], "2026-07-24T10:00:00+00:00")
