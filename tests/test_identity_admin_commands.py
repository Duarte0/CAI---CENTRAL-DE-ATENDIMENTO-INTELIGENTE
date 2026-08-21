"""Disposable PostgreSQL coverage for authenticated identity commands."""

from __future__ import annotations

import asyncio

import psycopg
import pytest

from src.core.config import settings
from src.core.db import upsert_digisac_contact
from src.core.digisac_client import DigisacContact
from src.core.identity_resolution import (
    IdentityCommandConflictError,
    IdentityResolutionError,
    confirm_identity_link_admin,
    discover_identity,
    reject_identity_link_admin,
)

pytestmark = pytest.mark.postgres


def create_company(external_id: str, *, active: bool = True) -> int:
    with psycopg.connect(settings.database_url) as connection:
        row = connection.execute(
            """
            INSERT INTO acessorias_companies (
                external_id, provider_id, legal_name, trade_name,
                is_present, is_active, synced_at
            ) VALUES (%s, %s, '', '', TRUE, %s, CURRENT_TIMESTAMP)
            RETURNING id
            """,
            (external_id, f"provider-{external_id}", active),
        ).fetchone()
    assert row is not None
    return int(row[0])


def create_company_contact(company_id: int, external_key: str) -> None:
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            """
            INSERT INTO acessorias_company_contacts (
                company_id, external_key, normalized_mobile, is_present,
                is_active, synced_at
            ) VALUES (%s, %s, %s, TRUE, TRUE, CURRENT_TIMESTAMP)
            """,
            (company_id, external_key, "551198765432"),
        )


async def create_contact(external_id: str) -> int:
    row = await upsert_digisac_contact(
        DigisacContact(
            external_id=external_id,
            raw_number="551198765432",
            normalized_number="551198765432",
            is_group=False,
        ),
        source="identity_admin_command_test",
    )
    return int(row["id"])


@pytest.mark.asyncio
async def test_confirmation_is_durable_idempotent_and_server_derived() -> None:
    company_id = create_company("admin-command-company")
    create_company_contact(company_id, "admin-command-directory-contact")
    contact_id = await create_contact("admin-command-contact")
    discovered = await discover_identity(contact_id)
    assert discovered["state"] == "candidate"

    first = await confirm_identity_link_admin(
        "admin-command-contact",
        "admin-command-company",
        reason="operator_verified",
        idempotency_key="confirm-command-key",
    )
    replay = await confirm_identity_link_admin(
        "admin-command-contact",
        "admin-command-company",
        reason="operator_verified",
        idempotency_key="confirm-command-key",
    )

    assert first["replayed"] is False
    assert replay == {"replayed": True, "result": first["result"]}
    assert first["result"]["state"] == "confirmed"
    assert first["result"]["source"] == "admin_api"
    assert first["result"]["confirmation_source"] == "admin_api"
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM identity_admin_commands"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM identity_company_link_transitions"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT confirmed_by FROM identity_company_links WHERE digisac_contact_id = %s",
            (contact_id,),
        ).fetchone() == ("admin",)


@pytest.mark.asyncio
async def test_rejection_preserves_confirmation_history_and_replay() -> None:
    company_id = create_company("admin-reject-company")
    create_company_contact(company_id, "admin-reject-directory-contact")
    contact_id = await create_contact("admin-reject-contact")
    await discover_identity(contact_id)
    await confirm_identity_link_admin(
        "admin-reject-contact",
        "admin-reject-company",
        reason="operator_verified",
        idempotency_key="reject-confirm-key",
    )

    rejected = await reject_identity_link_admin(
        "admin-reject-contact",
        "admin-reject-company",
        reason="operator_correction",
        idempotency_key="reject-command-key",
    )
    replay = await reject_identity_link_admin(
        "admin-reject-contact",
        "admin-reject-company",
        reason="operator_correction",
        idempotency_key="reject-command-key",
    )

    assert rejected["result"]["state"] == "rejected"
    assert rejected["result"]["source"] == "admin_api"
    assert rejected["result"]["rejection_reason"] == "operator_correction"
    assert replay == {"replayed": True, "result": rejected["result"]}
    with psycopg.connect(settings.database_url) as connection:
        transitions = connection.execute(
            """
            SELECT from_state, to_state, source, reason, confirmed_by
            FROM identity_company_link_transitions
            ORDER BY id
            """
        ).fetchall()
        assert transitions[-1] == (
            "confirmed",
            "rejected",
            "admin_api",
            "operator_correction",
            "admin",
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM identity_admin_commands"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT state, confirmed_at, confirmation_source FROM identity_company_links"
        ).fetchone() == ("rejected", None, None)


@pytest.mark.asyncio
async def test_command_key_conflict_and_failed_transaction_leave_state_unchanged() -> None:
    first_company_id = create_company("admin-conflict-company-one")
    second_company_id = create_company("admin-conflict-company-two")
    create_company_contact(first_company_id, "admin-conflict-directory-one")
    create_company_contact(second_company_id, "admin-conflict-directory-two")
    contact_id = await create_contact("admin-conflict-contact")
    await confirm_identity_link_admin(
        "admin-conflict-contact",
        "admin-conflict-company-one",
        reason="operator_verified",
        idempotency_key="conflict-command-key",
    )

    with pytest.raises(IdentityCommandConflictError):
        await confirm_identity_link_admin(
            "admin-conflict-contact",
            "admin-conflict-company-two",
            reason="operator_verified",
            idempotency_key="conflict-command-key",
        )
    inactive_id = create_company("admin-inactive-company", active=False)
    assert inactive_id > 0
    with pytest.raises(IdentityResolutionError) as error:
        await confirm_identity_link_admin(
            "admin-conflict-contact",
            "admin-inactive-company",
            reason="operator_verified",
            idempotency_key="inactive-command-key",
        )
    assert error.value.category == "directory_company_unavailable"
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute("SELECT COUNT(*) FROM identity_admin_commands").fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM identity_company_links WHERE digisac_contact_id = %s",
            (contact_id,),
        ).fetchone() == (1,)


@pytest.mark.asyncio
async def test_concurrent_same_command_key_converges_to_one_transition() -> None:
    company_id = create_company("admin-concurrent-company")
    create_company_contact(company_id, "admin-concurrent-directory-contact")
    contact_id = await create_contact("admin-concurrent-contact")

    results = await asyncio.gather(
        *[
            confirm_identity_link_admin(
                "admin-concurrent-contact",
                "admin-concurrent-company",
                reason="operator_verified",
                idempotency_key="concurrent-command-key",
            )
            for _ in range(5)
        ]
    )

    assert sum(not result["replayed"] for result in results) == 1
    assert {result["result"]["state"] for result in results} == {"confirmed"}
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM identity_admin_commands"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM identity_company_link_transitions"
        ).fetchone() == (1,)
    assert contact_id > 0
