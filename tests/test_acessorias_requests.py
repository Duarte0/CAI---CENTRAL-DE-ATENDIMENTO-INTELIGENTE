from __future__ import annotations

import asyncio
import time
from typing import Any

import psycopg
import pytest
import requests

from src.core.acessorias_requests import (
    AcessoriasRequestAdapter,
    AcessoriasRequestError,
    AcessoriasRequestOutcome,
    build_request_payload,
    create_request_for_cycle,
    reconcile_request_operation,
    release_request_operation,
)
from src.core.config import settings
from src.core.db import close_cycle, insert_classification


class FakeResponse:
    def __init__(self, payload: Any, *, status_code: int = 200, headers: dict[str, str] | None = None) -> None:
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> Any:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


async def seed_eligible_cycle(slug: str) -> str:
    cycle, _ = await close_cycle(
        conversation_id=f"request-{slug}",
        protocol="P-1",
        closed_at="2026-08-14T10:00:00Z",
        close_event_key=f"request-{slug}-close",
    )
    identity = await insert_classification(
        conversation_id=f"request-{slug}",
        message_ids=[],
        created_at="2026-08-14T10:01:00Z",
        full_context="safe context",
        message_count=0,
        result={
            "intent_type": "question",
            "confidence": 0.9,
            "title": "Persisted title",
            "description": "Persisted description",
        },
        model="test-model",
        processing_time_ms=1,
        prompt_version="test",
        idempotency_key=f"request-{slug}-classification",
    )
    with psycopg.connect(settings.database_url) as connection:
        company = connection.execute(
            """
            INSERT INTO acessorias_companies (
                external_id, provider_id, legal_name, trade_name,
                is_present, is_active, synced_at
            ) VALUES (%s, %s, '', '', TRUE, TRUE, CURRENT_TIMESTAMP)
            RETURNING id
            """,
            (f"company-{slug}", f"provider-{slug}"),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO acessorias_departments (
                external_id, name, is_present, is_active, synced_at
            ) VALUES (%s, 'Fiscal', TRUE, TRUE, CURRENT_TIMESTAMP)
            """,
            (f"10{len(slug)}",),
        )
        department_external_id = f"10{len(slug)}"
        department = connection.execute(
            """
            SELECT id FROM acessorias_departments WHERE external_id = %s
            """,
            (department_external_id,),
        ).fetchone()[0]
        digisac_department = f"digisac-{slug}"
        connection.execute(
            """
            INSERT INTO digisac_departments (id, name, synced_at)
            VALUES (%s, 'Fiscal', CURRENT_TIMESTAMP)
            """,
            (digisac_department,),
        )
        rule = connection.execute(
            """
            INSERT INTO department_mapping_rules (
                digisac_department_external_id,
                acessorias_department_external_id,
                version, state, source, reason, effective_at
            ) VALUES (%s, %s, 1, 'active', 'manual_db',
                      'approved_route', CURRENT_TIMESTAMP)
            RETURNING id
            """,
            (digisac_department, department_external_id),
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
            UPDATE conversation_processing_cycles
            SET classification_id = %s, status = 'completed', completed_at = CURRENT_TIMESTAMP
            WHERE public_id = %s
            """,
            (identity.id, cycle["public_id"]),
        )
        connection.execute(
            """
            INSERT INTO conversation_cycle_department_mappings (
                cycle_id, evaluation_key, rule_id, rule_version,
                digisac_department_external_id, acessorias_department_external_id,
                company_id, state, reason, validation_json, evaluated_at
            )
            SELECT id, 'default', %s, 1, %s, %s, %s,
                   'resolved', 'mapping_validated', '{}', CURRENT_TIMESTAMP
            FROM conversation_processing_cycles
            WHERE public_id = %s
            """,
            (
                rule,
                digisac_department,
                department_external_id,
                company,
                cycle["public_id"],
            ),
        )
    return str(cycle["public_id"])


def test_payload_is_bounded_and_contains_only_approved_fields() -> None:
    payload = build_request_payload(
        title="Título " + "x" * 120,
        description="Descrição persistida",
        company_external_id="company-1",
        department_external_id="10",
    )

    assert len(payload.subject) == 100
    assert payload.subject.startswith("Título")
    assert payload.form == {
        "assunto": payload.subject,
        "empresa": "company-1",
        "departamento": "10",
        "prioridade": "2",
        "descricao": "Descrição persistida",
        "tipo": "E",
    }
    assert set(payload.form) == {
        "assunto",
        "empresa",
        "departamento",
        "prioridade",
        "descricao",
        "tipo",
    }


def test_provider_success_uses_multipart_and_only_persists_id() -> None:
    session = FakeSession([FakeResponse({"id": "SOL-42", "msg": "created"})])
    adapter = AcessoriasRequestAdapter(
        base_url="https://api.example.test",
        token="secret-token",
        session=session,
        rate_limit_per_minute=100,
    )
    payload = build_request_payload(
        title="Title",
        description="Description",
        company_external_id="company-1",
        department_external_id="10",
    )

    outcome = adapter.create_request(payload)

    assert outcome.state == "completed"
    assert outcome.category == "provider_success"
    assert outcome.solid_id == "SOL-42"
    assert outcome.provider_status == 200
    assert session.calls[0][0] == "https://api.example.test/requests"
    assert set(session.calls[0][1]["files"]) == set(payload.form)
    assert all(value[0] is None for value in session.calls[0][1]["files"].values())
    assert session.calls[0][1]["headers"] == {"Authorization": "Bearer secret-token"}


@pytest.mark.parametrize(
    ("response", "category"),
    [
        (FakeResponse({"msg": "created"}), "missing_id"),
        (FakeResponse({"Erro": "validation failed"}, status_code=400), "provider_error"),
        (FakeResponse({"error": "temporary"}, status_code=500), "uncertain_5xx"),
    ],
)
def test_provider_never_treats_message_or_uncertain_status_as_success(
    response: FakeResponse, category: str
) -> None:
    adapter = AcessoriasRequestAdapter(
        token="secret-token",
        session=FakeSession([response]),
        max_attempts=1,
    )
    payload = build_request_payload(
        title="Title",
        description="Description",
        company_external_id="company-1",
        department_external_id="10",
    )

    outcome = adapter.create_request(payload)

    assert outcome.state in {"definitive_failure", "reconciliation_required"}
    assert outcome.category == category
    assert outcome.solid_id is None


def test_provider_retries_only_safe_rate_limit_and_bounds_pre_send_failure() -> None:
    sleeps: list[float] = []
    session = FakeSession(
        [
            FakeResponse({"Erro": "busy"}, status_code=429, headers={"Retry-After": "2"}),
            FakeResponse({"id": "SOL-42"}),
        ]
    )
    adapter = AcessoriasRequestAdapter(
        token="secret-token",
        session=session,
        sleep=sleeps.append,
        retry_base_seconds=0.0,
        retry_max_delay_seconds=5.0,
        retry_provider_margin_seconds=0.0,
    )
    payload = build_request_payload(
        title="Title",
        description="Description",
        company_external_id="company-1",
        department_external_id="10",
    )
    assert adapter.create_request(payload).solid_id == "SOL-42"
    assert len(session.calls) == 2
    assert sleeps == [2.0]

    failed = AcessoriasRequestAdapter(
        token="secret-token",
        session=FakeSession([requests.ConnectionError(), requests.ConnectionError()]),
        max_attempts=2,
        retry_base_seconds=0.0,
        retry_max_delay_seconds=1.0,
    )
    outcome = failed.create_request(payload)
    assert outcome.state == "retryable_failure"
    assert outcome.category == "pre_send_connection"


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_completed_cycle_creates_once_and_completed_replay_is_noop() -> None:
    cycle, _ = await close_cycle(
        conversation_id="request-cycle",
        protocol="P-1",
        closed_at="2026-08-14T10:00:00Z",
        close_event_key="request-cycle-close",
    )
    identity = await insert_classification(
        conversation_id="request-cycle",
        message_ids=[],
        created_at="2026-08-14T10:01:00Z",
        full_context="safe context",
        message_count=0,
        result={
            "intent_type": "question",
            "confidence": 0.9,
            "title": "Persisted title",
            "description": "Persisted description",
        },
        model="test-model",
        processing_time_ms=1,
        prompt_version="test",
        idempotency_key="request-cycle-classification",
    )
    with psycopg.connect(settings.database_url) as connection:
        company = connection.execute(
            """
            INSERT INTO acessorias_companies (
                external_id, provider_id, legal_name, trade_name,
                is_present, is_active, synced_at
            ) VALUES ('request-company', 'provider-company', '', '', TRUE, TRUE, CURRENT_TIMESTAMP)
            RETURNING id
            """
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO acessorias_departments (
                external_id, name, is_present, is_active, synced_at
            ) VALUES ('10', 'Fiscal', TRUE, TRUE, CURRENT_TIMESTAMP)
            """
        )
        department = connection.execute(
            "SELECT id FROM acessorias_departments WHERE external_id = '10'"
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO digisac_departments (id, name, synced_at)
            VALUES ('digisac-1', 'Fiscal', CURRENT_TIMESTAMP)
            """
        )
        rule = connection.execute(
            """
            INSERT INTO department_mapping_rules (
                digisac_department_external_id,
                acessorias_department_external_id,
                version, state, source, reason, effective_at
            ) VALUES ('digisac-1', '10', 1, 'active', 'manual_db',
                      'approved_route', CURRENT_TIMESTAMP)
            RETURNING id
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
            UPDATE conversation_processing_cycles
            SET classification_id = %s, status = 'completed', completed_at = CURRENT_TIMESTAMP
            WHERE public_id = %s
            """,
            (identity.id, cycle["public_id"]),
        )
        snapshot = connection.execute(
            """
            INSERT INTO conversation_cycle_department_mappings (
                cycle_id, evaluation_key, rule_id, rule_version,
                digisac_department_external_id, acessorias_department_external_id,
                company_id, state, reason, validation_json, evaluated_at
            )
            SELECT id, 'default', %s, 1, 'digisac-1', '10', %s,
                   'resolved', 'mapping_validated', '{}', CURRENT_TIMESTAMP
            FROM conversation_processing_cycles
            WHERE public_id = %s
            RETURNING id
            """,
            (rule, company, cycle["public_id"]),
        ).fetchone()
        assert snapshot is not None

    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        def create_request(self, payload: Any) -> AcessoriasRequestOutcome:
            self.calls += 1
            return AcessoriasRequestOutcome.success("SOL-1")

    provider = Provider()
    first = await create_request_for_cycle(str(cycle["public_id"]), provider=provider)
    second = await create_request_for_cycle(str(cycle["public_id"]), provider=provider)

    assert first["state"] == "completed"
    assert first["sol_id"] == "SOL-1"
    assert second["id"] == first["id"]
    assert provider.calls == 1
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM acessorias_request_operations"
        ).fetchone() == (1,)


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_uncertain_outcome_requires_manual_reconciliation() -> None:
    cycle, _ = await close_cycle(
        conversation_id="request-reconcile",
        protocol="P-1",
        closed_at="2026-08-14T10:00:00Z",
        close_event_key="request-reconcile-close",
    )
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            "UPDATE conversation_processing_cycles SET status = 'completed_with_warnings' WHERE public_id = %s",
            (cycle["public_id"],),
        )
    blocked = await create_request_for_cycle(
        str(cycle["public_id"]),
        provider=lambda payload: AcessoriasRequestOutcome.reconciliation("uncertain_test"),
    )
    assert blocked["state"] == "definitive_failure"
    assert blocked["failure_category"] == "classification_missing"

    with pytest.raises(AcessoriasRequestError, match="does not require reconciliation"):
        await reconcile_request_operation(
            str(cycle["public_id"]),
            solid_id="SOL-unknown",
            reason="verified_remote_request",
            operation_key="reconcile-missing-operation",
        )


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_uncertain_request_is_reconciled_or_released_only_with_explicit_evidence() -> None:
    cycle_id = await seed_eligible_cycle("uncertain")

    class UncertainProvider:
        def create_request(self, payload: Any) -> AcessoriasRequestOutcome:
            return AcessoriasRequestOutcome.reconciliation("uncertain_5xx")

    uncertain = await create_request_for_cycle(cycle_id, provider=UncertainProvider())
    assert uncertain["state"] == "reconciliation_required"
    reconciled = await reconcile_request_operation(
        cycle_id,
        solid_id="SOL-verified",
        reason="verified_remote_request",
        operation_key="reconcile-uncertain",
    )
    assert reconciled["state"] == "completed"
    assert reconciled["sol_id"] == "SOL-verified"
    assert (
        await reconcile_request_operation(
            cycle_id,
            solid_id="SOL-verified",
            reason="verified_remote_request",
            operation_key="reconcile-uncertain",
        )
    )["id"] == reconciled["id"]

    release_cycle_id = await seed_eligible_cycle("release")
    released = await create_request_for_cycle(
        release_cycle_id, provider=UncertainProvider()
    )
    with pytest.raises(ValueError, match="proof of remote absence"):
        await release_request_operation(
            release_cycle_id,
            reason="remote_absent",
            operation_key="release-without-proof",
        )
    released = await release_request_operation(
        release_cycle_id,
        reason="remote_absent",
        operation_key="release-after-proof",
        proof_of_absence=True,
    )
    assert released["state"] == "retryable_failure"

    class SuccessProvider:
        def create_request(self, payload: Any) -> AcessoriasRequestOutcome:
            return AcessoriasRequestOutcome.success("SOL-retried")

    assert (
        await create_request_for_cycle(
            release_cycle_id, provider=SuccessProvider()
        )
    )["sol_id"] == "SOL-retried"


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_concurrent_request_claims_issue_one_provider_post() -> None:
    cycle_id = await seed_eligible_cycle("concurrent")

    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        def create_request(self, payload: Any) -> AcessoriasRequestOutcome:
            self.calls += 1
            time.sleep(0.05)
            return AcessoriasRequestOutcome.success("SOL-concurrent")

    provider = Provider()
    results = await asyncio.gather(
        *[
            create_request_for_cycle(cycle_id, provider=provider)
            for _ in range(5)
        ]
    )
    assert provider.calls == 1
    assert {result["id"] for result in results} == {results[0]["id"]}
    completed = await create_request_for_cycle(cycle_id, provider=provider)
    assert completed["state"] == "completed"
    assert completed["sol_id"] == "SOL-concurrent"
