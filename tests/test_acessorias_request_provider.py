from __future__ import annotations

from typing import Any

import src.core.acessorias_request_provider as provider_module
import src.core.acessorias_requests as durable_module
from src.core.acessorias_request_provider import (
    AcessoriasRequestAdapter,
    AcessoriasRequestOutcome,
    AcessoriasRequestPayload,
    AcessoriasRequestPreSendError,
    AcessoriasRequestProvider,
    build_request_payload,
)


class FakeResponse:
    status_code = 201
    headers: dict[str, str] = {}

    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def json(self) -> Any:
        return self.payload


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        return FakeResponse({"id": "SOL-provider-boundary"})


def test_durable_module_reexports_provider_contract_without_duplicating_it() -> None:
    for name in (
        "AcessoriasRequestAdapter",
        "AcessoriasRequestOutcome",
        "AcessoriasRequestPayload",
        "AcessoriasRequestPreSendError",
        "AcessoriasRequestProvider",
        "build_request_payload",
    ):
        assert getattr(durable_module, name) is getattr(provider_module, name)

    assert AcessoriasRequestAdapter.__module__ == provider_module.__name__
    assert AcessoriasRequestOutcome.__module__ == provider_module.__name__
    assert AcessoriasRequestPayload.__module__ == provider_module.__name__
    assert AcessoriasRequestPreSendError.__module__ == provider_module.__name__
    assert AcessoriasRequestProvider.__module__ == provider_module.__name__
    assert not hasattr(provider_module, "get_database_pool")
    assert not hasattr(provider_module, "psycopg")


def test_provider_boundary_preserves_multipart_request_contract() -> None:
    session = FakeSession()
    payload = build_request_payload(
        protocol="20260820-1",
        title="Persisted title",
        description="Persisted description",
        company_external_id="company-1",
        department_external_id="10",
    )

    outcome = AcessoriasRequestAdapter(
        base_url="https://api.example.test",
        token="test-token",
        session=session,
        rate_limit_per_minute=100,
    ).create_request(payload)

    assert outcome == AcessoriasRequestOutcome.success(
        "SOL-provider-boundary", provider_status=201
    )
    assert session.calls[0][0] == "https://api.example.test/requests"
    assert set(session.calls[0][1]["files"]) == set(payload.form)
    assert session.calls[0][1]["headers"] == {"Authorization": "Bearer test-token"}
