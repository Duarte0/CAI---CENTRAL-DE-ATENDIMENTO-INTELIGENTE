from __future__ import annotations

import asyncio
import logging

import psycopg
import pytest

from src.core.config import settings
from src.core.db import close_cycle, upsert_digisac_contact
from src.core.digisac_client import DigisacContact
from src.core.identity_resolution import (
    IdentityConflictError,
    confirm_identity_link,
    discover_identity,
    get_cycle_identity_resolution,
    list_identity_evidence,
    reject_identity_link,
    resolve_cycle_identity,
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


def create_company_contact(
    company_id: int,
    external_key: str,
    *,
    mobile: str | None = None,
    email: str | None = None,
    active: bool = True,
) -> int:
    with psycopg.connect(settings.database_url) as connection:
        row = connection.execute(
            """
            INSERT INTO acessorias_company_contacts (
                company_id, external_key, raw_mobile, normalized_mobile,
                raw_email, normalized_email, is_present, is_active, synced_at
            ) VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, CURRENT_TIMESTAMP)
            RETURNING id
            """,
            (
                company_id,
                external_key,
                mobile,
                mobile,
                email,
                None if email is None else email.casefold(),
                active,
            ),
        ).fetchone()
    assert row is not None
    return int(row[0])


async def create_contact(
    external_id: str,
    *,
    number: str | None = None,
    email: str | None = None,
    is_group: bool = False,
) -> int:
    row = await upsert_digisac_contact(
        DigisacContact(
            external_id=external_id,
            raw_number=number,
            normalized_number=number,
            raw_email=email,
            normalized_email=None if email is None else email.casefold(),
            is_group=is_group,
        ),
        source="identity_test",
    )
    return int(row["id"])


@pytest.mark.asyncio
async def test_discovery_is_idempotent_and_keeps_many_to_many_evidence():
    company_one = create_company("company-one")
    company_two = create_company("company-two")
    create_company_contact(company_one, "contact-one", mobile="551198765432")
    create_company_contact(company_one, "contact-two", mobile="551198765432")
    create_company_contact(company_two, "contact-three", mobile="551198765432")
    contact_id = await create_contact("digisac-one", number="551198765432")

    result = await discover_identity(contact_id)
    repeated = await discover_identity(contact_id)

    assert result["state"] == "ambiguous"
    assert set(result["company_ids"]) == {company_one, company_two}
    assert repeated == result
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM identity_company_links"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT COUNT(*) FROM identity_company_link_transitions"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT COUNT(*) FROM identity_match_evidence"
        ).fetchone() == (3,)


@pytest.mark.asyncio
async def test_email_variant_group_and_negative_directory_cases():
    company = create_company("company-email")
    create_company_contact(company, "email-contact", email="user@example.test")
    email_contact = await create_contact(
        "digisac-email", email="USER@example.test"
    )
    email_result = await discover_identity(email_contact)
    assert email_result["state"] == "candidate"
    assert len(await list_identity_evidence(email_contact)) == 1

    variant_company = create_company("company-variant")
    create_company_contact(variant_company, "variant-contact", mobile="5511998765432")
    variant_contact = await create_contact(
        "digisac-variant", number="551198765432"
    )
    variant_result = await discover_identity(variant_contact)
    assert variant_result["state"] == "candidate"
    assert (await list_identity_evidence(variant_contact))[0]["evidence_type"] == (
        "brazil_mobile_variant"
    )

    inactive_company = create_company("company-inactive", active=False)
    create_company_contact(inactive_company, "inactive-contact", mobile="551198765432")
    group_contact = await create_contact(
        "digisac-group", number="551198765432", is_group=True
    )
    assert (await discover_identity(group_contact))["state"] == "unresolved"


@pytest.mark.asyncio
async def test_manual_confirmation_has_precedence_and_conflict_is_safe():
    first_company = create_company("company-confirmed")
    second_company = create_company("company-divergent")
    create_company_contact(first_company, "confirmed-contact", mobile="551198765432")
    contact_id = await create_contact("digisac-confirmed", number="551198765432")
    discovered = await discover_identity(contact_id)
    assert discovered["state"] == "candidate"

    confirmed_at = "2026-08-14T12:00:00Z"
    confirmations = await asyncio.gather(
        *[
            confirm_identity_link(
                contact_id, first_company, confirmed_at=confirmed_at
            )
            for _ in range(5)
        ]
    )
    confirmed = confirmations[0]
    assert {item["id"] for item in confirmations} == {confirmed["id"]}
    assert confirmed["state"] == "confirmed"
    assert confirmed["confirmation_source"] == "manual_db"
    assert confirmed["confirmed_by"] is None

    with pytest.raises(IdentityConflictError):
        await confirm_identity_link(
            contact_id, second_company, confirmed_at=confirmed_at
        )
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM identity_company_links WHERE state = 'confirmed'"
        ).fetchone() == (1,)

    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            """
            UPDATE digisac_contacts
            SET normalized_number = '5511998765432'
            WHERE id = %s
            """,
            (contact_id,),
        )
    create_company_contact(second_company, "divergent-contact", mobile="5511998765432")
    replayed = await discover_identity(contact_id)
    assert replayed["state"] == "confirmed"
    assert replayed["company_ids"] == [first_company]

    rejected = await reject_identity_link(
        contact_id, first_company, reason="operator_correction"
    )
    assert rejected["state"] == "rejected"
    corrected = await confirm_identity_link(
        contact_id, second_company, confirmed_at=confirmed_at
    )
    assert corrected["state"] == "confirmed"
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM identity_company_link_transitions"
        ).fetchone() == (5,)


@pytest.mark.asyncio
async def test_cycle_resolution_is_immutable_after_terminal_result():
    company = create_company("company-cycle")
    create_company_contact(company, "cycle-contact", mobile="551198765432")
    contact_id = await create_contact("digisac-cycle", number="551198765432")
    cycle, _ = await close_cycle(
        conversation_id="identity-cycle",
        protocol="protocol",
        closed_at="2026-08-14T10:00:00Z",
        close_event_key="identity-cycle-close",
    )
    cycle_public_id = str(cycle["public_id"])

    first = await resolve_cycle_identity(cycle_public_id, contact_id)
    assert first["state"] == "unresolved"
    await confirm_identity_link(
        contact_id, company, confirmed_at="2026-08-14T12:00:00Z"
    )
    second = await resolve_cycle_identity(cycle_public_id, contact_id)

    assert second["id"] == first["id"]
    assert second["state"] == "unresolved"
    assert (await get_cycle_identity_resolution(cycle_public_id))["id"] == first["id"]


@pytest.mark.asyncio
async def test_concurrent_replay_converges_and_invalid_transaction_changes_nothing(
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO, logger="src.core.identity_resolution")
    company = create_company("company-concurrent")
    create_company_contact(company, "concurrent-contact", email="concurrent@test")
    contact_id = await create_contact("digisac-concurrent", email="concurrent@test")
    results = await asyncio.gather(
        *[discover_identity(contact_id) for _ in range(5)]
    )
    assert {item["state"] for item in results} == {"candidate"}
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM identity_match_evidence"
        ).fetchone() == (1,)
    assert "concurrent@test" not in caplog.text

    with pytest.raises(LookupError):
        await discover_identity(999999999)
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM identity_match_evidence"
        ).fetchone() == (1,)


@pytest.mark.asyncio
async def test_manual_confirmation_requires_timestamp_and_schema_keeps_states_safe():
    company = create_company("company-constraints")
    contact_id = await create_contact("digisac-constraints")

    with pytest.raises(ValueError, match="confirmed_at"):
        await confirm_identity_link(contact_id, company)
    with psycopg.connect(settings.database_url) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                INSERT INTO identity_company_links (
                    digisac_contact_id, acessorias_company_id, state, source
                ) VALUES (%s, %s, 'rejected', 'manual_db')
                """,
                (contact_id, company),
            )
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM identity_company_links"
        ).fetchone() == (0,)
