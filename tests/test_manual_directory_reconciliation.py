from __future__ import annotations

import psycopg
import pytest
from typing import Any, cast

from src.core.acessorias_directory import (
    AcessoriasCompany,
    AcessoriasContact,
    AcessoriasDepartment,
    AcessoriasDirectoryAdapter,
    AcessoriasDirectoryError,
    AcessoriasSnapshot,
    require_complete_acessorias_snapshot,
)
from src.core.digisac_acessorias_reconciliation import (
    run_manual_reconciliation,
)
from src.core.config import settings
from src.core.digisac_client import DigisacContact, DigisacContactPage
from src.core.digisac_contact_backfill import DigisacContactBackfillSnapshot


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def json(self) -> object:
        return self.payload


class _Session:
    def __init__(self) -> None:
        self.responses = [
            _Response([{"ID": 10, "Nome": "Fiscal"}]),
            _Response(
                [
                    {
                        "ID": 1,
                        "Identificador": "company-1",
                        "Razao": "Empresa",
                        "Fantasia": "Empresa",
                        "Status": "Ativa",
                        "Telefone": None,
                        "UF": "SP",
                        "ContatosNaEmpresa": [],
                        "Departamentos": [{"ID": 10, "Nome": "Fiscal"}],
                    }
                ]
            ),
            _Response([]),
        ]

    def get(self, _url: str, **_kwargs: object) -> _Response:
        return self.responses.pop(0)


class _StaticAcessoriasProvider:
    def __init__(self, snapshot: AcessoriasSnapshot) -> None:
        self.snapshot = snapshot

    def fetch_snapshot(self) -> AcessoriasSnapshot:
        return self.snapshot


class _StaticDigiSacProvider:
    def __init__(self, contact: DigisacContact) -> None:
        self.contact = contact

    def get_contacts_page(
        self, *, page: int, per_page: int
    ) -> DigisacContactPage:
        assert page == 1
        assert per_page == 100
        return DigisacContactPage(
            contacts=(self.contact,),
            total=1,
            limit=100,
            current_page=1,
            last_page=1,
        )


def _snapshot(*, complete_view: bool) -> AcessoriasSnapshot:
    return AcessoriasSnapshot(
        departments=(AcessoriasDepartment("10", "Fiscal"),),
        companies=(
            AcessoriasCompany(
                external_id="company-1",
                provider_id="1",
                legal_name="Empresa",
                trade_name="Empresa",
                provider_status="Ativa",
                phone=None,
                uf="SP",
                client_since=None,
                client_until=None,
                registered_at=None,
                contacts=(
                    AcessoriasContact(
                        name="Responsável",
                        raw_email="responsavel@example.test",
                        normalized_email="responsavel@example.test",
                        raw_mobile=None,
                        normalized_mobile=None,
                        external_key="contact-1",
                    ),
                ),
                department_ids=("10",),
                is_active=True,
            ),
        ),
        page_count=1,
        request_attempt_count=1,
        complete_view=complete_view,
    )


def _contacts_snapshot() -> DigisacContactBackfillSnapshot:
    return DigisacContactBackfillSnapshot(
        contacts=(DigisacContact(external_id="digisac-1"),),
        page_count=1,
        duplicate_count=0,
        total=1,
    )


def test_observed_active_only_view_is_not_accepted_as_complete() -> None:
    adapter = AcessoriasDirectoryAdapter(
        token="synthetic-token",
        session=cast(Any, _Session()),
        retry_base_seconds=0.0,
        retry_max_delay_seconds=1.0,
    )

    snapshot = adapter.fetch_snapshot()

    assert snapshot.complete_view is False
    with pytest.raises(AcessoriasDirectoryError) as error:
        require_complete_acessorias_snapshot(snapshot)
    assert error.value.category == "incomplete_source_view"


@pytest.mark.asyncio
async def test_reconciliation_rejects_incomplete_source_before_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.core.digisac_acessorias_reconciliation as reconciliation

    started: list[tuple[object, object]] = []
    finished: list[tuple[object, object, object]] = []

    def start(execution_id: object, mode: object) -> None:
        started.append((execution_id, mode))

    def finish(
        execution_id: object,
        *,
        status: object,
        report: object,
        failure_category: object = None,
        failure_message: object = None,
    ) -> None:
        finished.append((execution_id, status, failure_category))

    async def acquire(**_kwargs: object):
        return _snapshot(complete_view=False), _contacts_snapshot()

    monkeypatch.setattr(reconciliation, "_start_execution", start)
    monkeypatch.setattr(reconciliation, "_finish_execution", finish)
    monkeypatch.setattr(reconciliation, "_acquire_snapshots", acquire)

    result = await run_manual_reconciliation(apply=True)

    assert result.status == "failed"
    assert result.failure_category == "incomplete_source_view"
    assert started and finished[-1][2] == "incomplete_source_view"


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_reconciliation_dry_run_apply_and_replay_are_idempotent() -> None:
    acessorias = _snapshot(complete_view=True)
    digisac = DigisacContact(
        external_id="digisac-1",
        raw_email="responsavel@example.test",
        normalized_email="responsavel@example.test",
        is_group=False,
    )
    acessorias_provider = _StaticAcessoriasProvider(acessorias)
    digisac_provider = _StaticDigiSacProvider(digisac)
    database_url = settings.database_url
    assert database_url

    preview = await run_manual_reconciliation(
        acessorias_provider=acessorias_provider,
        digisac_provider=digisac_provider,
        per_page=100,
    )
    assert preview.status == "dry_run"
    assert preview.report["delta"]["new_count"] == 5

    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT COUNT(*) FROM acessorias_companies").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM digisac_contacts").fetchone() == (0,)

    applied = await run_manual_reconciliation(
        apply=True,
        acessorias_provider=acessorias_provider,
        digisac_provider=digisac_provider,
        per_page=100,
    )
    replayed = await run_manual_reconciliation(
        apply=True,
        acessorias_provider=acessorias_provider,
        digisac_provider=digisac_provider,
        per_page=100,
    )

    assert applied.status == "succeeded"
    assert applied.report["identity"]["candidate_count"] == 1
    assert replayed.status == "succeeded"
    assert replayed.report["delta"]["new_count"] == 0
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT COUNT(*) FROM acessorias_companies").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM acessorias_company_contacts").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM acessorias_company_departments").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM digisac_contacts").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM identity_company_links").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM identity_match_evidence").fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM digisac_acessorias_reconciliation_executions"
        ).fetchone() == (3,)
