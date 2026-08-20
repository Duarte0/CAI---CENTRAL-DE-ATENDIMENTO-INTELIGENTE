"""Durable preparation boundary before Acessórias Request delivery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from psycopg.rows import dict_row

from src.core.db import get_database_pool
from src.core.department_mapping import evaluate_department_mapping
from src.core.identity_resolution import resolve_cycle_identity


_TERMINAL_CYCLE_STATES = frozenset({"completed", "completed_with_warnings"})


@dataclass(frozen=True)
class RequestPreparation:
    """Safe preparation result; no contact or classification content is retained."""

    ready: bool
    stage: str
    reason: str
    identity: dict[str, Any] | None = None
    mapping: dict[str, Any] | None = None


def _load_cycle_contact_sync(cycle_public_id: str) -> dict[str, Any] | None:
    with get_database_pool().connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute(
                """
                SELECT cycle.status, cycle.digisac_contact_external_id,
                       contact.id AS digisac_contact_id
                FROM conversation_processing_cycles AS cycle
                LEFT JOIN digisac_contacts AS contact
                  ON contact.external_id = cycle.digisac_contact_external_id
                WHERE cycle.public_id = %s
                """,
                (cycle_public_id,),
            ).fetchone()
    return None if row is None else dict(row)


async def _load_cycle_contact(cycle_public_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_load_cycle_contact_sync, cycle_public_id)


async def prepare_cycle_for_request(cycle_public_id: str) -> RequestPreparation:
    """Resolve the canonical ticket contact and mapping before Request creation."""
    cycle = await _load_cycle_contact(cycle_public_id)
    if cycle is None:
        raise LookupError("conversation cycle not found")
    if cycle.get("status") not in _TERMINAL_CYCLE_STATES:
        return RequestPreparation(False, "cycle", "cycle_not_terminal")

    contact_external_id = cycle.get("digisac_contact_external_id")
    if not isinstance(contact_external_id, str) or not contact_external_id.strip():
        return RequestPreparation(False, "identity", "canonical_contact_missing")
    contact_id = cycle.get("digisac_contact_id")
    if not isinstance(contact_id, int):
        return RequestPreparation(False, "identity", "canonical_contact_unavailable")

    identity = await resolve_cycle_identity(cycle_public_id, contact_id)
    if identity.get("digisac_contact_id") != contact_id:
        return RequestPreparation(False, "identity", "contact_provenance_mismatch")
    if identity.get("state") != "confirmed":
        return RequestPreparation(
            False,
            "identity",
            str(identity.get("reason") or identity.get("state") or "identity_unresolved"),
            identity=identity,
        )

    mapping = await evaluate_department_mapping(cycle_public_id)
    if mapping.get("state") != "resolved":
        return RequestPreparation(
            False,
            "mapping",
            str(mapping.get("reason") or "mapping_unresolved"),
            identity=identity,
            mapping=mapping,
        )
    return RequestPreparation(
        True,
        "ready",
        "prepared",
        identity=identity,
        mapping=mapping,
    )
