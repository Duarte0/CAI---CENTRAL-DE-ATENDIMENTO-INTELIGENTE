from __future__ import annotations

import logging
from typing import Any

import psycopg
import pytest
import requests

from src.core.acessorias_directory import (
    AcessoriasCompany,
    AcessoriasContact,
    AcessoriasDepartment,
    AcessoriasDirectoryAdapter,
    AcessoriasDirectoryError,
    AcessoriasSnapshot,
    _activity_from_status,
    normalize_email,
    normalize_mobile,
    sync_acessorias_directory_sync,
)
from src.core.config import settings


class FakeResponse:
    def __init__(
        self,
        payload: Any,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> Any:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def company_payload(
    *,
    identifier: str = "company-1",
    provider_id: int = 1,
    status: object = 1,
    department_id: int = 10,
    contacts: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "ID": provider_id,
        "Identificador": identifier,
        "Razao": "Empresa Teste",
        "Fantasia": "Teste",
        "Status": status,
        "Telefone": "1130000000",
        "UF": "SP",
        "ContatosNaEmpresa": contacts or [],
        "Departamentos": [{"ID": department_id, "Nome": "Fiscal"}],
    }


def test_normalization_keeps_raw_contract_separate() -> None:
    assert normalize_mobile(" +55 (١١) ") == "5511"
    assert normalize_mobile("  ") is None
    assert normalize_email("  User@Example.COM ") == "user@example.com"
    assert normalize_email("") is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [("Ativa", True), ("Ativo", True), ("Inativa", False), ("Não", False), ("unknown", None)],
)
def test_activity_accepts_provider_text_statuses(
    status: str, expected: bool | None
) -> None:
    assert _activity_from_status(status) is expected


def test_adapter_fetches_complete_pages_and_centralizes_bearer_header() -> None:
    session = FakeSession(
        [
            FakeResponse([{"ID": 10, "Nome": "Fiscal"}]),
            FakeResponse(
                [
                    company_payload(
                        contacts=[
                            {
                                "Nome": "Contato",
                                "E-mail": "  User@Example.COM ",
                                "Celular": " +55 (١١) ",
                            }
                        ]
                    )
                ]
            ),
            FakeResponse([]),
        ]
    )
    adapter = AcessoriasDirectoryAdapter(
        base_url="https://api.example.test",
        token="test-token",
        session=session,
        retry_base_seconds=0.0,
        retry_max_delay_seconds=1.0,
        rate_limit_per_minute=100,
    )

    snapshot = adapter.fetch_snapshot()

    assert [item.external_id for item in snapshot.departments] == ["10"]
    assert snapshot.page_count == 1
    assert snapshot.companies[0].contacts[0].normalized_mobile == "5511"
    assert snapshot.companies[0].contacts[0].normalized_email == "user@example.com"
    assert session.calls[1][1]["params"]["Pagina"] == 1
    assert session.calls[2][1]["params"]["Pagina"] == 2
    assert session.calls[0][1]["headers"] == {"Authorization": "Bearer test-token"}


@pytest.mark.parametrize(
    ("responses", "category"),
    [
        (
            [
                FakeResponse([{"ID": 10, "Nome": "Fiscal"}]),
                FakeResponse([company_payload()]),
                FakeResponse([company_payload()]),
            ],
            "pagination_loop",
        ),
        (
            [
                FakeResponse([{"ID": 10, "Nome": "Fiscal"}]),
                FakeResponse([company_payload(department_id=99)]),
                FakeResponse([]),
            ],
            "invalid_parent",
        ),
        (
            [FakeResponse({"not": "a list"})],
            "invalid_payload",
        ),
    ],
)
def test_adapter_rejects_partial_or_looping_snapshots(
    responses: list[FakeResponse | Exception], category: str
) -> None:
    adapter = AcessoriasDirectoryAdapter(
        token="test-token",
        session=FakeSession(responses),
        page_safety_limit=3,
        retry_base_seconds=0.0,
        retry_max_delay_seconds=1.0,
    )
    with pytest.raises(AcessoriasDirectoryError) as error:
        adapter.fetch_snapshot()
    assert error.value.category == category


def test_adapter_retries_429_with_retry_after_without_leaking_secret() -> None:
    sleeps: list[float] = []
    session = FakeSession(
        [
            FakeResponse([], status_code=429, headers={"Retry-After": "4"}),
            FakeResponse([{"ID": 10, "Nome": "Fiscal"}]),
            FakeResponse([]),
        ]
    )
    adapter = AcessoriasDirectoryAdapter(
        token="test-token",
        session=session,
        sleep=sleeps.append,
        retry_base_seconds=0.0,
        retry_max_delay_seconds=10.0,
        retry_provider_margin_seconds=1.0,
    )

    adapter.fetch_snapshot()

    assert sleeps == [5.0]
    assert adapter.token == "test-token"


def test_adapter_missing_credentials_fails_before_http() -> None:
    session = FakeSession([])
    adapter = AcessoriasDirectoryAdapter(token="", session=session)
    with pytest.raises(AcessoriasDirectoryError, match="not configured") as error:
        adapter.fetch_snapshot()
    assert error.value.category == "missing_credentials"
    assert session.calls == []


@pytest.mark.parametrize("status_code", [408, 425, 429, 500, 502, 503, 504])
def test_adapter_bounds_all_transient_http_statuses(status_code: int) -> None:
    session = FakeSession(
        [
            FakeResponse([], status_code=status_code),
            FakeResponse([{"ID": 10, "Nome": "Fiscal"}]),
            FakeResponse([]),
        ]
    )
    adapter = AcessoriasDirectoryAdapter(
        token="test-token",
        session=session,
        max_attempts=2,
        retry_base_seconds=0.0,
        retry_max_delay_seconds=1.0,
        retry_provider_margin_seconds=0.0,
        sleep=lambda _delay: None,
    )

    snapshot = adapter.fetch_snapshot()

    assert snapshot.departments[0].external_id == "10"
    assert adapter.request_attempt_count == 3


def test_adapter_bounds_connection_retries_and_rejects_authentication() -> None:
    session = FakeSession(
        [requests.Timeout(), FakeResponse([{"ID": 10, "Nome": "Fiscal"}]), FakeResponse([])]
    )
    adapter = AcessoriasDirectoryAdapter(
        token="test-token",
        session=session,
        max_attempts=2,
        retry_base_seconds=0.0,
        retry_max_delay_seconds=1.0,
        sleep=lambda _delay: None,
    )
    assert adapter.fetch_snapshot().departments[0].external_id == "10"

    auth_adapter = AcessoriasDirectoryAdapter(
        token="test-token",
        session=FakeSession([FakeResponse({}, status_code=401)]),
    )
    with pytest.raises(AcessoriasDirectoryError) as error:
        auth_adapter.fetch_snapshot()
    assert error.value.category == "authentication"


def test_adapter_fails_at_page_safety_limit_instead_of_publishing_partial_data() -> None:
    adapter = AcessoriasDirectoryAdapter(
        token="test-token",
        page_safety_limit=1,
        session=FakeSession(
            [
                FakeResponse([{"ID": 10, "Nome": "Fiscal"}]),
                FakeResponse([company_payload()]),
            ]
        ),
    )
    with pytest.raises(AcessoriasDirectoryError) as error:
        adapter.fetch_snapshot()
    assert error.value.category == "pagination_limit"


def _snapshot(*, include_company: bool = True, active: bool | None = True) -> AcessoriasSnapshot:
    department = AcessoriasDepartment("10", "Fiscal", "Resp", "resp@example.test")
    company = AcessoriasCompany(
        external_id="company-1",
        provider_id="1",
        legal_name="Empresa Teste",
        trade_name="Teste",
        provider_status=str(int(active)) if active is not None else "unknown",
        phone="1130000000",
        uf="SP",
        client_since=None,
        client_until=None,
        registered_at=None,
        contacts=(
            AcessoriasContact(
                name="Contato",
                raw_email="Raw@Example.Test",
                normalized_email="raw@example.test",
                raw_mobile="(11) 9999",
                normalized_mobile="119999",
                external_key="contact-1",
            ),
        ),
        department_ids=("10",),
        is_active=active,
    )
    return AcessoriasSnapshot(
        departments=(department,),
        companies=(company,) if include_company else (),
        page_count=1,
        request_attempt_count=3,
    )


class StaticProvider:
    def __init__(self, snapshot: AcessoriasSnapshot) -> None:
        self.snapshot = snapshot

    def fetch_snapshot(self) -> AcessoriasSnapshot:
        return self.snapshot


@pytest.mark.postgres
def test_valid_snapshot_is_idempotent_and_absence_reactivates() -> None:
    first = sync_acessorias_directory_sync(adapter=StaticProvider(_snapshot()))
    repeated = sync_acessorias_directory_sync(adapter=StaticProvider(_snapshot()))
    absent = sync_acessorias_directory_sync(
        adapter=StaticProvider(_snapshot(include_company=False))
    )
    restored = sync_acessorias_directory_sync(adapter=StaticProvider(_snapshot()))

    assert first.status == "succeeded"
    assert repeated.status == "deduplicated"
    assert absent.status == "succeeded"
    assert restored.status == "deduplicated"
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute("SELECT COUNT(*) FROM acessorias_companies").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM acessorias_company_contacts").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM acessorias_company_departments").fetchone() == (1,)
        assert connection.execute(
            "SELECT is_present, is_active FROM acessorias_companies WHERE external_id = 'company-1'"
        ).fetchone() == (True, True)
        assert connection.execute(
            "SELECT COUNT(*) FROM acessorias_directory_sync_executions WHERE status = 'succeeded'"
        ).fetchone() == (2,)
        contact = connection.execute(
            "SELECT raw_mobile, normalized_mobile, raw_email, normalized_email "
            "FROM acessorias_company_contacts"
        ).fetchone()
        assert contact == ("(11) 9999", "119999", "Raw@Example.Test", "raw@example.test")


@pytest.mark.postgres
def test_failed_snapshot_preserves_last_success_and_sanitizes_state(caplog: pytest.LogCaptureFixture) -> None:
    successful = sync_acessorias_directory_sync(adapter=StaticProvider(_snapshot()))
    bad = AcessoriasSnapshot(
        departments=(),
        companies=(
            AcessoriasCompany(
                external_id="company-1",
                provider_id="1",
                legal_name="",
                trade_name="",
                provider_status="1",
                phone=None,
                uf=None,
                client_since=None,
                client_until=None,
                registered_at=None,
                contacts=(),
                department_ids=("missing",),
                is_active=True,
            ),
        ),
        page_count=1,
        request_attempt_count=1,
    )
    with caplog.at_level(logging.WARNING):
        failed = sync_acessorias_directory_sync(adapter=StaticProvider(bad))

    assert successful.status == "succeeded"
    assert failed.status == "failed"
    assert failed.failure_category == "invalid_parent"
    assert "Raw@Example.Test" not in caplog.text
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT is_present FROM acessorias_companies WHERE external_id = 'company-1'"
        ).fetchone() == (True,)
        assert connection.execute(
            "SELECT status, failure_category, failure_message "
            "FROM acessorias_directory_sync_executions WHERE execution_id = %s",
            (failed.execution_id,),
        ).fetchone() == ("failed", "invalid_parent", "company department has no parent")


@pytest.mark.postgres
def test_concurrent_publication_is_excluded_by_postgres_advisory_lock() -> None:
    with psycopg.connect(settings.database_url) as lock_connection:
        lock_connection.execute(
            "SELECT pg_advisory_lock(hashtext('cai:acessorias-directory'))"
        )
        blocked = sync_acessorias_directory_sync(adapter=StaticProvider(_snapshot()))
        lock_connection.execute(
            "SELECT pg_advisory_unlock(hashtext('cai:acessorias-directory'))"
        )

    assert blocked.status == "failed"
    assert blocked.failure_category == "refresh_in_progress"
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute("SELECT COUNT(*) FROM acessorias_companies").fetchone() == (0,)
