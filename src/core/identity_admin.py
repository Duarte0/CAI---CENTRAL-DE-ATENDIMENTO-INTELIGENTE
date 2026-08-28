"""PostgreSQL read projections for the authenticated identity triage API.

This module is deliberately read-only.  It exposes sanitized projections of
the existing identity and directory tables without selecting normalized
phone/email values or invoking discovery, hydration, synchronization, Redis,
or either provider.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, LiteralString, Mapping, cast

from psycopg.rows import dict_row

from src.core.db import get_database_pool

IDENTITY_STATES = frozenset(
    {"candidate", "confirmed", "rejected", "ambiguous", "unresolved", "conflict"}
)


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _display_name(row: Mapping[str, Any]) -> str:
    value = row.get("display_name") or row.get("external_id")
    return str(value)


def _current_state(*, candidate_count: int, confirmed_count: int) -> str:
    if confirmed_count > 1:
        return "conflict"
    if confirmed_count == 1:
        return "confirmed"
    if candidate_count > 1:
        return "ambiguous"
    if candidate_count == 1:
        return "candidate"
    return "unresolved"


def _link_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    is_present = bool(row["is_present"])
    is_active = row["is_active"]
    return {
        "acessorias_company_external_id": str(row["company_external_id"]),
        "state": str(row["state"]),
        "source": str(row["source"]),
        "confirmation_source": row["confirmation_source"],
        "confirmed_at": _iso(row["confirmed_at"]),
        "rejection_reason": row["rejection_reason"],
        "display_name": _display_name(row),
        "is_present": is_present,
        "is_active": is_active,
        "available": is_present and is_active is True,
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def _evidence_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "acessorias_company_external_id": str(row["company_external_id"]),
        "evidence_type": str(row["evidence_type"]),
        "count": int(row["evidence_count"]),
        "latest_observed_at": _iso(row["latest_observed_at"]),
    }


def _list_identity_link_projection_sync(
    *,
    state: str | None,
    query: str | None,
    after: tuple[str, int] | None,
    limit: int,
) -> dict[str, Any]:
    if state is not None and state not in IDENTITY_STATES:
        raise ValueError("invalid identity state")
    if not 1 <= limit <= 100:
        raise ValueError("identity projection limit must be between 1 and 100")

    conditions: list[str] = []
    parameters: list[Any] = []
    if state is not None:
        conditions.append(
            "(classified.current_state = %s "
            "OR (%s = 'rejected' AND classified.rejected_count > 0))"
        )
        parameters.extend((state, state))
    if query:
        conditions.append(
            "(classified.external_id ILIKE %s OR "
            "COALESCE(NULLIF(BTRIM(classified.name), ''), "
            "NULLIF(BTRIM(classified.alternative_name), ''), "
            "NULLIF(BTRIM(classified.internal_name), ''), "
            "classified.external_id) ILIKE %s)"
        )
        pattern = f"%{query}%"
        parameters.extend((pattern, pattern))
    if after is not None:
        conditions.append("(classified.external_id, classified.contact_id) > (%s, %s)")
        parameters.extend(after)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    parameters.append(limit + 1)
    query = f"""
        WITH aggregates AS (
            SELECT
                contact.id AS contact_id,
                contact.external_id,
                contact.name,
                contact.alternative_name,
                contact.internal_name,
                contact.is_group,
                COUNT(DISTINCT link.acessorias_company_id)
                    FILTER (WHERE link.state = 'candidate') AS candidate_count,
                COUNT(link.id) FILTER (WHERE link.state = 'confirmed')
                    AS confirmed_count,
                COUNT(link.id) FILTER (WHERE link.state = 'rejected')
                    AS rejected_count
            FROM digisac_contacts AS contact
            LEFT JOIN identity_company_links AS link
              ON link.digisac_contact_id = contact.id
            GROUP BY contact.id
        ), classified AS (
            SELECT
                aggregates.*,
                CASE
                    WHEN confirmed_count > 1 THEN 'conflict'
                    WHEN confirmed_count = 1 THEN 'confirmed'
                    WHEN candidate_count > 1 THEN 'ambiguous'
                    WHEN candidate_count = 1 THEN 'candidate'
                    ELSE 'unresolved'
                END AS current_state
            FROM aggregates
        )
        SELECT *
        FROM classified
        {where}
        ORDER BY classified.external_id, classified.contact_id
        LIMIT %s
    """

    pool = get_database_pool()
    with pool.connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            page_rows = cursor.execute(cast(LiteralString, query), parameters).fetchall()
            has_more = len(page_rows) > limit
            page_rows = page_rows[:limit]
            if not page_rows:
                return {"items": [], "next_after": None}

            contact_ids = [int(row["contact_id"]) for row in page_rows]
            link_rows = cursor.execute(
                """
                SELECT
                    link.digisac_contact_id,
                    link.state,
                    link.source,
                    link.confirmation_source,
                    link.confirmed_at,
                    link.rejection_reason,
                    link.created_at,
                    link.updated_at,
                    company.external_id AS company_external_id,
                    company.legal_name,
                    company.trade_name,
                    company.is_present,
                    company.is_active,
                    COALESCE(
                        NULLIF(BTRIM(company.trade_name), ''),
                        NULLIF(BTRIM(company.legal_name), ''),
                        company.external_id
                    ) AS display_name
                FROM identity_company_links AS link
                JOIN acessorias_companies AS company
                  ON company.id = link.acessorias_company_id
                WHERE link.digisac_contact_id = ANY(%s)
                ORDER BY link.digisac_contact_id, link.id
                """,
                (contact_ids,),
            ).fetchall()
            evidence_rows = cursor.execute(
                """
                SELECT
                    evidence.digisac_contact_id,
                    company.external_id AS company_external_id,
                    evidence.evidence_type,
                    COUNT(*)::INTEGER AS evidence_count,
                    MAX(evidence.observed_at) AS latest_observed_at
                FROM identity_match_evidence AS evidence
                JOIN acessorias_companies AS company
                  ON company.id = evidence.acessorias_company_id
                WHERE evidence.digisac_contact_id = ANY(%s)
                GROUP BY
                    evidence.digisac_contact_id,
                    company.external_id,
                    evidence.evidence_type
                ORDER BY
                    evidence.digisac_contact_id,
                    company.external_id,
                    evidence.evidence_type
                """,
                (contact_ids,),
            ).fetchall()

    links_by_contact: dict[int, list[dict[str, Any]]] = {contact_id: [] for contact_id in contact_ids}
    for row in link_rows:
        links_by_contact[int(row["digisac_contact_id"])].append(_link_projection(row))
    evidence_by_contact: dict[int, list[dict[str, Any]]] = {
        contact_id: [] for contact_id in contact_ids
    }
    for row in evidence_rows:
        evidence_by_contact[int(row["digisac_contact_id"])].append(
            _evidence_projection(row)
        )

    items: list[dict[str, Any]] = []
    for row in page_rows:
        contact_id = int(row["contact_id"])
        display_name = row["name"] or row["alternative_name"] or row["internal_name"]
        items.append(
            {
                "digisac_contact_external_id": str(row["external_id"]),
                "display_name": display_name,
                "is_group": row["is_group"],
                "state": (
                    "rejected"
                    if state == "rejected" and int(row["rejected_count"]) > 0
                    else str(row["current_state"])
                ),
                "candidate_company_count": int(row["candidate_count"]),
                "links": links_by_contact[contact_id],
                "evidence": evidence_by_contact[contact_id],
            }
        )

    last = page_rows[-1]
    next_after = (
        (str(last["external_id"]), int(last["contact_id"])) if has_more else None
    )
    return {"items": items, "next_after": next_after}


async def list_identity_link_projection(
    *,
    state: str | None,
    query: str | None = None,
    after: tuple[str, int] | None,
    limit: int,
) -> dict[str, Any]:
    """Return one bounded, deterministic page of sanitized contact projections."""
    return await asyncio.to_thread(
        _list_identity_link_projection_sync,
        state=state,
        query=query,
        after=after,
        limit=limit,
    )


def _get_identity_contact_projection_sync(
    external_id: str,
    *,
    include_contact_number: bool,
) -> dict[str, Any] | None:
    pool = get_database_pool()
    with pool.connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            contact_columns = (
                "id, external_id, name, alternative_name, internal_name, "
                "raw_number, is_group"
                if include_contact_number
                else "id, external_id, name, alternative_name, internal_name, is_group"
            )
            contact = cursor.execute(
                f"""
                SELECT {contact_columns}
                FROM digisac_contacts
                WHERE external_id = %s
                """,
                (external_id,),
            ).fetchone()
            if contact is None:
                return None
            contact_id = int(contact["id"])
            link_rows = cursor.execute(
                """
                SELECT
                    link.id,
                    link.state,
                    link.source,
                    link.confirmation_source,
                    link.confirmed_at,
                    link.rejection_reason,
                    link.created_at,
                    link.updated_at,
                    company.external_id AS company_external_id,
                    company.legal_name,
                    company.trade_name,
                    company.is_present,
                    company.is_active,
                    COALESCE(
                        NULLIF(BTRIM(company.trade_name), ''),
                        NULLIF(BTRIM(company.legal_name), ''),
                        company.external_id
                    ) AS display_name
                FROM identity_company_links AS link
                JOIN acessorias_companies AS company
                  ON company.id = link.acessorias_company_id
                WHERE link.digisac_contact_id = %s
                ORDER BY link.id
                """,
                (contact_id,),
            ).fetchall()
            evidence_rows = cursor.execute(
                """
                SELECT
                    company.external_id AS company_external_id,
                    evidence.evidence_type,
                    COUNT(*)::INTEGER AS evidence_count,
                    MAX(evidence.observed_at) AS latest_observed_at
                FROM identity_match_evidence AS evidence
                JOIN acessorias_companies AS company
                  ON company.id = evidence.acessorias_company_id
                WHERE evidence.digisac_contact_id = %s
                GROUP BY company.external_id, evidence.evidence_type
                ORDER BY company.external_id, evidence.evidence_type
                """,
                (contact_id,),
            ).fetchall()
            transition_rows = cursor.execute(
                """
                SELECT
                    transition.id,
                    company.external_id AS company_external_id,
                    transition.from_state,
                    transition.to_state,
                    transition.source,
                    transition.reason,
                    transition.confirmation_source,
                    transition.confirmed_at,
                    transition.created_at
                FROM identity_company_link_transitions AS transition
                JOIN identity_company_links AS link
                  ON link.id = transition.link_id
                JOIN acessorias_companies AS company
                  ON company.id = link.acessorias_company_id
                WHERE link.digisac_contact_id = %s
                ORDER BY transition.created_at, transition.id
                """,
                (contact_id,),
            ).fetchall()
            candidate_company_rows = cursor.execute(
                """
                SELECT DISTINCT
                    company.external_id AS company_external_id,
                    company.legal_name,
                    company.trade_name,
                    company.is_present,
                    company.is_active,
                    COALESCE(
                        NULLIF(BTRIM(company.trade_name), ''),
                        NULLIF(BTRIM(company.legal_name), ''),
                        company.external_id
                    ) AS display_name
                FROM acessorias_companies AS company
                WHERE company.id IN (
                    SELECT link.acessorias_company_id
                    FROM identity_company_links AS link
                    WHERE link.digisac_contact_id = %s
                    UNION
                    SELECT evidence.acessorias_company_id
                    FROM identity_match_evidence AS evidence
                    WHERE evidence.digisac_contact_id = %s
                )
                ORDER BY company.external_id
                """,
                (contact_id, contact_id),
            ).fetchall()

    links = [_link_projection(row) for row in link_rows]
    evidence = [_evidence_projection(row) for row in evidence_rows]
    confirmed_count = sum(1 for row in links if row["state"] == "confirmed")
    candidate_count = len(
        {row["acessorias_company_external_id"] for row in links if row["state"] == "candidate"}
    )
    display_name = contact["name"] or contact["alternative_name"] or contact["internal_name"]
    companies: list[dict[str, Any]] = []
    for row in candidate_company_rows:
        is_present = bool(row["is_present"])
        is_active = row["is_active"]
        companies.append(
            {
                "acessorias_company_external_id": str(row["company_external_id"]),
                "display_name": _display_name(row),
                "is_present": is_present,
                "is_active": is_active,
                "available": is_present and is_active is True,
            }
        )
    transitions = [
        {
            "acessorias_company_external_id": str(row["company_external_id"]),
            "from_state": row["from_state"],
            "to_state": str(row["to_state"]),
            "source": str(row["source"]),
            "reason": str(row["reason"]),
            "confirmation_source": row["confirmation_source"],
            "confirmed_at": _iso(row["confirmed_at"]),
            "created_at": _iso(row["created_at"]),
        }
        for row in transition_rows
    ]
    projection = {
        "digisac_contact_external_id": str(contact["external_id"]),
        "display_name": display_name,
        "is_group": contact["is_group"],
        "state": _current_state(
            candidate_count=candidate_count, confirmed_count=confirmed_count
        ),
        "candidate_company_count": candidate_count,
        "links": links,
        "evidence": evidence,
        "transitions": transitions,
        "candidate_companies": companies,
    }
    if include_contact_number:
        projection["contact_number"] = contact["raw_number"]
    return projection


async def get_identity_contact_projection(
    external_id: str,
    *,
    include_contact_number: bool = False,
) -> dict[str, Any] | None:
    """Return one contact projection, including candidate-free contacts."""
    return await asyncio.to_thread(
        _get_identity_contact_projection_sync,
        external_id,
        include_contact_number=include_contact_number,
    )


def _list_active_companies_sync(
    *,
    query: str | None,
    after: tuple[str, int] | None,
    limit: int,
) -> dict[str, Any]:
    if not 1 <= limit <= 100:
        raise ValueError("company projection limit must be between 1 and 100")
    conditions = ["company.is_present IS TRUE", "company.is_active IS TRUE"]
    parameters: list[Any] = []
    if query:
        conditions.append(
            "(company.external_id ILIKE %s OR company.legal_name ILIKE %s "
            "OR company.trade_name ILIKE %s)"
        )
        pattern = f"%{query}%"
        parameters.extend((pattern, pattern, pattern))
    if after is not None:
        conditions.append("(company.external_id, company.id) > (%s, %s)")
        parameters.extend(after)
    parameters.append(limit + 1)
    with get_database_pool().connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            rows = cursor.execute(
                cast(
                    LiteralString,
                    f"""
                SELECT
                    company.id,
                    company.external_id AS company_external_id,
                    company.legal_name,
                    company.trade_name,
                    company.is_present,
                    company.is_active,
                    COALESCE(
                        NULLIF(BTRIM(company.trade_name), ''),
                        NULLIF(BTRIM(company.legal_name), ''),
                        company.external_id
                    ) AS display_name
                FROM acessorias_companies AS company
                WHERE {' AND '.join(conditions)}
                ORDER BY company.external_id, company.id
                LIMIT %s
                """,
                ),
                parameters,
            ).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [
        {
            "acessorias_company_external_id": str(row["company_external_id"]),
            "display_name": _display_name(row),
            "is_present": True,
            "is_active": True,
            "available": True,
        }
        for row in rows
    ]
    last = rows[-1] if rows else None
    next_after = (
        (str(last["company_external_id"]), int(last["id"]))
        if has_more and last is not None
        else None
    )
    return {"items": items, "next_after": next_after}


async def list_active_company_projection(
    *,
    query: str | None,
    after: tuple[str, int] | None,
    limit: int,
) -> dict[str, Any]:
    """Return only present, active directory companies in deterministic pages."""
    return await asyncio.to_thread(
        _list_active_companies_sync,
        query=query,
        after=after,
        limit=limit,
    )
