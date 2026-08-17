from __future__ import annotations

import asyncio
import logging

import psycopg
import pytest

from src.core.config import settings
from src.core.db import (
    close_cycle,
    create_open_cycle,
    record_ticket_assignment,
    transition_cycle,
    upsert_digisac_contact,
)
from src.core.digisac_client import DigisacContact
from src.core.department_mapping import (
    DepartmentMappingConflictError,
    configure_department_mapping,
    evaluate_department_mapping,
)
from src.core.identity_resolution import (
    confirm_identity_link,
    resolve_cycle_identity,
)

pytestmark = pytest.mark.postgres


def create_digisac_department(external_id: str, name: str = "DigiSac") -> None:
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            """
            INSERT INTO digisac_departments (id, name, synced_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            """,
            (external_id, name),
        )


def create_acessorias_company(external_id: str) -> int:
    with psycopg.connect(settings.database_url) as connection:
        row = connection.execute(
            """
            INSERT INTO acessorias_companies (
                external_id, provider_id, legal_name, trade_name,
                is_present, is_active, synced_at
            ) VALUES (%s, %s, '', '', TRUE, TRUE, CURRENT_TIMESTAMP)
            RETURNING id
            """,
            (external_id, f"provider-{external_id}"),
        ).fetchone()
    assert row is not None
    return int(row[0])


def create_acessorias_department(
    external_id: str, *, active: bool = True, name: str = "Acessórias"
) -> None:
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            """
            INSERT INTO acessorias_departments (
                external_id, name, is_present, is_active, synced_at
            ) VALUES (%s, %s, TRUE, %s, CURRENT_TIMESTAMP)
            """,
            (external_id, name, active),
        )


def create_company_department(
    company_id: int, department_external_id: str, *, active: bool = True
) -> None:
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            """
            INSERT INTO acessorias_company_departments (
                company_id, department_id, is_present, is_active, synced_at
            )
            SELECT %s, id, TRUE, %s, CURRENT_TIMESTAMP
            FROM acessorias_departments
            WHERE external_id = %s
            """,
            (company_id, active, department_external_id),
        )


async def create_confirmed_cycle(
    *,
    conversation_id: str,
    digisac_department_external_id: str,
    company_id: int | None,
    contact_external_id: str,
) -> str:
    await create_open_cycle(
        conversation_id=conversation_id,
        started_at="2026-08-14T09:00:00Z",
        open_event_key=f"{conversation_id}-open",
        start_strategy="department_mapping_test",
    )
    await record_ticket_assignment(
        conversation_id=conversation_id,
        department_id=digisac_department_external_id,
        user_id=None,
        event_timestamp="2026-08-14T10:00:00Z",
        event_key=f"{conversation_id}-assignment",
    )
    contact = await upsert_digisac_contact(
        DigisacContact(external_id=contact_external_id, is_group=False),
        source="department_mapping_test",
    )
    cycle, _ = await close_cycle(
        conversation_id=conversation_id,
        protocol="protocol",
        closed_at="2026-08-14T11:00:00Z",
        close_event_key=f"{conversation_id}-close",
    )
    if company_id is not None:
        await confirm_identity_link(
            int(contact["id"]), company_id, confirmed_at="2026-08-14T12:00:00Z"
        )
    await resolve_cycle_identity(str(cycle["public_id"]), int(contact["id"]))
    return str(cycle["public_id"])


@pytest.mark.asyncio
async def test_mapping_uses_the_assignment_from_the_selected_cycle_after_reopen() -> None:
    create_digisac_department("digisac-old")
    create_digisac_department("digisac-new")
    company_id = create_acessorias_company("company-reopen")
    create_acessorias_department("acessorias-old")
    create_acessorias_department("acessorias-new")
    create_company_department(company_id, "acessorias-old")
    create_company_department(company_id, "acessorias-new")
    await configure_department_mapping(
        "digisac-old",
        "acessorias-old",
        reason="approved_route",
        operation_key="map-reopen-old",
    )
    await configure_department_mapping(
        "digisac-new",
        "acessorias-new",
        reason="approved_route",
        operation_key="map-reopen-new",
    )
    contact = await upsert_digisac_contact(
        DigisacContact(external_id="mapping-contact-reopen", is_group=False),
        source="department_mapping_test",
    )

    await create_open_cycle(
        conversation_id="mapping-reopen",
        started_at="2026-08-14T10:00:00Z",
        open_event_key="mapping-reopen-open-1",
        start_strategy="department_mapping_test",
    )
    await record_ticket_assignment(
        conversation_id="mapping-reopen",
        department_id="digisac-old",
        user_id=None,
        event_timestamp="2026-08-14T10:05:00Z",
        event_key="mapping-reopen-assignment-old",
    )
    first_cycle, _ = await close_cycle(
        conversation_id="mapping-reopen",
        protocol="first",
        closed_at="2026-08-14T11:00:00Z",
        close_event_key="mapping-reopen-close-1",
    )
    await confirm_identity_link(
        int(contact["id"]), company_id, confirmed_at="2026-08-14T11:30:00Z"
    )
    await resolve_cycle_identity(str(first_cycle["public_id"]), int(contact["id"]))

    await create_open_cycle(
        conversation_id="mapping-reopen",
        started_at="2026-08-14T12:00:00Z",
        open_event_key="mapping-reopen-open-2",
        start_strategy="department_mapping_test",
    )
    await record_ticket_assignment(
        conversation_id="mapping-reopen",
        department_id="digisac-new",
        user_id=None,
        event_timestamp="2026-08-14T12:05:00Z",
        event_key="mapping-reopen-assignment-new",
    )
    second_cycle, _ = await close_cycle(
        conversation_id="mapping-reopen",
        protocol="second",
        closed_at="2026-08-14T13:00:00Z",
        close_event_key="mapping-reopen-close-2",
    )
    await resolve_cycle_identity(str(second_cycle["public_id"]), int(contact["id"]))

    with psycopg.connect(settings.database_url) as connection:
        assignment_ids = connection.execute(
            """
            SELECT event_key, id
            FROM ticket_assignment_history
            WHERE event_key IN (%s, %s)
            """,
            ("mapping-reopen-assignment-old", "mapping-reopen-assignment-new"),
        ).fetchall()
    assignment_id_by_key = {row[0]: int(row[1]) for row in assignment_ids}

    first_result = await evaluate_department_mapping(str(first_cycle["public_id"]))
    second_result = await evaluate_department_mapping(str(second_cycle["public_id"]))

    assert first_result["state"] == "resolved"
    assert first_result["digisac_department_external_id"] == "digisac-old"
    assert first_result["acessorias_department_external_id"] == "acessorias-old"
    assert first_result["validation_json"]["cycle_started_at"] == (
        "2026-08-14T10:00:00+00:00"
    )
    assert first_result["validation_json"]["ticket_closed_at"] == (
        "2026-08-14T11:00:00+00:00"
    )
    assert first_result["validation_json"]["assignment_history_id"] == assignment_id_by_key[
        "mapping-reopen-assignment-old"
    ]
    assert second_result["state"] == "resolved"
    assert second_result["digisac_department_external_id"] == "digisac-new"
    assert second_result["acessorias_department_external_id"] == "acessorias-new"
    assert second_result["validation_json"]["assignment_history_id"] == assignment_id_by_key[
        "mapping-reopen-assignment-new"
    ]
    later_evaluation = await evaluate_department_mapping(
        str(first_cycle["public_id"]), evaluation_key="after-reopen"
    )
    assert later_evaluation["digisac_department_external_id"] == "digisac-old"
    assert later_evaluation["validation_json"]["assignment_history_id"] == (
        assignment_id_by_key["mapping-reopen-assignment-old"]
    )


@pytest.mark.asyncio
async def test_mapping_uses_assignment_id_as_tiebreaker_within_cycle() -> None:
    create_digisac_department("digisac-tie-first")
    create_digisac_department("digisac-tie-last")
    company_id = create_acessorias_company("company-tie")
    create_acessorias_department("acessorias-tie-first")
    create_acessorias_department("acessorias-tie-last")
    create_company_department(company_id, "acessorias-tie-first")
    create_company_department(company_id, "acessorias-tie-last")
    await configure_department_mapping(
        "digisac-tie-first",
        "acessorias-tie-first",
        reason="approved_route",
        operation_key="map-tie-first",
    )
    await configure_department_mapping(
        "digisac-tie-last",
        "acessorias-tie-last",
        reason="approved_route",
        operation_key="map-tie-last",
    )
    contact = await upsert_digisac_contact(
        DigisacContact(external_id="mapping-contact-tie", is_group=False),
        source="department_mapping_test",
    )
    await create_open_cycle(
        conversation_id="mapping-tie",
        started_at="2026-08-14T10:00:00Z",
        open_event_key="mapping-tie-open",
        start_strategy="department_mapping_test",
    )
    await record_ticket_assignment(
        conversation_id="mapping-tie",
        department_id="digisac-tie-first",
        user_id=None,
        event_timestamp="2026-08-14T10:05:00Z",
        event_key="mapping-tie-first",
    )
    await record_ticket_assignment(
        conversation_id="mapping-tie",
        department_id="digisac-tie-last",
        user_id=None,
        event_timestamp="2026-08-14T10:05:00Z",
        event_key="mapping-tie-last",
    )
    cycle, _ = await close_cycle(
        conversation_id="mapping-tie",
        protocol="tie",
        closed_at="2026-08-14T11:00:00Z",
        close_event_key="mapping-tie-close",
    )
    await confirm_identity_link(
        int(contact["id"]), company_id, confirmed_at="2026-08-14T11:30:00Z"
    )
    await resolve_cycle_identity(str(cycle["public_id"]), int(contact["id"]))

    result = await evaluate_department_mapping(str(cycle["public_id"]))

    with psycopg.connect(settings.database_url) as connection:
        last_assignment_id = connection.execute(
            "SELECT id FROM ticket_assignment_history WHERE event_key = %s",
            ("mapping-tie-last",),
        ).fetchone()
    assert last_assignment_id is not None
    assert result["digisac_department_external_id"] == "digisac-tie-last"
    assert result["validation_json"]["assignment_history_id"] == int(last_assignment_id[0])


@pytest.mark.asyncio
async def test_mapping_blocks_when_cycle_boundary_is_insufficient() -> None:
    create_digisac_department("digisac-no-boundary")
    await record_ticket_assignment(
        conversation_id="mapping-no-boundary",
        department_id="digisac-no-boundary",
        user_id=None,
        event_timestamp="2026-08-14T10:05:00Z",
        event_key="mapping-no-boundary-assignment",
    )
    cycle, _ = await close_cycle(
        conversation_id="mapping-no-boundary",
        protocol="no-boundary",
        closed_at="2026-08-14T11:00:00Z",
        close_event_key="mapping-no-boundary-close",
    )

    result = await evaluate_department_mapping(str(cycle["public_id"]))

    assert result["state"] == "unresolved"
    assert result["reason"] == "cycle_boundary_insufficient"
    assert result["digisac_department_external_id"] is None
    assert result["validation_json"]["assignment_history_id"] is None


@pytest.mark.asyncio
async def test_mapping_lifecycle_many_to_one_and_replay() -> None:
    create_digisac_department("digisac-fiscal")
    create_digisac_department("digisac-tax")
    create_acessorias_department("acessorias-fiscal")

    first = await configure_department_mapping(
        "digisac-fiscal",
        "acessorias-fiscal",
        reason="approved_route",
        operation_key="map-fiscal-v1",
    )
    concurrent = await asyncio.gather(
        *[
            configure_department_mapping(
                "digisac-tax",
                "acessorias-fiscal",
                reason="approved_route",
                operation_key="map-tax-v1",
            )
            for _ in range(5)
        ]
    )
    replayed = await configure_department_mapping(
        "digisac-fiscal",
        "acessorias-fiscal",
        reason="approved_route",
        operation_key="map-fiscal-v1",
    )
    with pytest.raises(DepartmentMappingConflictError):
        await configure_department_mapping(
            "digisac-fiscal",
            "acessorias-fiscal",
            reason="different_operation",
            operation_key="map-fiscal-v1",
        )

    assert first["state"] == "active"
    assert first["source"] == "manual_db"
    assert first["actor"] is None
    assert first["effective_at"]
    assert {item["id"] for item in concurrent} == {concurrent[0]["id"]}
    assert replayed["id"] == first["id"]
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM department_mapping_rules WHERE state = 'active'"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT COUNT(*) FROM department_mapping_transitions"
        ).fetchone() == (2,)


@pytest.mark.asyncio
async def test_inactivation_preserves_rule_history() -> None:
    create_digisac_department("digisac-history")
    create_acessorias_department("acessorias-history")
    await configure_department_mapping(
        "digisac-history",
        "acessorias-history",
        reason="approved_route",
        operation_key="map-history-v1",
    )

    inactive = await configure_department_mapping(
        "digisac-history",
        "acessorias-history",
        active=False,
        reason="route_disabled",
        operation_key="map-history-disable-v1",
    )
    assert inactive["state"] == "inactive"
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM department_mapping_rules"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM department_mapping_transitions"
        ).fetchone() == (2,)


@pytest.mark.asyncio
async def test_valid_cycle_mapping_is_auditable_and_idempotent() -> None:
    create_digisac_department("digisac-valid")
    company_id = create_acessorias_company("company-valid")
    create_acessorias_department("acessorias-valid")
    create_company_department(company_id, "acessorias-valid")
    await configure_department_mapping(
        "digisac-valid",
        "acessorias-valid",
        reason="approved_route",
        operation_key="map-valid-v1",
    )
    cycle_id = await create_confirmed_cycle(
        conversation_id="mapping-valid",
        digisac_department_external_id="digisac-valid",
        company_id=company_id,
        contact_external_id="mapping-contact-valid",
    )

    evaluations = await asyncio.gather(
        *[evaluate_department_mapping(cycle_id) for _ in range(5)]
    )
    first = evaluations[0]
    replayed = await evaluate_department_mapping(cycle_id)

    assert first["state"] == "resolved"
    assert first["acessorias_department_external_id"] == "acessorias-valid"
    assert first["rule_version"] == 1
    assert first["validation_json"]["relationship_available"] is True
    assert {item["id"] for item in evaluations} == {first["id"]}
    assert replayed["id"] == first["id"]
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM conversation_cycle_department_mappings"
        ).fetchone() == (1,)


@pytest.mark.asyncio
async def test_unconfirmed_identity_and_missing_rule_never_fallback() -> None:
    create_digisac_department("digisac-unresolved")
    company_id = create_acessorias_company("company-unresolved")
    create_acessorias_department("acessorias-unresolved")
    create_company_department(company_id, "acessorias-unresolved")
    cycle_id = await create_confirmed_cycle(
        conversation_id="mapping-unresolved",
        digisac_department_external_id="digisac-unresolved",
        company_id=None,
        contact_external_id="mapping-contact-unresolved",
    )

    result = await evaluate_department_mapping(cycle_id)

    assert result["state"] == "unresolved"
    assert result["acessorias_department_external_id"] is None
    assert result["reason"] == "identity_not_confirmed"

    confirmed_cycle_id = await create_confirmed_cycle(
        conversation_id="mapping-no-rule",
        digisac_department_external_id="digisac-unresolved",
        company_id=company_id,
        contact_external_id="mapping-contact-no-rule",
    )
    no_rule = await evaluate_department_mapping(confirmed_cycle_id)
    assert no_rule["state"] == "unresolved"
    assert no_rule["reason"] == "mapping_rule_missing"


@pytest.mark.asyncio
async def test_missing_directory_department_and_failed_configuration_leave_state_unchanged() -> None:
    create_digisac_department("digisac-rollback")
    with pytest.raises(LookupError, match="Acessorias department"):
        await configure_department_mapping(
            "digisac-rollback",
            "missing-target",
            reason="approved_route",
            operation_key="map-rollback-v1",
        )
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM department_mapping_rules"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM department_mapping_transitions"
        ).fetchone() == (0,)

    company_id = create_acessorias_company("company-missing-source")
    create_acessorias_department("acessorias-missing-source")
    create_company_department(company_id, "acessorias-missing-source")
    cycle_id = await create_confirmed_cycle(
        conversation_id="mapping-missing-source",
        digisac_department_external_id="digisac-not-in-directory",
        company_id=company_id,
        contact_external_id="mapping-contact-missing-source",
    )
    result = await evaluate_department_mapping(cycle_id)
    assert result["state"] == "invalid"
    assert result["reason"] == "digisac_department_unavailable"


@pytest.mark.asyncio
async def test_inactive_relationship_is_invalid_and_no_fallback() -> None:
    create_digisac_department("digisac-invalid")
    company_id = create_acessorias_company("company-invalid")
    create_acessorias_department("acessorias-invalid")
    create_company_department(company_id, "acessorias-invalid", active=False)
    await configure_department_mapping(
        "digisac-invalid",
        "acessorias-invalid",
        reason="approved_route",
        operation_key="map-invalid-v1",
    )
    cycle_id = await create_confirmed_cycle(
        conversation_id="mapping-invalid",
        digisac_department_external_id="digisac-invalid",
        company_id=company_id,
        contact_external_id="mapping-contact-invalid",
    )

    result = await evaluate_department_mapping(cycle_id)

    assert result["state"] == "invalid"
    assert result["reason"] == "company_department_unavailable"
    assert result["acessorias_department_external_id"] == "acessorias-invalid"


@pytest.mark.asyncio
async def test_terminal_snapshot_is_immutable_and_explicit_later_evaluation_is_separate() -> None:
    create_digisac_department("digisac-terminal", name="Original")
    company_id = create_acessorias_company("company-terminal")
    create_acessorias_department("acessorias-terminal", name="Original")
    create_company_department(company_id, "acessorias-terminal")
    await configure_department_mapping(
        "digisac-terminal",
        "acessorias-terminal",
        reason="approved_route",
        operation_key="map-terminal-v1",
    )
    cycle_id = await create_confirmed_cycle(
        conversation_id="mapping-terminal",
        digisac_department_external_id="digisac-terminal",
        company_id=company_id,
        contact_external_id="mapping-contact-terminal",
    )
    first = await evaluate_department_mapping(cycle_id)
    await transition_cycle(str(cycle_id), "completed")
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            "UPDATE digisac_departments SET name = 'Renamed' WHERE id = %s",
            ("digisac-terminal",),
        )
        connection.execute(
            "UPDATE acessorias_departments SET name = 'Renamed' WHERE external_id = %s",
            ("acessorias-terminal",),
        )
        connection.execute(
            """
            UPDATE acessorias_company_departments AS relation
            SET is_active = FALSE
            FROM acessorias_departments AS department
            WHERE relation.company_id = %s
              AND relation.department_id = department.id
              AND department.external_id = %s
            """,
            (company_id, "acessorias-terminal"),
        )
    replayed = await evaluate_department_mapping(cycle_id)
    later = await evaluate_department_mapping(cycle_id, evaluation_key="refresh-1")

    assert replayed["id"] == first["id"]
    assert replayed["state"] == "resolved"
    assert later["id"] != first["id"]
    assert later["state"] == "invalid"


@pytest.mark.asyncio
async def test_mapping_rejects_unsafe_reason_without_persisting(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    with pytest.raises(ValueError, match="reason"):
        await configure_department_mapping(
            "missing",
            "missing",
            reason="raw PII and secret",
        )
    assert "raw PII" not in caplog.text
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM department_mapping_rules"
        ).fetchone() == (0,)
