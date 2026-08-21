"""Conservative, PostgreSQL-authoritative DigiSac-Acessorias identity resolution.

The module deliberately keeps matching, candidate links, manual confirmation,
and cycle resolution as separate facts.  It reads the existing directory and
contact foundations through the process PostgreSQL pool; Redis is not involved
in identity decisions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from src.core.db import get_database_pool

DEFAULT_RULE_VERSION = "spec0009-v1.1"
AUTOMATIC_SOURCE = "automatic"
MANUAL_SOURCE = "manual_db"
ADMIN_SOURCE = "admin_api"
ADMIN_ACTOR = "admin"
ADMIN_CONFIRM_OPERATION = "identity_link_confirmation"
ADMIN_REJECT_OPERATION = "identity_link_rejection"

_SAFE_VALUE = re.compile(r"^[a-z0-9_.:@-]{1,120}$")
_SAFE_REASON = re.compile(r"^[a-z0-9_:-]{1,120}$")
logger = logging.getLogger(__name__)


class IdentityResolutionError(RuntimeError):
    """A sanitized identity-resolution failure."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.safe_message = message


class IdentityConflictError(IdentityResolutionError):
    """A requested confirmation would create competing confirmed links."""

    def __init__(self) -> None:
        super().__init__(
            "conflicting_confirmation",
            "a different company is already confirmed for this contact",
        )


class IdentityCommandConflictError(IdentityResolutionError):
    """An idempotency key was reused for a different command."""

    def __init__(self) -> None:
        super().__init__(
            "incompatible_command",
            "the administrative command key was already used for another command",
        )


@dataclass(frozen=True)
class DigiSacMatchInput:
    contact_id: int
    normalized_number: str | None
    normalized_email: str | None
    is_group: bool | None


@dataclass(frozen=True)
class AcessoriasMatchInput:
    contact_id: int
    company_id: int
    normalized_mobile: str | None
    normalized_email: str | None
    contact_is_present: bool
    contact_is_active: bool
    company_is_present: bool
    company_is_active: bool | None


@dataclass(frozen=True)
class MatchEvidence:
    acessorias_contact_id: int
    acessorias_company_id: int
    evidence_type: str
    value_fingerprint: str


@dataclass(frozen=True)
class IdentityDiscoveryResult:
    digisac_contact_id: int
    state: str
    company_ids: tuple[int, ...]
    link_ids: tuple[int, ...]
    evidence_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "digisac_contact_id": self.digisac_contact_id,
            "state": self.state,
            "company_ids": list(self.company_ids),
            "link_ids": list(self.link_ids),
            "evidence_count": self.evidence_count,
        }


def _parse_timestamp(value: str | datetime) -> datetime:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_value(value: str, field: str) -> str:
    normalized = value.strip().lower()
    if not _SAFE_VALUE.fullmatch(normalized):
        raise ValueError(f"{field} must be a safe nonblank value")
    return normalized


def _safe_reason(value: str) -> str:
    normalized = value.strip().lower()
    if not _SAFE_REASON.fullmatch(normalized):
        raise ValueError("reason must be a safe nonblank category")
    return normalized


def _safe_opaque_reference(value: str, field: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 200
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise ValueError(f"{field} must be a safe nonblank value")
    return normalized


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _variant_fingerprint(left: str, right: str) -> str:
    return _fingerprint("\x1f".join(sorted((left, right))))


def _is_ascii_digits(value: str) -> bool:
    return bool(value) and value.isascii() and value.isdecimal()


def is_brazilian_mobile_variant(left: str, right: str) -> bool:
    """Return true only for the approved Brazilian mobile 8-to-9 rule."""
    if not _is_ascii_digits(left) or not _is_ascii_digits(right):
        return False
    if not left.startswith("55") or not right.startswith("55"):
        return False
    if len(left) not in {12, 13} or len(right) not in {12, 13}:
        return False
    if len(left) == len(right):
        return False
    short, long = (left, right) if len(left) < len(right) else (right, left)
    if len(short) != 12 or len(long) != 13:
        return False
    if short[2:4] != long[2:4]:
        return False
    short_local = short[4:]
    long_local = long[4:]
    ddd = int(short[2:4])
    return (
        11 <= ddd <= 99
        and short_local[0] in "6789"
        and long_local.startswith("9")
        and long_local[1:] == short_local
    )


def match_identity(
    digisac: DigiSacMatchInput,
    directory_contacts: Sequence[AcessoriasMatchInput],
) -> tuple[MatchEvidence, ...]:
    """Discover exact evidence and the single approved phone variant.

    A group, an unknown group flag, or a directory row that is not current is
    intentionally excluded.  No names, provider aliases, or scores enter the
    comparison.
    """
    if digisac.is_group is not False:
        return ()
    matches: list[MatchEvidence] = []
    for contact in directory_contacts:
        if not (
            contact.contact_is_present
            and contact.contact_is_active
            and contact.company_is_present
            and contact.company_is_active is not False
        ):
            continue
        if (
            digisac.normalized_number
            and contact.normalized_mobile == digisac.normalized_number
        ):
            matches.append(
                MatchEvidence(
                    contact.contact_id,
                    contact.company_id,
                    "exact_phone",
                    _fingerprint(digisac.normalized_number),
                )
            )
        if (
            digisac.normalized_email
            and contact.normalized_email == digisac.normalized_email
        ):
            matches.append(
                MatchEvidence(
                    contact.contact_id,
                    contact.company_id,
                    "exact_email",
                    _fingerprint(digisac.normalized_email),
                )
            )
        if (
            digisac.normalized_number
            and contact.normalized_mobile
            and is_brazilian_mobile_variant(
                digisac.normalized_number, contact.normalized_mobile
            )
        ):
            matches.append(
                MatchEvidence(
                    contact.contact_id,
                    contact.company_id,
                    "brazil_mobile_variant",
                    _variant_fingerprint(
                        digisac.normalized_number, contact.normalized_mobile
                    ),
                )
            )
    return tuple(matches)


def _serialize_row(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result: dict[str, Any] = {}
    for key, value in row.items():
        result[key] = value.isoformat() if isinstance(value, datetime) else value
    return result


def _lock_contact(connection: psycopg.Connection[Any], contact_id: int) -> Mapping[str, Any]:
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"cai:identity:contact:{contact_id}",),
    )
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            """
            SELECT id, normalized_number, normalized_email, is_group
            FROM digisac_contacts
            WHERE id = %s
            FOR UPDATE
            """,
            (contact_id,),
        ).fetchone()
    if row is None:
        raise LookupError("DigiSac contact not found")
    return row


def _load_directory_contacts(
    connection: psycopg.Connection[Any],
) -> list[AcessoriasMatchInput]:
    with connection.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            """
            SELECT
                contact.id AS contact_id,
                contact.company_id AS company_id,
                contact.normalized_mobile,
                contact.normalized_email,
                contact.is_present AS contact_is_present,
                contact.is_active AS contact_is_active,
                company.is_present AS company_is_present,
                company.is_active AS company_is_active
            FROM acessorias_company_contacts AS contact
            JOIN acessorias_companies AS company
              ON company.id = contact.company_id
            WHERE contact.normalized_mobile IS NOT NULL
               OR contact.normalized_email IS NOT NULL
            """
        ).fetchall()
    return [
        AcessoriasMatchInput(
            contact_id=int(row["contact_id"]),
            company_id=int(row["company_id"]),
            normalized_mobile=row["normalized_mobile"],
            normalized_email=row["normalized_email"],
            contact_is_present=bool(row["contact_is_present"]),
            contact_is_active=bool(row["contact_is_active"]),
            company_is_present=bool(row["company_is_present"]),
            company_is_active=row["company_is_active"],
        )
        for row in rows
    ]


def _insert_transition(
    connection: psycopg.Connection[Any],
    *,
    link_id: int,
    from_state: str | None,
    to_state: str,
    source: str,
    reason: str,
    transition_key: str,
    confirmation_source: str | None = None,
    confirmed_at: datetime | None = None,
    confirmed_by: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO identity_company_link_transitions (
            link_id, from_state, to_state, source, reason, transition_key,
            confirmation_source, confirmed_at, confirmed_by
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (transition_key) DO NOTHING
        """,
        (
            link_id,
            from_state,
            to_state,
            source,
            reason,
            transition_key,
            confirmation_source,
            confirmed_at,
            confirmed_by,
        ),
    )


def _admin_command_fingerprint(
    *,
    operation: str,
    digisac_contact_external_id: str,
    acessorias_company_external_id: str,
    reason: str,
) -> str:
    payload = json.dumps(
        {
            "acessorias_company_external_id": acessorias_company_external_id,
            "digisac_contact_external_id": digisac_contact_external_id,
            "operation": operation,
            "reason": reason,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _fingerprint(payload)


def _admin_link_result(
    row: Mapping[str, Any],
    *,
    digisac_contact_external_id: str,
    acessorias_company_external_id: str,
) -> dict[str, Any]:
    serialized = _serialize_row(row)
    if serialized is None:
        raise RuntimeError("identity link is unavailable")
    return {
        "digisac_contact_external_id": digisac_contact_external_id,
        "acessorias_company_external_id": acessorias_company_external_id,
        "state": str(serialized["state"]),
        "source": str(serialized["source"]),
        "confirmation_source": serialized["confirmation_source"],
        "confirmed_at": serialized["confirmed_at"],
        "rejection_reason": serialized["rejection_reason"],
        "created_at": serialized["created_at"],
        "updated_at": serialized["updated_at"],
    }


def _upsert_candidate_link(
    connection: psycopg.Connection[Any],
    *,
    contact_id: int,
    company_id: int,
) -> int:
    inserted = connection.execute(
        """
        INSERT INTO identity_company_links (
            digisac_contact_id, acessorias_company_id, state, source
        ) VALUES (%s, %s, 'candidate', %s)
        ON CONFLICT (digisac_contact_id, acessorias_company_id) DO NOTHING
        RETURNING id
        """,
        (contact_id, company_id, AUTOMATIC_SOURCE),
    ).fetchone()
    if inserted is None:
        existing = connection.execute(
            """
            SELECT id
            FROM identity_company_links
            WHERE digisac_contact_id = %s
              AND acessorias_company_id = %s
            FOR UPDATE
            """,
            (contact_id, company_id),
        ).fetchone()
        if existing is None:
            raise RuntimeError("identity link upsert returned no row")
        return int(existing[0])
    link_id = int(inserted[0])
    _insert_transition(
        connection,
        link_id=link_id,
        from_state=None,
        to_state="candidate",
        source=AUTOMATIC_SOURCE,
        reason="discovery",
        transition_key=f"candidate:{link_id}",
    )
    return link_id


def _persist_evidence(
    connection: psycopg.Connection[Any],
    *,
    contact_id: int,
    evidence: MatchEvidence,
    rule_version: str,
    observed_at: datetime,
) -> int:
    row = connection.execute(
        """
        INSERT INTO identity_match_evidence (
            digisac_contact_id, acessorias_company_contact_id,
            acessorias_company_id, evidence_type, value_fingerprint,
            source, rule_version, observed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (
            digisac_contact_id, acessorias_company_contact_id, evidence_type,
            value_fingerprint, rule_version
        ) DO UPDATE SET observed_at = EXCLUDED.observed_at
        RETURNING id
        """,
        (
            contact_id,
            evidence.acessorias_contact_id,
            evidence.acessorias_company_id,
            evidence.evidence_type,
            evidence.value_fingerprint,
            AUTOMATIC_SOURCE,
            rule_version,
            observed_at,
        ),
    ).fetchone()
    if row is None:
        raise RuntimeError("identity evidence upsert returned no row")
    return int(row[0])


def _current_links(
    connection: psycopg.Connection[Any], contact_id: int
) -> Sequence[Mapping[str, Any]]:
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """
            SELECT id, acessorias_company_id, state
            FROM identity_company_links
            WHERE digisac_contact_id = %s
            ORDER BY id
            FOR UPDATE
            """,
            (contact_id,),
        ).fetchall()


def _discover_locked(
    connection: psycopg.Connection[Any],
    *,
    contact_id: int,
    rule_version: str,
    observed_at: datetime,
) -> IdentityDiscoveryResult:
    contact = _lock_contact(connection, contact_id)
    directory = _load_directory_contacts(connection)
    matches = match_identity(
        DigiSacMatchInput(
            contact_id=contact_id,
            normalized_number=contact["normalized_number"],
            normalized_email=contact["normalized_email"],
            is_group=contact["is_group"],
        ),
        directory,
    )

    for evidence in matches:
        _persist_evidence(
            connection,
            contact_id=contact_id,
            evidence=evidence,
            rule_version=rule_version,
            observed_at=observed_at,
        )
        _upsert_candidate_link(
            connection,
            contact_id=contact_id,
            company_id=evidence.acessorias_company_id,
        )

    company_ids = tuple(sorted({item.acessorias_company_id for item in matches}))
    links = _current_links(connection, contact_id)
    confirmed = [row for row in links if row["state"] == "confirmed"]
    if len(confirmed) == 1:
        state = "confirmed"
        company_ids = (int(confirmed[0]["acessorias_company_id"]),)
    elif len(confirmed) > 1:
        state = "conflict"
    elif len(company_ids) == 1:
        state = "candidate"
    elif len(company_ids) > 1:
        state = "ambiguous"
    else:
        state = "unresolved"
    link_ids = tuple(
        int(row["id"])
        for row in links
        if int(row["acessorias_company_id"]) in company_ids
    )
    if state == "confirmed":
        link_ids = (int(confirmed[0]["id"]),)
    return IdentityDiscoveryResult(
        digisac_contact_id=contact_id,
        state=state,
        company_ids=company_ids,
        link_ids=link_ids,
        evidence_count=len(matches),
    )


def _discover_identity_sync(
    contact_id: int,
    *,
    rule_version: str = DEFAULT_RULE_VERSION,
    observed_at: str | datetime | None = None,
) -> dict[str, Any]:
    safe_rule = _safe_value(rule_version, "rule_version")
    timestamp = (
        _parse_timestamp(observed_at)
        if isinstance(observed_at, (str, datetime))
        else datetime.now(timezone.utc)
    )
    with get_database_pool().connection() as connection:
        with connection.transaction():
            result = _discover_locked(
                connection,
                contact_id=contact_id,
                rule_version=safe_rule,
                observed_at=timestamp,
            )
    payload = result.as_dict()
    logger.info(
        "Identity discovery completed: contact_id=%s state=%s companies=%s evidence=%s",
        payload["digisac_contact_id"],
        payload["state"],
        len(payload["company_ids"]),
        payload["evidence_count"],
    )
    return payload


async def discover_identity(
    contact_id: int,
    *,
    rule_version: str = DEFAULT_RULE_VERSION,
    observed_at: str | datetime | None = None,
) -> dict[str, Any]:
    """Persist deterministic evidence/candidate links for one contact."""
    return await asyncio.to_thread(
        _discover_identity_sync,
        contact_id,
        rule_version=rule_version,
        observed_at=observed_at,
    )


def _resolution_reason(state: str, *, company_count: int, is_group: bool | None) -> str:
    if state == "confirmed":
        return "confirmed_link"
    if state == "conflict":
        return "multiple_confirmed_links"
    if state == "ambiguous":
        return "multiple_candidate_companies"
    if is_group is not False:
        return "group_contact"
    if company_count == 1:
        return "candidate_pending_confirmation"
    return "no_candidate"


def _resolve_cycle_identity_sync(
    cycle_public_id: str,
    contact_id: int,
    *,
    rule_version: str = DEFAULT_RULE_VERSION,
    resolved_at: str | datetime | None = None,
) -> dict[str, Any]:
    safe_rule = _safe_value(rule_version, "rule_version")
    timestamp = (
        _parse_timestamp(resolved_at)
        if isinstance(resolved_at, (str, datetime))
        else datetime.now(timezone.utc)
    )
    with get_database_pool().connection() as connection:
        with connection.transaction():
            cycle = connection.execute(
                """
                SELECT id, public_id
                FROM conversation_processing_cycles
                WHERE public_id = %s
                FOR UPDATE
                """,
                (cycle_public_id,),
            ).fetchone()
            if cycle is None:
                raise LookupError("conversation cycle not found")
            with connection.cursor(row_factory=dict_row) as cursor:
                existing = cursor.execute(
                    """
                    SELECT resolution.*, cycle.public_id AS cycle_public_id
                    FROM conversation_cycle_identity_resolutions AS resolution
                    JOIN conversation_processing_cycles AS cycle
                      ON cycle.id = resolution.cycle_id
                    WHERE resolution.cycle_id = %s
                    FOR UPDATE
                    """,
                    (cycle[0],),
                ).fetchone()
            if existing is not None:
                serialized = _serialize_row(existing)
                if serialized is None:
                    raise RuntimeError("cycle identity resolution is unavailable")
                logger.info(
                    "Cycle identity resolution replayed: cycle_id=%s contact_id=%s state=%s",
                    cycle_public_id,
                    contact_id,
                    serialized["state"],
                )
                return serialized

            discovery = _discover_locked(
                connection,
                contact_id=contact_id,
                rule_version=safe_rule,
                observed_at=timestamp,
            )
            contact = connection.execute(
                "SELECT is_group FROM digisac_contacts WHERE id = %s",
                (contact_id,),
            ).fetchone()
            if contact is None:
                raise LookupError("DigiSac contact not found")
            state = discovery.state
            if state == "candidate":
                state = "unresolved"
            link_id = discovery.link_ids[0] if state == "confirmed" else None
            reason = _resolution_reason(
                state,
                company_count=len(discovery.company_ids),
                is_group=contact[0],
            )
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    """
                    INSERT INTO conversation_cycle_identity_resolutions (
                        cycle_id, digisac_contact_id, state, origin, reason,
                        link_id, resolved_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        cycle[0],
                        contact_id,
                        state,
                        AUTOMATIC_SOURCE,
                        reason,
                        link_id,
                        timestamp,
                    ),
                ).fetchone()
            serialized = _serialize_row(row)
            if serialized is None:
                raise RuntimeError("PostgreSQL did not return cycle resolution")
            serialized["cycle_public_id"] = str(cycle[1])
            logger.info(
                "Cycle identity resolution persisted: cycle_id=%s contact_id=%s state=%s",
                cycle_public_id,
                contact_id,
                serialized["state"],
            )
            return serialized


async def resolve_cycle_identity(
    cycle_public_id: str,
    contact_id: int,
    *,
    rule_version: str = DEFAULT_RULE_VERSION,
    resolved_at: str | datetime | None = None,
) -> dict[str, Any]:
    """Persist one immutable identity outcome for a conversation cycle."""
    return await asyncio.to_thread(
        _resolve_cycle_identity_sync,
        cycle_public_id,
        contact_id,
        rule_version=rule_version,
        resolved_at=resolved_at,
    )


def _confirm_identity_link_locked(
    connection: psycopg.Connection[Any],
    *,
    contact_id: int,
    company_id: int,
    confirmed_at: datetime,
    confirmed_by: str | None,
    source: str = MANUAL_SOURCE,
    confirmation_source: str = MANUAL_SOURCE,
    transition_reason: str = "manual_confirmation",
) -> Mapping[str, Any]:
    _lock_contact(connection, contact_id)
    company = connection.execute(
        """
        SELECT id, is_present, is_active
        FROM acessorias_companies
        WHERE id = %s
        FOR UPDATE
        """,
        (company_id,),
    ).fetchone()
    if company is None:
        raise LookupError("Acessorias company not found")
    if not company[1] or company[2] is False:
        raise IdentityResolutionError(
            "directory_company_unavailable",
            "Acessorias company is not currently available",
        )
    competing = connection.execute(
        """
        SELECT id
        FROM identity_company_links
        WHERE digisac_contact_id = %s
          AND state = 'confirmed'
          AND acessorias_company_id <> %s
        FOR UPDATE
        """,
        (contact_id, company_id),
    ).fetchone()
    if competing is not None:
        raise IdentityConflictError()
    with connection.cursor(row_factory=dict_row) as cursor:
        existing = cursor.execute(
            """
            SELECT *
            FROM identity_company_links
            WHERE digisac_contact_id = %s AND acessorias_company_id = %s
            FOR UPDATE
            """,
            (contact_id, company_id),
        ).fetchone()
    if existing is None:
        with connection.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute(
                """
                INSERT INTO identity_company_links (
                    digisac_contact_id, acessorias_company_id, state, source,
                    confirmation_source, confirmed_at, confirmed_by
                ) VALUES (%s, %s, 'confirmed', %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    contact_id,
                    company_id,
                    source,
                    confirmation_source,
                    confirmed_at,
                    confirmed_by,
                ),
            ).fetchone()
        if row is None:
            raise RuntimeError("PostgreSQL did not return confirmed identity link")
        _insert_transition(
            connection,
            link_id=int(row["id"]),
            from_state=None,
            to_state="confirmed",
            source=source,
            reason=transition_reason,
            transition_key=f"confirm:{source}:{row['id']}:{confirmed_at.isoformat()}",
            confirmation_source=confirmation_source,
            confirmed_at=confirmed_at,
            confirmed_by=confirmed_by,
        )
        return row
    if existing["state"] == "confirmed":
        return existing
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            """
            UPDATE identity_company_links
            SET state = 'confirmed', source = %s, confirmation_source = %s,
                confirmed_at = %s, confirmed_by = %s, rejection_reason = NULL,
                updated_at = now()
            WHERE id = %s
            RETURNING *
            """,
            (
                source,
                confirmation_source,
                confirmed_at,
                confirmed_by,
                existing["id"],
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("PostgreSQL did not update identity link")
    _insert_transition(
        connection,
        link_id=int(row["id"]),
        from_state=str(existing["state"]),
        to_state="confirmed",
        source=source,
        reason=transition_reason,
        transition_key=f"confirm:{source}:{row['id']}:{confirmed_at.isoformat()}",
        confirmation_source=confirmation_source,
        confirmed_at=confirmed_at,
        confirmed_by=confirmed_by,
    )
    return row


def _confirm_identity_link_sync(
    contact_id: int,
    company_id: int,
    *,
    confirmed_at: str | datetime | None,
    confirmed_by: str | None,
) -> dict[str, Any]:
    if confirmed_at is None:
        raise ValueError("confirmed_at is required for manual confirmation")
    timestamp = _parse_timestamp(confirmed_at)
    actor = None if confirmed_by is None else _safe_value(confirmed_by, "confirmed_by")
    with get_database_pool().connection() as connection:
        with connection.transaction():
            row = _confirm_identity_link_locked(
                connection,
                contact_id=contact_id,
                company_id=company_id,
                confirmed_at=timestamp,
                confirmed_by=actor,
            )
    serialized = _serialize_row(row)
    if serialized is None:
        raise RuntimeError("identity link is unavailable")
    return serialized


async def confirm_identity_link(
    contact_id: int,
    company_id: int,
    *,
    confirmed_at: str | datetime | None = None,
    confirmed_by: str | None = None,
) -> dict[str, Any]:
    """Confirm one explicit local contact/company pair via ``manual_db``."""
    return await asyncio.to_thread(
        _confirm_identity_link_sync,
        contact_id,
        company_id,
        confirmed_at=confirmed_at,
        confirmed_by=confirmed_by,
    )


def _confirm_identity_by_external_ids_sync(
    digisac_external_id: str,
    acessorias_company_external_id: str,
    *,
    confirmed_at: str | datetime | None,
    confirmed_by: str | None,
) -> dict[str, Any]:
    if confirmed_at is None:
        raise ValueError("confirmed_at is required for manual confirmation")
    timestamp = _parse_timestamp(confirmed_at)
    actor = None if confirmed_by is None else _safe_value(confirmed_by, "confirmed_by")
    with get_database_pool().connection() as connection:
        with connection.transaction():
            ids = connection.execute(
                """
                SELECT contact.id, company.id
                FROM digisac_contacts AS contact
                CROSS JOIN acessorias_companies AS company
                WHERE contact.external_id = %s
                  AND company.external_id = %s
                """,
                (digisac_external_id, acessorias_company_external_id),
            ).fetchone()
            if ids is None:
                raise LookupError("identity confirmation records not found")
            row = _confirm_identity_link_locked(
                connection,
                contact_id=int(ids[0]),
                company_id=int(ids[1]),
                confirmed_at=timestamp,
                confirmed_by=actor,
            )
    serialized = _serialize_row(row)
    if serialized is None:
        raise RuntimeError("identity link is unavailable")
    return serialized


async def confirm_identity_by_external_ids(
    digisac_external_id: str,
    acessorias_company_external_id: str,
    *,
    confirmed_at: str | datetime | None = None,
    confirmed_by: str | None = None,
) -> dict[str, Any]:
    """Confirm a pair using the two opaque provider identities."""
    return await asyncio.to_thread(
        _confirm_identity_by_external_ids_sync,
        digisac_external_id,
        acessorias_company_external_id,
        confirmed_at=confirmed_at,
        confirmed_by=confirmed_by,
    )


def _reject_identity_link_locked(
    connection: psycopg.Connection[Any],
    *,
    contact_id: int,
    company_id: int,
    safe_reason: str,
    source: str,
    actor: str | None,
    append_same_state_transition: bool,
) -> Mapping[str, Any]:
    _lock_contact(connection, contact_id)
    with connection.cursor(row_factory=dict_row) as cursor:
        existing = cursor.execute(
            """
            SELECT *
            FROM identity_company_links
            WHERE digisac_contact_id = %s AND acessorias_company_id = %s
            FOR UPDATE
            """,
            (contact_id, company_id),
        ).fetchone()
    if existing is None:
        raise LookupError("identity link not found")
    if (
        not append_same_state_transition
        and existing["state"] == "rejected"
        and existing["rejection_reason"] == safe_reason
    ):
        return existing
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            """
            UPDATE identity_company_links
            SET state = 'rejected', source = %s,
                confirmation_source = NULL, confirmed_at = NULL,
                confirmed_by = NULL, rejection_reason = %s,
                updated_at = now()
            WHERE id = %s
            RETURNING *
            """,
            (source, safe_reason, existing["id"]),
        ).fetchone()
    if row is None:
        raise RuntimeError("PostgreSQL did not reject identity link")
    _insert_transition(
        connection,
        link_id=int(row["id"]),
        from_state=str(existing["state"]),
        to_state="rejected",
        source=source,
        reason=safe_reason,
        transition_key=f"reject:{source}:{row['id']}:{safe_reason}:{row['updated_at'].isoformat()}",
        confirmed_by=actor,
    )
    return row


def _reject_identity_link_sync(
    contact_id: int,
    company_id: int,
    *,
    reason: str,
) -> dict[str, Any]:
    safe_reason = _safe_reason(reason)
    with get_database_pool().connection() as connection:
        with connection.transaction():
            row = _reject_identity_link_locked(
                connection,
                contact_id=contact_id,
                company_id=company_id,
                safe_reason=safe_reason,
                source=MANUAL_SOURCE,
                actor=None,
                append_same_state_transition=False,
            )
    serialized = _serialize_row(row)
    if serialized is None:
        raise RuntimeError("identity link is unavailable")
    return serialized


async def reject_identity_link(
    contact_id: int, company_id: int, *, reason: str
) -> dict[str, Any]:
    """Record an auditable rejection/correction without deleting history."""
    return await asyncio.to_thread(
        _reject_identity_link_sync, contact_id, company_id, reason=reason
    )


def _admin_command_replay(
    row: Mapping[str, Any],
    *,
    request_fingerprint: str,
) -> dict[str, Any]:
    if row["request_fingerprint"] != request_fingerprint:
        raise IdentityCommandConflictError()
    result = row["result_json"]
    if row["state"] != "completed" or not isinstance(result, dict):
        raise IdentityCommandConflictError()
    return {"replayed": True, "result": result}


def _execute_admin_identity_link_command_sync(
    *,
    operation: str,
    digisac_contact_external_id: str,
    acessorias_company_external_id: str,
    reason: str,
    idempotency_key: str,
) -> dict[str, Any]:
    safe_contact_external_id = _safe_opaque_reference(
        digisac_contact_external_id, "digisac_contact_external_id"
    )
    safe_company_external_id = _safe_opaque_reference(
        acessorias_company_external_id, "acessorias_company_external_id"
    )
    safe_reason = _safe_reason(reason)
    safe_key = _safe_opaque_reference(idempotency_key, "idempotency_key")
    command_key_hash = _fingerprint(safe_key)
    request_fingerprint = _admin_command_fingerprint(
        operation=operation,
        digisac_contact_external_id=safe_contact_external_id,
        acessorias_company_external_id=safe_company_external_id,
        reason=safe_reason,
    )

    with get_database_pool().connection() as connection:
        with connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                existing_command = cursor.execute(
                    """
                    SELECT *
                    FROM identity_admin_commands
                    WHERE command_key_hash = %s
                    FOR UPDATE
                    """,
                    (command_key_hash,),
                ).fetchone()
            if existing_command is not None:
                return _admin_command_replay(
                    existing_command, request_fingerprint=request_fingerprint
                )

            with connection.cursor(row_factory=dict_row) as cursor:
                contact = cursor.execute(
                    """
                    SELECT id
                    FROM digisac_contacts
                    WHERE external_id = %s
                    """,
                    (safe_contact_external_id,),
                ).fetchone()
                if contact is None:
                    raise LookupError("DigiSac contact not found")
                company = cursor.execute(
                    """
                    SELECT id
                    FROM acessorias_companies
                    WHERE external_id = %s
                    """,
                    (safe_company_external_id,),
                ).fetchone()
                if company is None:
                    raise LookupError("Acessorias company not found")

            with connection.cursor(row_factory=dict_row) as cursor:
                command = cursor.execute(
                    """
                    INSERT INTO identity_admin_commands (
                        command_key_hash, operation, digisac_contact_id,
                        acessorias_company_id, request_fingerprint
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (command_key_hash) DO NOTHING
                    RETURNING *
                    """,
                    (
                        command_key_hash,
                        operation,
                        int(contact["id"]),
                        int(company["id"]),
                        request_fingerprint,
                    ),
                ).fetchone()
            if command is None:
                with connection.cursor(row_factory=dict_row) as cursor:
                    command = cursor.execute(
                        """
                        SELECT *
                        FROM identity_admin_commands
                        WHERE command_key_hash = %s
                        FOR UPDATE
                        """,
                        (command_key_hash,),
                    ).fetchone()
                if command is None:
                    raise RuntimeError("identity command reservation is unavailable")
                return _admin_command_replay(
                    command, request_fingerprint=request_fingerprint
                )

            contact_id = int(contact["id"])
            company_id = int(company["id"])
            if operation == ADMIN_CONFIRM_OPERATION:
                row = _confirm_identity_link_locked(
                    connection,
                    contact_id=contact_id,
                    company_id=company_id,
                    confirmed_at=datetime.now(timezone.utc),
                    confirmed_by=ADMIN_ACTOR,
                    source=ADMIN_SOURCE,
                    confirmation_source=ADMIN_SOURCE,
                    transition_reason=safe_reason,
                )
            elif operation == ADMIN_REJECT_OPERATION:
                row = _reject_identity_link_locked(
                    connection,
                    contact_id=contact_id,
                    company_id=company_id,
                    safe_reason=safe_reason,
                    source=ADMIN_SOURCE,
                    actor=ADMIN_ACTOR,
                    append_same_state_transition=True,
                )
            else:
                raise RuntimeError("unsupported administrative identity command")

            result = _admin_link_result(
                row,
                digisac_contact_external_id=safe_contact_external_id,
                acessorias_company_external_id=safe_company_external_id,
            )
            connection.execute(
                """
                UPDATE identity_admin_commands
                SET state = 'completed', result_json = %s, completed_at = now()
                WHERE id = %s
                """,
                (Jsonb(result), command["id"]),
            )
            return {"replayed": False, "result": result}


async def confirm_identity_link_admin(
    digisac_contact_external_id: str,
    acessorias_company_external_id: str,
    *,
    reason: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Confirm one external-ID pair with durable admin API idempotency."""
    return await asyncio.to_thread(
        _execute_admin_identity_link_command_sync,
        operation=ADMIN_CONFIRM_OPERATION,
        digisac_contact_external_id=digisac_contact_external_id,
        acessorias_company_external_id=acessorias_company_external_id,
        reason=reason,
        idempotency_key=idempotency_key,
    )


async def reject_identity_link_admin(
    digisac_contact_external_id: str,
    acessorias_company_external_id: str,
    *,
    reason: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Reject one external-ID pair with durable admin API idempotency."""
    return await asyncio.to_thread(
        _execute_admin_identity_link_command_sync,
        operation=ADMIN_REJECT_OPERATION,
        digisac_contact_external_id=digisac_contact_external_id,
        acessorias_company_external_id=acessorias_company_external_id,
        reason=reason,
        idempotency_key=idempotency_key,
    )


def _get_cycle_identity_resolution_sync(
    cycle_public_id: str,
) -> dict[str, Any] | None:
    with get_database_pool().connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute(
                """
                SELECT resolution.*, cycle.public_id AS cycle_public_id
                FROM conversation_cycle_identity_resolutions AS resolution
                JOIN conversation_processing_cycles AS cycle
                  ON cycle.id = resolution.cycle_id
                WHERE cycle.public_id = %s
                """,
                (cycle_public_id,),
            ).fetchone()
    return _serialize_row(row)


async def get_cycle_identity_resolution(
    cycle_public_id: str,
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        _get_cycle_identity_resolution_sync, cycle_public_id
    )


def _list_identity_evidence_sync(contact_id: int) -> list[dict[str, Any]]:
    with get_database_pool().connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            rows = cursor.execute(
                """
                SELECT *
                FROM identity_match_evidence
                WHERE digisac_contact_id = %s
                ORDER BY id
                """,
                (contact_id,),
            ).fetchall()
    return [_serialize_row(row) or {} for row in rows]


async def list_identity_evidence(contact_id: int) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_list_identity_evidence_sync, contact_id)
