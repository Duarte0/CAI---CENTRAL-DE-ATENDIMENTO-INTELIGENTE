from __future__ import annotations

from typing import Any

import pytest
import requests

from src.core.digisac_client import (
    DigisacClient,
    DigisacClientError,
    DigisacResponseError,
)


class FakeResponse:
    def __init__(
        self,
        payload: Any,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def client(responses):
    return DigisacClient(
        base_url="https://example.test/api/v1",
        api_key="secret",
        timeout_seconds=1,
        max_attempts=3,
        retry_base_seconds=0.001,
        session=FakeSession(responses),
    )


def ticket(last_message_id="m2"):
    return FakeResponse({"id": "ticket", "lastMessageId": last_message_id})


def page(number, last, total, messages):
    return FakeResponse(
        {
            "data": messages,
            "currentPage": number,
            "lastPage": last,
            "total": total,
        }
    )


def test_fetches_all_pages_and_deduplicates_in_api_order():
    instance = client(
        [
            ticket(),
            page(1, 2, 2, [{"id": "m1"}, {"id": "m2"}]),
            page(2, 2, 2, [{"id": "m2"}]),
        ]
    )
    history = instance.get_ticket_history("ticket")
    assert [item["id"] for item in history.messages] == ["m1", "m2"]
    assert history.page_count == 2
    assert history.duplicate_count == 1
    assert history.complete is True
    assert instance.session.calls[1][1]["params"]["where[ticketId]"] == "ticket"
    assert instance.session.calls[2][1]["params"]["page"] == 2


def test_marks_missing_last_message_as_incomplete():
    history = client(
        [ticket("missing"), page(1, 1, 1, [{"id": "m1"}])]
    ).get_ticket_history("ticket")
    assert history.complete is False
    assert "last_message_id_missing" in history.consistency_reasons


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"data": "invalid", "currentPage": 1, "lastPage": 1, "total": 0},
        {"data": [], "currentPage": 2, "lastPage": 1, "total": 0},
    ],
)
def test_rejects_invalid_payloads(payload):
    instance = client([ticket(None), FakeResponse(payload)])
    with pytest.raises(DigisacResponseError):
        instance.get_ticket_history("ticket")


def test_retries_429_and_honors_retry_after(monkeypatch):
    sleeps = []
    monkeypatch.setattr("src.core.digisac_client.time.sleep", sleeps.append)
    instance = client(
        [
            FakeResponse({}, status_code=429, headers={"Retry-After": "0"}),
            ticket(None),
            page(1, 1, 0, []),
        ]
    )
    assert instance.get_ticket_history("ticket").complete is True
    assert sleeps == [0.0]


def test_retries_timeout_and_500(monkeypatch):
    monkeypatch.setattr("src.core.digisac_client.time.sleep", lambda _value: None)
    instance = client(
        [
            requests.Timeout(),
            FakeResponse({}, status_code=500),
            ticket(None),
            page(1, 1, 0, []),
        ]
    )
    assert instance.get_ticket_history("ticket").complete is True


def test_permanent_http_error_is_not_retried():
    instance = client([FakeResponse({}, status_code=404)])
    with pytest.raises(DigisacClientError, match="permanent HTTP 404"):
        instance.get_ticket_history("ticket")
    assert len(instance.session.calls) == 1
