"""PostgreSQL-authoritative DigiSac-to-Acessorias department mapping.

Rules are administered through the controlled ``manual_db`` boundary and cycle
results are append-only snapshots.  This module deliberately does not inspect
IA output, names, or Redis state when selecting a department.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Mapping

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from src.core.db import get_database_pool

MANUAL_SOURCE = "manual_db"
_SAFE_REASON = re.compile(r"^[a-z0-9_:-]{1,120}$")
_SAFE_OPERATION = re.compile(r"^[a-z0-9_.:@-]{1,240}$")
_SAFE_ACTOR = re.compile(r"^[a-z0-9_.:@-]{1,120}$")
_SAFE_METADATA_KEY = re.compile(r"^[a-z0-9_.:-]{1,64}$")
_FORBIDDEN_METADATA_KEY_PARTS = frozenset(
    {"body", "content", "email", "header", "name", "phone", "payload", "secret", "token"}
)
logger = logging.getLogger(__name__)


class DepartmentMappingError(RuntimeError):
    """A sanitized department-mapping operation failure."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.safe_message = message


class DepartmentMappingConflictError(DepartmentMappingError):
    """A requested operation conflicts with an existing mapping."""

    def __init__(self, message: str = "department mapping operation conflicts") -> None:
        super().__init__("mapping_conflict", message)


def _parse_timestamp(value: str | datetime) -> datetime:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _external_id(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 240 or any(
        character in normalized for character in "\r\n\x00"
    ):
        raise ValueError(f"{field} must be a nonblank stable ID")
    return normalized


def _safe_reason(value: str) -> str:
    normalized = value.strip().lower()
    if not _SAFE_REASON.fullmatch(normalized):
        raise ValueError("reason must be a safe nonblank category")
    return normalized


def _safe_actor(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not _SAFE_ACTOR.fullmatch(normalized):
        raise ValueError("actor must be a safe administrative identity")
    return normalized


def _safe_operation(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not _SAFE_OPERATION.fullmatch(normalized):
        raise ValueError("operation_key must be a safe nonblank value")
    return normalized


def _sanitize_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if metadata is None:
        return {}
    sanitized: dict[str, Any] = {}
    for raw_key, value in metadata.items():
        key = raw_key.strip().lower()
        if not _SAFE_METADATA_KEY.fullmatch(key) or any(
            part in _FORBIDDEN_METADATA_KEY_PARTS for part in key.split(".")
        ):
            raise ValueError("metadata contains an unsafe key")
        if value is None or isinstance(value, bool):
            sanitized[key] = value
        elif isinstance(value, int) and not isinstance(value, bool):
            sanitized[key] = value
        elif isinstance(value, str) and _SAFE_OPERATION.fullmatch(value.strip().lower()):
            sanitized[key] = value.strip().lower()
        else:
            raise ValueError("metadata values must be safe scalar values")
    return sanitized


def _serialize_row(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result: dict[str, Any] = {}
    for key, value in row.items():
        result[key] = value.isoformat() if isinstance(value, datetime) else value
    return result


def _rule_by_id(
    cursor: psycopg.Cursor[Any], rule_id: int
) -> Mapping[str, Any] | None:
    return cursor.execute(
        "SELECT * FROM department_mapping_rules WHERE id = %s",
        (rule_id,),
    ).fetchone()


def _transition(
    cursor: psycopg.Cursor[Any],
    *,
    rule_id: int,
    from_state: str | None,
    to_state: str,
    reason: str,
    operation_key: str,
    actor: str | None,
) -> None:
    inserted = cursor.execute(
        """
        INSERT INTO department_mapping_transitions (
            rule_id, from_state, to_state, source, reason,
            operation_key, actor
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (operation_key) DO NOTHING
        RETURNING id
        """,
        (
            rule_id,
            from_state,
            to_state,
            MANUAL_SOURCE,
            reason,
            operation_key,
            actor,
        ),
    ).fetchone()
    if inserted is not None:
        return
    existing = cursor.execute(
        """
        SELECT rule_id, to_state, reason, actor
        FROM department_mapping_transitions
        WHERE operation_key = %s
        """,
        (operation_key,),
    ).fetchone()
    if existing is None:
        raise RuntimeError("department mapping transition was not persisted")
    if (
        existing["rule_id"],
        existing["to_state"],
        existing["reason"],
        existing["actor"],
    ) != (rule_id, to_state, reason, actor):
        raise DepartmentMappingConflictError()


def _configure_department_mapping_sync(
    digisac_department_external_id: str,
    acessorias_department_external_id: str | None,
    *,
    active: bool,
    reason: str,
    actor: str | None,
    operation_key: str | None,
    metadata: Mapping[str, Any] | None,
    effective_at: str | datetime | None,
) -> dict[str, Any]:
    digisac_id = _external_id(
        digisac_department_external_id, "digisac_department_external_id"
    )
    target_id = (
        None
        if acessorias_department_external_id is None
        else _external_id(
            acessorias_department_external_id,
            "acessorias_department_external_id",
        )
    )
    safe_reason = _safe_reason(reason)
    safe_actor = _safe_actor(actor)
    safe_metadata = _sanitize_metadata(metadata)
    safe_operation = _safe_operation(operation_key)
    timestamp = (
        _parse_timestamp(effective_at)
        if isinstance(effective_at, (str, datetime))
        else datetime.now(timezone.utc)
    )

    with get_database_pool().connection() as connection:
        with connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"cai:department_mapping:{digisac_id}",),
                )
                if safe_operation is None:
                    operation_target = target_id or "current"
                    digest = hashlib.sha256(
                        "|".join(
                            (
                                MANUAL_SOURCE,
                                "active" if active else "inactive",
                                digisac_id,
                                operation_target,
                                safe_reason,
                            )
                        ).encode("utf-8")
                    ).hexdigest()
                    safe_operation = f"{MANUAL_SOURCE}:{digest}"

                replay = cursor.execute(
                    """
                    SELECT transition.rule_id,
                           transition.to_state,
                           transition.reason,
                           rule.acessorias_department_external_id
                    FROM department_mapping_transitions
                    AS transition
                    JOIN department_mapping_rules AS rule
                      ON rule.id = transition.rule_id
                    WHERE operation_key = %s
                    """,
                    (safe_operation,),
                ).fetchone()
                if replay is not None:
                    expected_state = "active" if active else "inactive"
                    if (
                        replay["to_state"] != expected_state
                        or replay["reason"] != safe_reason
                        or (
                            target_id is not None
                            and replay["acessorias_department_external_id"] != target_id
                        )
                    ):
                        raise DepartmentMappingConflictError(
                            "operation_key was already used for another mapping operation"
                        )
                    row = _rule_by_id(cursor, int(replay["rule_id"]))
                    serialized = _serialize_row(row)
                    if serialized is None:
                        raise RuntimeError("replayed department mapping is unavailable")
                    return serialized

                if cursor.execute(
                    "SELECT 1 FROM digisac_departments WHERE id = %s",
                    (digisac_id,),
                ).fetchone() is None:
                    raise LookupError("DigiSac department not found")

                current = cursor.execute(
                    """
                    SELECT *
                    FROM department_mapping_rules
                    WHERE digisac_department_external_id = %s
                      AND state = 'active'
                    FOR UPDATE
                    """,
                    (digisac_id,),
                ).fetchone()

                if not active and current is not None and target_id is not None:
                    if current["acessorias_department_external_id"] != target_id:
                        raise DepartmentMappingConflictError(
                            "requested inactivation targets a different active rule"
                        )

                if target_id is None:
                    if current is None:
                        raise ValueError(
                            "acessorias_department_external_id is required without an active rule"
                        )
                    target_id = str(current["acessorias_department_external_id"])

                if cursor.execute(
                    "SELECT 1 FROM acessorias_departments WHERE external_id = %s",
                    (target_id,),
                ).fetchone() is None:
                    raise LookupError("Acessorias department not found")

                if active:
                    if current is not None:
                        old_target = str(current["acessorias_department_external_id"])
                        if old_target == target_id:
                            _transition(
                                cursor,
                                rule_id=int(current["id"]),
                                from_state="active",
                                to_state="active",
                                reason=safe_reason,
                                operation_key=safe_operation,
                                actor=safe_actor,
                            )
                            return _serialize_row(current) or {}
                        cursor.execute(
                            """
                            UPDATE department_mapping_rules
                            SET state = 'inactive', updated_at = now()
                            WHERE id = %s
                            """,
                            (current["id"],),
                        )
                        _transition(
                            cursor,
                            rule_id=int(current["id"]),
                            from_state="active",
                            to_state="inactive",
                            reason="mapping_replaced",
                            operation_key=f"{safe_operation}:deactivate",
                            actor=safe_actor,
                        )

                    version = cursor.execute(
                        """
                        SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                        FROM department_mapping_rules
                        WHERE digisac_department_external_id = %s
                        """,
                        (digisac_id,),
                    ).fetchone()
                    if version is None:
                        raise RuntimeError("department mapping version is unavailable")
                    row = cursor.execute(
                        """
                        INSERT INTO department_mapping_rules (
                            digisac_department_external_id,
                            acessorias_department_external_id,
                            version, state, source, reason, actor,
                            metadata_json, effective_at
                        ) VALUES (%s, %s, %s, 'active', %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (
                            digisac_id,
                            target_id,
                            int(version["next_version"]),
                            MANUAL_SOURCE,
                            safe_reason,
                            safe_actor,
                            Jsonb(safe_metadata),
                            timestamp,
                        ),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("department mapping was not persisted")
                    _transition(
                        cursor,
                        rule_id=int(row["id"]),
                        from_state=None,
                        to_state="active",
                        reason=safe_reason,
                        operation_key=safe_operation,
                        actor=safe_actor,
                    )
                    return _serialize_row(row) or {}

                if current is None:
                    latest = cursor.execute(
                        """
                        SELECT *
                        FROM department_mapping_rules
                        WHERE digisac_department_external_id = %s
                        ORDER BY version DESC
                        LIMIT 1
                        """,
                        (digisac_id,),
                    ).fetchone()
                    if latest is not None:
                        _transition(
                            cursor,
                            rule_id=int(latest["id"]),
                            from_state="inactive",
                            to_state="inactive",
                            reason=safe_reason,
                            operation_key=safe_operation,
                            actor=safe_actor,
                        )
                        return _serialize_row(latest) or {}
                    version = cursor.execute(
                        """
                        SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                        FROM department_mapping_rules
                        WHERE digisac_department_external_id = %s
                        """,
                        (digisac_id,),
                    ).fetchone()
                    if version is None:
                        raise RuntimeError("department mapping version is unavailable")
                    row = cursor.execute(
                        """
                        INSERT INTO department_mapping_rules (
                            digisac_department_external_id,
                            acessorias_department_external_id,
                            version, state, source, reason, actor,
                            metadata_json, effective_at
                        ) VALUES (%s, %s, %s, 'inactive', %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (
                            digisac_id,
                            target_id,
                            int(version["next_version"]),
                            MANUAL_SOURCE,
                            safe_reason,
                            safe_actor,
                            Jsonb(safe_metadata),
                            timestamp,
                        ),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("inactive department mapping was not persisted")
                    _transition(
                        cursor,
                        rule_id=int(row["id"]),
                        from_state=None,
                        to_state="inactive",
                        reason=safe_reason,
                        operation_key=safe_operation,
                        actor=safe_actor,
                    )
                    return _serialize_row(row) or {}

                cursor.execute(
                    """
                    UPDATE department_mapping_rules
                    SET state = 'inactive', reason = %s, actor = %s,
                        metadata_json = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (safe_reason, safe_actor, Jsonb(safe_metadata), current["id"]),
                )
                _transition(
                    cursor,
                    rule_id=int(current["id"]),
                    from_state="active",
                    to_state="inactive",
                    reason=safe_reason,
                    operation_key=safe_operation,
                    actor=safe_actor,
                )
                row = _rule_by_id(cursor, int(current["id"]))
                serialized = _serialize_row(row)
                if serialized is None:
                    raise RuntimeError("inactivated department mapping is unavailable")
                return serialized


async def configure_department_mapping(
    digisac_department_external_id: str,
    acessorias_department_external_id: str | None = None,
    *,
    active: bool = True,
    reason: str,
    actor: str | None = None,
    operation_key: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    effective_at: str | datetime | None = None,
) -> dict[str, Any]:
    """Create, activate, replace, or inactivate one stable-ID mapping rule."""
    return await asyncio.to_thread(
        _configure_department_mapping_sync,
        digisac_department_external_id,
        acessorias_department_external_id,
        active=active,
        reason=reason,
        actor=actor,
        operation_key=operation_key,
        metadata=metadata,
        effective_at=effective_at,
    )


def _persist_evaluation(
    cursor: psycopg.Cursor[Any],
    *,
    cycle_id: int,
    evaluation_key: str,
    rule_id: int | None,
    rule_version: int | None,
    digisac_department_external_id: str | None,
    acessorias_department_external_id: str | None,
    company_id: int | None,
    state: str,
    reason: str,
    validation: Mapping[str, Any],
    evaluated_at: datetime,
) -> dict[str, Any]:
    row = cursor.execute(
        """
        INSERT INTO conversation_cycle_department_mappings (
            cycle_id, evaluation_key, rule_id, rule_version,
            digisac_department_external_id, acessorias_department_external_id,
            company_id, state, reason, validation_json, evaluated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (cycle_id, evaluation_key) DO NOTHING
        RETURNING *
        """,
        (
            cycle_id,
            evaluation_key,
            rule_id,
            rule_version,
            digisac_department_external_id,
            acessorias_department_external_id,
            company_id,
            state,
            reason,
            Jsonb(dict(validation)),
            evaluated_at,
        ),
    ).fetchone()
    if row is None:
        row = cursor.execute(
            """
            SELECT *
            FROM conversation_cycle_department_mappings
            WHERE cycle_id = %s AND evaluation_key = %s
            """,
            (cycle_id, evaluation_key),
        ).fetchone()
    serialized = _serialize_row(row)
    if serialized is None:
        raise RuntimeError("department mapping evaluation is unavailable")
    return serialized


def _evaluate_department_mapping_sync(
    cycle_public_id: str,
    *,
    evaluation_key: str | None,
    evaluated_at: str | datetime | None,
) -> dict[str, Any]:
    public_id = _external_id(cycle_public_id, "cycle_public_id")
    key = _safe_operation(evaluation_key) or "default"
    timestamp = (
        _parse_timestamp(evaluated_at)
        if isinstance(evaluated_at, (str, datetime))
        else datetime.now(timezone.utc)
    )
    with get_database_pool().connection() as connection:
        with connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                cycle = cursor.execute(
                    """
                    SELECT id, conversation_id
                    FROM conversation_processing_cycles
                    WHERE public_id = %s
                    FOR UPDATE
                    """,
                    (public_id,),
                ).fetchone()
                if cycle is None:
                    raise LookupError("conversation cycle not found")
                existing = cursor.execute(
                    """
                    SELECT *
                    FROM conversation_cycle_department_mappings
                    WHERE cycle_id = %s AND evaluation_key = %s
                    FOR UPDATE
                    """,
                    (cycle["id"], key),
                ).fetchone()
                if existing is not None:
                    return _serialize_row(existing) or {}
                if key == "default":
                    existing = cursor.execute(
                        """
                        SELECT *
                        FROM conversation_cycle_department_mappings
                        WHERE cycle_id = %s
                        ORDER BY id
                        LIMIT 1
                        FOR UPDATE
                        """,
                        (cycle["id"],),
                    ).fetchone()
                    if existing is not None:
                        return _serialize_row(existing) or {}

                assignment = cursor.execute(
                    """
                    SELECT id, department_id
                    FROM ticket_assignment_history
                    WHERE conversation_id = %s AND department_id IS NOT NULL
                    ORDER BY event_timestamp DESC, id DESC
                    LIMIT 1
                    """,
                    (cycle["conversation_id"],),
                ).fetchone()
                validation: dict[str, Any] = {
                    "assignment_history_id": (
                        None if assignment is None else int(assignment["id"])
                    ),
                    "identity_resolution_id": None,
                    "identity_link_id": None,
                    "company_id": None,
                    "rule_present": False,
                    "target_department_available": False,
                    "company_available": False,
                    "relationship_available": False,
                }
                department_id = (
                    None if assignment is None else str(assignment["department_id"])
                )
                if department_id is None:
                    return _persist_evaluation(
                        cursor,
                        cycle_id=int(cycle["id"]),
                        evaluation_key=key,
                        rule_id=None,
                        rule_version=None,
                        digisac_department_external_id=None,
                        acessorias_department_external_id=None,
                        company_id=None,
                        state="unresolved",
                        reason="current_department_missing",
                        validation=validation,
                        evaluated_at=timestamp,
                    )

                validation["digisac_department_present"] = (
                    cursor.execute(
                        "SELECT 1 FROM digisac_departments WHERE id = %s",
                        (department_id,),
                    ).fetchone()
                    is not None
                )
                if not validation["digisac_department_present"]:
                    return _persist_evaluation(
                        cursor,
                        cycle_id=int(cycle["id"]),
                        evaluation_key=key,
                        rule_id=None,
                        rule_version=None,
                        digisac_department_external_id=department_id,
                        acessorias_department_external_id=None,
                        company_id=None,
                        state="invalid",
                        reason="digisac_department_unavailable",
                        validation=validation,
                        evaluated_at=timestamp,
                    )

                identity = cursor.execute(
                    """
                    SELECT resolution.id AS resolution_id,
                           resolution.state AS resolution_state,
                           resolution.link_id,
                           link.acessorias_company_id
                    FROM conversation_cycle_identity_resolutions AS resolution
                    LEFT JOIN identity_company_links AS link
                      ON link.id = resolution.link_id
                    WHERE resolution.cycle_id = %s
                    """,
                    (cycle["id"],),
                ).fetchone()
                if identity is not None:
                    validation["identity_resolution_id"] = int(identity["resolution_id"])
                    validation["identity_link_id"] = (
                        None if identity["link_id"] is None else int(identity["link_id"])
                    )
                if (
                    identity is None
                    or identity["resolution_state"] != "confirmed"
                    or identity["link_id"] is None
                    or identity["acessorias_company_id"] is None
                ):
                    return _persist_evaluation(
                        cursor,
                        cycle_id=int(cycle["id"]),
                        evaluation_key=key,
                        rule_id=None,
                        rule_version=None,
                        digisac_department_external_id=department_id,
                        acessorias_department_external_id=None,
                        company_id=None,
                        state="unresolved",
                        reason="identity_not_confirmed",
                        validation=validation,
                        evaluated_at=timestamp,
                    )

                company_id = int(identity["acessorias_company_id"])
                validation["company_id"] = company_id
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"cai:department_mapping:{department_id}",),
                )
                rule = cursor.execute(
                    """
                    SELECT *
                    FROM department_mapping_rules
                    WHERE digisac_department_external_id = %s
                      AND state = 'active'
                    FOR UPDATE
                    """,
                    (department_id,),
                ).fetchone()
                if rule is None:
                    return _persist_evaluation(
                        cursor,
                        cycle_id=int(cycle["id"]),
                        evaluation_key=key,
                        rule_id=None,
                        rule_version=None,
                        digisac_department_external_id=department_id,
                        acessorias_department_external_id=None,
                        company_id=company_id,
                        state="unresolved",
                        reason="mapping_rule_missing",
                        validation=validation,
                        evaluated_at=timestamp,
                    )

                validation["rule_present"] = True
                target_id = str(rule["acessorias_department_external_id"])
                target = cursor.execute(
                    """
                    SELECT is_present, is_active
                    FROM acessorias_departments
                    WHERE external_id = %s
                    """,
                    (target_id,),
                ).fetchone()
                target_available = target is not None and bool(
                    target["is_present"] and target["is_active"]
                )
                validation["target_department_available"] = target_available
                company = cursor.execute(
                    """
                    SELECT is_present, is_active
                    FROM acessorias_companies
                    WHERE id = %s
                    """,
                    (company_id,),
                ).fetchone()
                company_available = company is not None and bool(
                    company["is_present"] and company["is_active"]
                )
                validation["company_available"] = company_available
                relationship = cursor.execute(
                    """
                    SELECT relation.is_present, relation.is_active
                    FROM acessorias_company_departments AS relation
                    JOIN acessorias_departments AS department
                      ON department.id = relation.department_id
                    WHERE relation.company_id = %s
                      AND department.external_id = %s
                    """,
                    (company_id, target_id),
                ).fetchone()
                relationship_available = relationship is not None and bool(
                    relationship["is_present"] and relationship["is_active"]
                )
                validation["relationship_available"] = relationship_available
                if not target_available:
                    state = "invalid"
                    reason = "acessorias_department_unavailable"
                elif not company_available:
                    state = "invalid"
                    reason = "company_unavailable"
                elif not relationship_available:
                    state = "invalid"
                    reason = "company_department_unavailable"
                else:
                    state = "resolved"
                    reason = "mapping_validated"
                return _persist_evaluation(
                    cursor,
                    cycle_id=int(cycle["id"]),
                    evaluation_key=key,
                    rule_id=int(rule["id"]),
                    rule_version=int(rule["version"]),
                    digisac_department_external_id=department_id,
                    acessorias_department_external_id=target_id,
                    company_id=company_id,
                    state=state,
                    reason=reason,
                    validation=validation,
                    evaluated_at=timestamp,
                )


async def evaluate_department_mapping(
    cycle_public_id: str,
    *,
    evaluation_key: str | None = None,
    evaluated_at: str | datetime | None = None,
) -> dict[str, Any]:
    """Evaluate and append one auditable mapping snapshot for a cycle."""
    result = await asyncio.to_thread(
        _evaluate_department_mapping_sync,
        cycle_public_id,
        evaluation_key=evaluation_key,
        evaluated_at=evaluated_at,
    )
    logger.info(
        "Department mapping evaluated: cycle_id=%s department_id=%s state=%s reason=%s",
        cycle_public_id,
        result.get("digisac_department_external_id"),
        result.get("state"),
        result.get("reason"),
    )
    return result


def _get_cycle_department_mapping_sync(
    cycle_public_id: str, *, evaluation_key: str | None
) -> dict[str, Any] | None:
    public_id = _external_id(cycle_public_id, "cycle_public_id")
    key = _safe_operation(evaluation_key) or "default"
    with get_database_pool().connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute(
                """
                SELECT snapshot.*
                FROM conversation_cycle_department_mappings AS snapshot
                JOIN conversation_processing_cycles AS cycle
                  ON cycle.id = snapshot.cycle_id
                WHERE cycle.public_id = %s AND snapshot.evaluation_key = %s
                """,
                (public_id, key),
            ).fetchone()
    return _serialize_row(row)


async def get_cycle_department_mapping(
    cycle_public_id: str, *, evaluation_key: str | None = None
) -> dict[str, Any] | None:
    """Read one cycle mapping snapshot without reevaluating it."""
    return await asyncio.to_thread(
        _get_cycle_department_mapping_sync,
        cycle_public_id,
        evaluation_key=evaluation_key,
    )
