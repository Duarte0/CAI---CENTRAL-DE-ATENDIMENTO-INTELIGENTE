"""PostgreSQL persistence for DigiSac ticket-assignment history.

The repository uses the process-local pool owned by ``src.core.db`` while
keeping assignment event idempotency, chronological history, and directory
name projection together.  The database facade re-exports the async
operations for existing routes, workers, and tests.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any


def _record_ticket_assignment_sync(
    *,
    conversation_id: str,
    department_id: str | None,
    user_id: str | None,
    event_timestamp: str,
    event_key: str,
    source_event_id: str | None = None,
    ticket_transfer_count: int | None = None,
) -> bool:
    if department_id is None and user_id is None:
        return False
    from src.core.db import _parse_timestamp, get_database_pool

    now = datetime.now(timezone.utc)
    with get_database_pool().connection() as connection:
        with connection.transaction():
            inserted = connection.execute(
                """
                INSERT INTO ticket_assignment_event_keys (
                    event_key, conversation_id, created_at
                ) VALUES (%s, %s, %s)
                ON CONFLICT (event_key) DO NOTHING
                RETURNING event_key
                """,
                (event_key, conversation_id, now),
            ).fetchone()
            if inserted is None:
                return False
            previous = connection.execute(
                """
                SELECT department_id, user_id
                FROM ticket_assignment_history
                WHERE conversation_id = %s
                ORDER BY event_timestamp DESC, id DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
            if previous == (department_id, user_id):
                return False
            connection.execute(
                """
                INSERT INTO ticket_assignment_history (
                    conversation_id, department_id, user_id, event_timestamp,
                    source_event_id, event_key, ticket_transfer_count, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_key) DO NOTHING
                """,
                (
                    conversation_id,
                    department_id,
                    user_id,
                    _parse_timestamp(event_timestamp),
                    source_event_id,
                    event_key,
                    ticket_transfer_count,
                    now,
                ),
            )
            return True


async def record_ticket_assignment(**kwargs: Any) -> bool:
    return await asyncio.to_thread(_record_ticket_assignment_sync, **kwargs)


def _resolve_ticket_assignments_sync(
    conversation_id: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    from src.core.db import get_database_pool

    with get_database_pool().connection() as connection:
        rows = connection.execute(
            """
            SELECT h.department_id, d.name, h.user_id, u.name
            FROM ticket_assignment_history AS h
            LEFT JOIN digisac_departments AS d ON d.id = h.department_id
            LEFT JOIN digisac_users AS u ON u.id = h.user_id
            WHERE h.conversation_id = %s
            ORDER BY h.event_timestamp ASC, h.id ASC
            """,
            (conversation_id,),
        ).fetchall()
    departments: list[str] = []
    agents: list[str] = []
    unresolved_departments: list[str] = []
    unresolved_users: list[str] = []
    seen_departments: set[str] = set()
    seen_agents: set[str] = set()
    seen_unresolved_departments: set[str] = set()
    seen_unresolved_users: set[str] = set()
    for department_id, department_name, user_id, user_name in rows:
        if isinstance(department_name, str) and department_name.strip():
            normalized = department_name.strip()
            if normalized not in seen_departments:
                seen_departments.add(normalized)
                departments.append(normalized)
        elif department_id and department_id not in seen_unresolved_departments:
            seen_unresolved_departments.add(department_id)
            unresolved_departments.append(department_id)
        if isinstance(user_name, str) and user_name.strip():
            normalized = user_name.strip()
            if normalized not in seen_agents:
                seen_agents.add(normalized)
                agents.append(normalized)
        elif user_id and user_id not in seen_unresolved_users:
            seen_unresolved_users.add(user_id)
            unresolved_users.append(user_id)
    return departments, agents, unresolved_departments, unresolved_users


async def resolve_ticket_assignments(
    conversation_id: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    return await asyncio.to_thread(
        _resolve_ticket_assignments_sync, conversation_id
    )
