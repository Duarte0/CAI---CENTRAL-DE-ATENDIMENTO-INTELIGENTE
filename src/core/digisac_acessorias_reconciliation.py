"""Manual, PostgreSQL-authoritative DigiSac/Acessórias reconciliation.

This module is intentionally separate from the periodic DigiSac department/user
directory loop and from the one-contact administrative discovery command.  It
acquires both provider views first, computes a safe local delta, publishes both
sources under compatible PostgreSQL advisory locks, and only then runs the
domain batch discovery boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, cast
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from src.core.acessorias_directory import (
    AcessoriasCompany,
    AcessoriasContact,
    AcessoriasDepartment,
    AcessoriasDirectoryAdapter,
    AcessoriasDirectoryError,
    AcessoriasSnapshot,
    require_complete_acessorias_snapshot,
    validate_acessorias_snapshot,
)
from src.core.db import get_database_pool
from src.core.digisac_client import DigisacClient, DigisacContact, DigisacContactPage
from src.core.digisac_contact_backfill import (
    DigisacContactBackfillError,
    DigisacContactBackfillSnapshot,
    acquire_contact_backfill,
)
from src.core.digisac_contact_repository import upsert_digisac_contact_cursor
from src.core.identity_resolution import discover_all_identities

logger = logging.getLogger(__name__)

RECONCILIATION_LOCK = "cai:digisac-acessorias-reconciliation"
ACESSORIAS_LOCK = "cai:acessorias-directory"
DIGISAC_CONTACTS_LOCK = "cai:digisac_contacts:full_backfill"
MANUAL_CONTACT_SOURCE = "manual_reconciliation"
_SAFE_CATEGORY = re.compile(r"^[a-z0-9_:-]{1,80}$")


class AcessoriasReconciliationProvider(Protocol):
    def fetch_snapshot(self) -> AcessoriasSnapshot:
        """Fetch a complete, validated Acessórias view."""
        ...


class DigisacReconciliationProvider(Protocol):
    def get_contacts_page(
        self, *, page: int, per_page: int
    ) -> DigisacContactPage:
        """Fetch one validated DigiSac Contacts page."""
        ...


class ManualReconciliationError(RuntimeError):
    """A sanitized failure from the manual reconciliation boundary."""

    def __init__(self, category: str, message: str) -> None:
        safe_category = category.strip().lower()
        if not _SAFE_CATEGORY.fullmatch(safe_category):
            safe_category = "internal_error"
        super().__init__(message)
        self.category = safe_category
        self.safe_message = message[:240]


class ReconciliationInProgress(ManualReconciliationError):
    def __init__(self) -> None:
        super().__init__(
            "reconciliation_in_progress",
            "another reconciliation is already publishing",
        )


@dataclass(frozen=True)
class ReconciliationPlan:
    """Safe counts and hashes; it intentionally contains no provider values."""

    acessorias_snapshot_hash: str
    digisac_snapshot_hash: str
    resources: Mapping[str, Mapping[str, int]]
    historical_retained_count: int
    confirmed_link_count: int

    @property
    def new_count(self) -> int:
        return sum(values.get("new", 0) for values in self.resources.values())

    @property
    def changed_count(self) -> int:
        return sum(
            values.get("changed", 0) for values in self.resources.values()
        )

    @property
    def unchanged_count(self) -> int:
        return sum(
            values.get("unchanged", 0) for values in self.resources.values()
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "acessorias_snapshot_hash": self.acessorias_snapshot_hash,
            "digisac_snapshot_hash": self.digisac_snapshot_hash,
            "resources": {
                name: dict(sorted(counts.items()))
                for name, counts in sorted(self.resources.items())
            },
            "new_count": self.new_count,
            "changed_count": self.changed_count,
            "unchanged_count": self.unchanged_count,
            "historical_retained_count": self.historical_retained_count,
            "confirmed_link_count": self.confirmed_link_count,
        }


@dataclass(frozen=True)
class ManualReconciliationResult:
    execution_id: UUID
    mode: str
    status: str
    report: Mapping[str, Any]
    failure_category: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "execution_id": str(self.execution_id),
            "mode": self.mode,
            "status": self.status,
            "report": dict(self.report),
            "failure_category": self.failure_category,
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _hash_digisac_snapshot(snapshot: DigisacContactBackfillSnapshot) -> str:
    def contact_payload(contact: DigisacContact) -> dict[str, Any]:
        return {
            field: _iso(getattr(contact, field))
            for field in (
                "external_id",
                "name",
                "alternative_name",
                "internal_name",
                "raw_number",
                "normalized_number",
                "raw_email",
                "normalized_email",
                "is_group",
                "account_id",
                "service_id",
                "provider_created_at",
                "provider_updated_at",
                "provider_deleted_at",
            )
        }

    canonical = [
        contact_payload(contact)
        for contact in sorted(snapshot.contacts, key=lambda item: item.external_id)
    ]
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _counts(*keys: str) -> dict[str, int]:
    return {key: 0 for key in keys}


def _access_company_fields(company: AcessoriasCompany) -> tuple[Any, ...]:
    return (
        company.provider_id,
        company.legal_name,
        company.trade_name,
        company.provider_status,
        company.phone,
        company.uf,
        company.client_since,
        company.client_until,
        company.registered_at,
        company.is_active,
    )


def _access_department_fields(department: AcessoriasDepartment) -> tuple[Any, ...]:
    return (
        department.name,
        department.responsible_name,
        department.responsible_email,
    )


def _access_contact_fields(contact: AcessoriasContact) -> tuple[Any, ...]:
    return (
        contact.name,
        contact.raw_mobile,
        contact.normalized_mobile,
        contact.raw_email,
        contact.normalized_email,
    )


def _load_directory_rows(connection: psycopg.Connection[Any]) -> dict[str, Any]:
    with connection.cursor(row_factory=dict_row) as cursor:
        companies = cursor.execute(
            """
            SELECT external_id, provider_id, legal_name, trade_name,
                   provider_status, phone, uf, client_since, client_until,
                   registered_at, is_present, is_active
            FROM acessorias_companies
            """
        ).fetchall()
        departments = cursor.execute(
            """
            SELECT external_id, name, responsible_name, responsible_email,
                   is_present, is_active
            FROM acessorias_departments
            """
        ).fetchall()
        contacts = cursor.execute(
            """
            SELECT company.external_id AS company_external_id,
                   contact.external_key, contact.name, contact.raw_mobile,
                   contact.normalized_mobile, contact.raw_email,
                   contact.normalized_email, contact.is_present,
                   contact.is_active
            FROM acessorias_company_contacts AS contact
            JOIN acessorias_companies AS company ON company.id = contact.company_id
            """
        ).fetchall()
        relationships = cursor.execute(
            """
            SELECT company.external_id AS company_external_id,
                   department.external_id AS department_external_id,
                   relation.is_present, relation.is_active
            FROM acessorias_company_departments AS relation
            JOIN acessorias_companies AS company ON company.id = relation.company_id
            JOIN acessorias_departments AS department ON department.id = relation.department_id
            """
        ).fetchall()
        confirmed = cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM identity_company_links
            WHERE state = 'confirmed'
            """
        ).fetchone()
    return {
        "companies": {str(row["external_id"]): row for row in companies},
        "departments": {str(row["external_id"]): row for row in departments},
        "contacts": {
            (str(row["company_external_id"]), str(row["external_key"])): row
            for row in contacts
        },
        "relationships": {
            (
                str(row["company_external_id"]),
                str(row["department_external_id"]),
            ): row
            for row in relationships
        },
        "confirmed_link_count": int(confirmed["count"] if confirmed else 0),
    }


def _contact_change_kind(
    row: Mapping[str, Any] | None, contact: DigisacContact
) -> str:
    if row is None:
        return "new"
    old_updated = row["provider_updated_at"]
    new_updated = contact.provider_updated_at
    older = old_updated is not None and new_updated is not None and new_updated < old_updated
    unordered = old_updated is not None and new_updated is None
    fields = (
        "name",
        "alternative_name",
        "internal_name",
        "raw_number",
        "normalized_number",
        "raw_email",
        "normalized_email",
        "is_group",
        "account_id",
        "service_id",
        "provider_created_at",
        "provider_updated_at",
        "provider_deleted_at",
    )
    changed = False
    for field in fields:
        incoming = getattr(contact, field)
        if incoming is None or older or (unordered and row[field] is not None):
            value = row[field]
        else:
            value = incoming
        if value != row[field]:
            changed = True
    if older:
        return "older"
    if unordered:
        return "unordered" if changed else "unchanged"
    return "changed" if changed else "unchanged"


def build_reconciliation_plan(
    connection: psycopg.Connection[Any],
    *,
    acessorias_snapshot: AcessoriasSnapshot,
    digisac_snapshot: DigisacContactBackfillSnapshot,
) -> ReconciliationPlan:
    """Build a deterministic plan from PostgreSQL and two validated snapshots."""
    validate_acessorias_snapshot(acessorias_snapshot)
    require_complete_acessorias_snapshot(acessorias_snapshot)
    rows = _load_directory_rows(connection)
    companies = {item.external_id: item for item in acessorias_snapshot.companies}
    departments = {
        item.external_id: item for item in acessorias_snapshot.departments
    }
    contacts = {
        (company.external_id, contact.external_key): contact
        for company in acessorias_snapshot.companies
        for contact in company.contacts
    }
    relationships = {
        (company.external_id, department_id)
        for company in acessorias_snapshot.companies
        for department_id in company.department_ids
    }
    resource_counts: dict[str, dict[str, int]] = {
        name: _counts("new", "changed", "unchanged")
        for name in (
            "companies",
            "departments",
            "contacts",
            "relationships",
            "digisac_contacts",
        )
    }
    for external_id, company in sorted(companies.items()):
        row = rows["companies"].get(external_id)
        if row is None:
            resource_counts["companies"]["new"] += 1
        elif (
            _access_company_fields(company)
            != (
                row["provider_id"],
                row["legal_name"],
                row["trade_name"],
                row["provider_status"],
                row["phone"],
                row["uf"],
                row["client_since"],
                row["client_until"],
                row["registered_at"],
                row["is_active"],
            )
            or not row["is_present"]
        ):
            resource_counts["companies"]["changed"] += 1
        else:
            resource_counts["companies"]["unchanged"] += 1
    for external_id, department in sorted(departments.items()):
        row = rows["departments"].get(external_id)
        if row is None:
            resource_counts["departments"]["new"] += 1
        elif (
            _access_department_fields(department)
            != (row["name"], row["responsible_name"], row["responsible_email"])
            or not row["is_present"]
            or not row["is_active"]
        ):
            resource_counts["departments"]["changed"] += 1
        else:
            resource_counts["departments"]["unchanged"] += 1
    for key, contact in sorted(contacts.items()):
        row = rows["contacts"].get(key)
        if row is None:
            resource_counts["contacts"]["new"] += 1
        elif (
            _access_contact_fields(contact)
            != (
                row["name"],
                row["raw_mobile"],
                row["normalized_mobile"],
                row["raw_email"],
                row["normalized_email"],
            )
            or not row["is_present"]
            or not row["is_active"]
        ):
            resource_counts["contacts"]["changed"] += 1
        else:
            resource_counts["contacts"]["unchanged"] += 1
    for key in sorted(relationships):
        row = rows["relationships"].get(key)
        if row is None:
            resource_counts["relationships"]["new"] += 1
        elif not row["is_present"] or not row["is_active"]:
            resource_counts["relationships"]["changed"] += 1
        else:
            resource_counts["relationships"]["unchanged"] += 1

    with connection.cursor(row_factory=dict_row) as cursor:
        digisac_rows = {
            str(row["external_id"]): row
            for row in cursor.execute("SELECT * FROM digisac_contacts").fetchall()
        }
    for contact in sorted(digisac_snapshot.contacts, key=lambda item: item.external_id):
        kind = _contact_change_kind(digisac_rows.get(contact.external_id), contact)
        resource_counts["digisac_contacts"][kind] = (
            resource_counts["digisac_contacts"].get(kind, 0) + 1
        )

    incoming_company_ids = set(companies)
    incoming_department_ids = set(departments)
    retained = sum(
        1 for external_id in rows["companies"] if external_id not in incoming_company_ids
    )
    retained += sum(
        1
        for external_id in rows["departments"]
        if external_id not in incoming_department_ids
    )
    retained += sum(1 for key in rows["contacts"] if key not in contacts)
    retained += sum(1 for key in rows["relationships"] if key not in relationships)
    return ReconciliationPlan(
        acessorias_snapshot_hash=acessorias_snapshot.snapshot_hash,
        digisac_snapshot_hash=_hash_digisac_snapshot(digisac_snapshot),
        resources=resource_counts,
        historical_retained_count=retained,
        confirmed_link_count=rows["confirmed_link_count"],
    )


def _lock_all_sources(connection: psycopg.Connection[Any]) -> None:
    for lock_name in (RECONCILIATION_LOCK, ACESSORIAS_LOCK, DIGISAC_CONTACTS_LOCK):
        row = connection.execute(
            "SELECT pg_try_advisory_xact_lock(hashtext(%s))", (lock_name,)
        ).fetchone()
        if not row or not row[0]:
            raise ReconciliationInProgress()


def _upsert_department(
    connection: psycopg.Connection[Any],
    department: AcessoriasDepartment,
    now: datetime,
) -> int:
    row = connection.execute(
        """
        INSERT INTO acessorias_departments (
            external_id, name, responsible_name, responsible_email,
            is_present, is_active, synced_at, updated_at
        ) VALUES (%s, %s, %s, %s, TRUE, TRUE, %s, %s)
        ON CONFLICT (external_id) DO UPDATE SET
            name = EXCLUDED.name,
            responsible_name = EXCLUDED.responsible_name,
            responsible_email = EXCLUDED.responsible_email,
            is_present = TRUE,
            is_active = TRUE,
            synced_at = EXCLUDED.synced_at,
            updated_at = EXCLUDED.updated_at
        RETURNING id
        """,
        (
            department.external_id,
            department.name,
            department.responsible_name,
            department.responsible_email,
            now,
            now,
        ),
    ).fetchone()
    if row is None:
        row = connection.execute(
            "SELECT id FROM acessorias_departments WHERE external_id = %s",
            (department.external_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("department upsert returned no row")
    return int(row[0])


def _upsert_company(
    connection: psycopg.Connection[Any],
    company: AcessoriasCompany,
    now: datetime,
) -> int:
    row = connection.execute(
        """
        INSERT INTO acessorias_companies (
            external_id, provider_id, legal_name, trade_name, provider_status,
            phone, uf, client_since, client_until, registered_at,
            is_present, is_active, synced_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s)
        ON CONFLICT (external_id) DO UPDATE SET
            provider_id = EXCLUDED.provider_id,
            legal_name = EXCLUDED.legal_name,
            trade_name = EXCLUDED.trade_name,
            provider_status = EXCLUDED.provider_status,
            phone = EXCLUDED.phone,
            uf = EXCLUDED.uf,
            client_since = EXCLUDED.client_since,
            client_until = EXCLUDED.client_until,
            registered_at = EXCLUDED.registered_at,
            is_present = TRUE,
            is_active = EXCLUDED.is_active,
            synced_at = EXCLUDED.synced_at,
            updated_at = EXCLUDED.updated_at
        RETURNING id
        """,
        (
            company.external_id,
            company.provider_id,
            company.legal_name,
            company.trade_name,
            company.provider_status,
            company.phone,
            company.uf,
            company.client_since,
            company.client_until,
            company.registered_at,
            company.is_active,
            now,
            now,
        ),
    ).fetchone()
    if row is None:
        row = connection.execute(
            "SELECT id FROM acessorias_companies WHERE external_id = %s",
            (company.external_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("company upsert returned no row")
    return int(row[0])


def _apply_directory_delta(
    connection: psycopg.Connection[Any],
    snapshot: AcessoriasSnapshot,
    now: datetime,
) -> None:
    department_ids = {
        item.external_id: _upsert_department(connection, item, now)
        for item in sorted(snapshot.departments, key=lambda value: value.external_id)
    }
    company_ids = {
        item.external_id: _upsert_company(connection, item, now)
        for item in sorted(snapshot.companies, key=lambda value: value.external_id)
    }
    incoming_contacts: set[tuple[int, str]] = set()
    incoming_relationships: set[tuple[int, int]] = set()
    for company in sorted(snapshot.companies, key=lambda value: value.external_id):
        company_id = company_ids[company.external_id]
        for contact in sorted(company.contacts, key=lambda value: value.external_key):
            incoming_contacts.add((company_id, contact.external_key))
            connection.execute(
                """
                INSERT INTO acessorias_company_contacts (
                    company_id, external_key, name, raw_mobile, normalized_mobile,
                    raw_email, normalized_email, is_present, is_active,
                    synced_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, TRUE, %s, %s)
                ON CONFLICT (company_id, external_key) DO UPDATE SET
                    name = EXCLUDED.name,
                    raw_mobile = EXCLUDED.raw_mobile,
                    normalized_mobile = EXCLUDED.normalized_mobile,
                    raw_email = EXCLUDED.raw_email,
                    normalized_email = EXCLUDED.normalized_email,
                    is_present = TRUE,
                    is_active = TRUE,
                    synced_at = EXCLUDED.synced_at,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    company_id,
                    contact.external_key,
                    contact.name,
                    contact.raw_mobile,
                    contact.normalized_mobile,
                    contact.raw_email,
                    contact.normalized_email,
                    now,
                    now,
                ),
            )
        for department_external_id in sorted(company.department_ids):
            department_id = department_ids.get(department_external_id)
            if department_id is None:
                raise ManualReconciliationError(
                    "invalid_parent", "company department has no parent"
                )
            incoming_relationships.add((company_id, department_id))
            connection.execute(
                """
                INSERT INTO acessorias_company_departments (
                    company_id, department_id, is_present, is_active,
                    synced_at, updated_at
                ) VALUES (%s, %s, TRUE, TRUE, %s, %s)
                ON CONFLICT (company_id, department_id) DO UPDATE SET
                    is_present = TRUE,
                    is_active = TRUE,
                    synced_at = EXCLUDED.synced_at,
                    updated_at = EXCLUDED.updated_at
                """,
                (company_id, department_id, now, now),
            )

    # The provider view is complete.  Absence becomes historical retention,
    # never physical deletion, and only after all incoming facts were valid.
    with connection.cursor(row_factory=dict_row) as cursor:
        existing_companies = cursor.execute(
            "SELECT id, external_id, is_present, is_active FROM acessorias_companies"
        ).fetchall()
        existing_departments = cursor.execute(
            "SELECT id, external_id, is_present, is_active FROM acessorias_departments"
        ).fetchall()
        existing_contacts = cursor.execute(
            "SELECT id, company_id, external_key, is_present, is_active FROM acessorias_company_contacts"
        ).fetchall()
        existing_relationships = cursor.execute(
            "SELECT id, company_id, department_id, is_present, is_active FROM acessorias_company_departments"
        ).fetchall()
    current_company_ids = set(company_ids.values())
    current_department_ids = set(department_ids.values())
    for row in existing_companies:
        if int(row["id"]) not in current_company_ids and (
            row["is_present"] or row["is_active"]
        ):
            connection.execute(
                "UPDATE acessorias_companies SET is_present = FALSE, is_active = FALSE, updated_at = %s WHERE id = %s",
                (now, row["id"]),
            )
    for row in existing_departments:
        if int(row["id"]) not in current_department_ids and (
            row["is_present"] or row["is_active"]
        ):
            connection.execute(
                "UPDATE acessorias_departments SET is_present = FALSE, is_active = FALSE, updated_at = %s WHERE id = %s",
                (now, row["id"]),
            )
    for row in existing_contacts:
        key = (int(row["company_id"]), str(row["external_key"]))
        if key not in incoming_contacts and (row["is_present"] or row["is_active"]):
            connection.execute(
                "UPDATE acessorias_company_contacts SET is_present = FALSE, is_active = FALSE, updated_at = %s WHERE id = %s",
                (now, row["id"]),
            )
    for row in existing_relationships:
        key = (int(row["company_id"]), int(row["department_id"]))
        if key not in incoming_relationships and (
            row["is_present"] or row["is_active"]
        ):
            connection.execute(
                "UPDATE acessorias_company_departments SET is_present = FALSE, is_active = FALSE, updated_at = %s WHERE id = %s",
                (now, row["id"]),
            )


def _apply_digisac_delta(
    connection: psycopg.Connection[Any],
    snapshot: DigisacContactBackfillSnapshot,
    now: datetime,
) -> None:
    with connection.cursor(row_factory=dict_row) as cursor:
        for contact in sorted(snapshot.contacts, key=lambda item: item.external_id):
            upsert_digisac_contact_cursor(
                cursor, contact, MANUAL_CONTACT_SOURCE, now
            )


def _execution_report(
    *,
    execution_id: UUID,
    mode: str,
    status: str,
    plan: ReconciliationPlan | None,
    acessorias_snapshot: AcessoriasSnapshot | None = None,
    digisac_snapshot: DigisacContactBackfillSnapshot | None = None,
    identity: Mapping[str, int] | None = None,
    failure_category: str | None = None,
) -> dict[str, Any]:
    acquisition = {
        "acessorias": {
            "page_count": acessorias_snapshot.page_count if acessorias_snapshot else 0,
            "request_attempt_count": acessorias_snapshot.request_attempt_count if acessorias_snapshot else 0,
            "company_count": len(acessorias_snapshot.companies) if acessorias_snapshot else 0,
            "contact_count": acessorias_snapshot.contact_count if acessorias_snapshot else 0,
            "department_count": len(acessorias_snapshot.departments) if acessorias_snapshot else 0,
            "relationship_count": acessorias_snapshot.relationship_count if acessorias_snapshot else 0,
        },
        "digisac": {
            "page_count": digisac_snapshot.page_count if digisac_snapshot else 0,
            "request_attempt_count": digisac_snapshot.page_count if digisac_snapshot else 0,
            "contact_count": len(digisac_snapshot.contacts) if digisac_snapshot else 0,
            "duplicate_count": digisac_snapshot.duplicate_count if digisac_snapshot else 0,
        },
    }
    report: dict[str, Any] = {
        "execution_id": str(execution_id),
        "mode": mode,
        "status": status,
        "acquisition": acquisition,
        "delta": plan.as_dict() if plan else {},
        "identity": dict(identity or {}),
    }
    if plan is not None:
        report["acessorias_snapshot_hash"] = plan.acessorias_snapshot_hash
        report["digisac_snapshot_hash"] = plan.digisac_snapshot_hash
    if failure_category is not None:
        report["failure_category"] = failure_category
    return report


def _flat_counts(report: Mapping[str, Any]) -> dict[str, int]:
    def mapping(value: Any) -> Mapping[str, Any]:
        return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else {}

    delta = mapping(report.get("delta"))
    identity_map = mapping(report.get("identity"))
    resources = mapping(delta.get("resources"))

    def total(key: str) -> int:
        return sum(
            int(mapping(values).get(key, 0)) for values in resources.values()
        )

    return {
        "new_count": total("new"),
        "changed_count": total("changed"),
        "unchanged_count": total("unchanged"),
        "historical_retained_count": int(delta.get("historical_retained_count", 0)),
        "discovered_count": int(identity_map.get("processed_count", 0)),
        "candidate_count": int(identity_map.get("candidate_count", 0)),
        "ambiguous_count": int(identity_map.get("ambiguous_count", 0)),
        "unresolved_count": int(identity_map.get("unresolved_count", 0)),
        "confirmed_preserved_count": int(identity_map.get("confirmed_preserved_count", 0)),
        "matching_retry_count": int(identity_map.get("failed_count", 0)),
    }


def _write_execution(
    connection: psycopg.Connection[Any],
    *,
    execution_id: UUID,
    status: str,
    report: Mapping[str, Any],
    failure_category: str | None = None,
    failure_message: str | None = None,
) -> None:
    def mapping(value: Any) -> Mapping[str, Any]:
        return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else {}

    acquisition = mapping(report.get("acquisition"))
    access = mapping(acquisition.get("acessorias"))
    digi = mapping(acquisition.get("digisac"))
    identity = _flat_counts(report)
    values = {
        "acessorias_snapshot_hash": report.get("acessorias_snapshot_hash"),
        "digisac_snapshot_hash": report.get("digisac_snapshot_hash"),
        "acessorias_page_count": int(access.get("page_count", 0)),
        "acessorias_request_attempt_count": int(access.get("request_attempt_count", 0)),
        "digisac_page_count": int(digi.get("page_count", 0)),
        "digisac_request_attempt_count": int(digi.get("request_attempt_count", 0)),
        "acessorias_company_count": int(access.get("company_count", 0)),
        "acessorias_contact_count": int(access.get("contact_count", 0)),
        "acessorias_department_count": int(access.get("department_count", 0)),
        "acessorias_relationship_count": int(access.get("relationship_count", 0)),
        "digisac_contact_count": int(digi.get("contact_count", 0)),
        "digisac_duplicate_count": int(digi.get("duplicate_count", 0)),
        **identity,
    }
    connection.execute(
        """
        UPDATE digisac_acessorias_reconciliation_executions
        SET status = %s,
            acessorias_snapshot_hash = %s,
            digisac_snapshot_hash = %s,
            acessorias_page_count = %s,
            acessorias_request_attempt_count = %s,
            digisac_page_count = %s,
            digisac_request_attempt_count = %s,
            acessorias_company_count = %s,
            acessorias_contact_count = %s,
            acessorias_department_count = %s,
            acessorias_relationship_count = %s,
            digisac_contact_count = %s,
            digisac_duplicate_count = %s,
            new_count = %s,
            changed_count = %s,
            unchanged_count = %s,
            historical_retained_count = %s,
            discovered_count = %s,
            candidate_count = %s,
            ambiguous_count = %s,
            unresolved_count = %s,
            confirmed_preserved_count = %s,
            matching_retry_count = %s,
            completed_at = now(),
            failure_category = %s,
            failure_message = %s,
            report_json = %s,
            updated_at = now()
        WHERE execution_id = %s
        """,
        (
            status,
            values["acessorias_snapshot_hash"],
            values["digisac_snapshot_hash"],
            values["acessorias_page_count"],
            values["acessorias_request_attempt_count"],
            values["digisac_page_count"],
            values["digisac_request_attempt_count"],
            values["acessorias_company_count"],
            values["acessorias_contact_count"],
            values["acessorias_department_count"],
            values["acessorias_relationship_count"],
            values["digisac_contact_count"],
            values["digisac_duplicate_count"],
            values["new_count"],
            values["changed_count"],
            values["unchanged_count"],
            values["historical_retained_count"],
            values["discovered_count"],
            values["candidate_count"],
            values["ambiguous_count"],
            values["unresolved_count"],
            values["confirmed_preserved_count"],
            values["matching_retry_count"],
            failure_category,
            failure_message[:240] if failure_message else None,
            Jsonb(dict(report)),
            execution_id,
        ),
    )


def _start_execution(execution_id: UUID, mode: str) -> None:
    now = _utc_now()
    with get_database_pool().connection() as connection:
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO digisac_acessorias_reconciliation_executions
                    (execution_id, mode, status, started_at, created_at, updated_at)
                VALUES (%s, %s, 'started', %s, %s, %s)
                """,
                (execution_id, mode, now, now, now),
            )


def _finish_execution(
    execution_id: UUID,
    *,
    status: str,
    report: Mapping[str, Any],
    failure_category: str | None = None,
    failure_message: str | None = None,
) -> None:
    with get_database_pool().connection() as connection:
        with connection.transaction():
            _write_execution(
                connection,
                execution_id=execution_id,
                status=status,
                report=report,
                failure_category=failure_category,
                failure_message=failure_message,
            )


async def _acquire_snapshots(
    *,
    acessorias_provider: AcessoriasReconciliationProvider | None,
    digisac_provider: DigisacReconciliationProvider | None,
    per_page: int | None,
) -> tuple[AcessoriasSnapshot, DigisacContactBackfillSnapshot]:
    selected_acessorias = acessorias_provider or AcessoriasDirectoryAdapter()
    selected_digisac = digisac_provider or DigisacClient()

    async def access() -> AcessoriasSnapshot:
        try:
            snapshot = await asyncio.to_thread(selected_acessorias.fetch_snapshot)
            validate_acessorias_snapshot(snapshot)
            require_complete_acessorias_snapshot(snapshot)
            return snapshot
        except AcessoriasDirectoryError:
            raise
        except Exception as exc:
            raise ManualReconciliationError(
                "provider", "Acessórias snapshot acquisition failed"
            ) from exc

    async def digisac() -> DigisacContactBackfillSnapshot:
        try:
            return await acquire_contact_backfill(
                client=selected_digisac,
                per_page=per_page,
            )
        except DigisacContactBackfillError:
            raise
        except Exception as exc:
            raise ManualReconciliationError(
                "provider", "DigiSac snapshot acquisition failed"
            ) from exc

    access_result, digisac_result = await asyncio.gather(access(), digisac())
    return access_result, digisac_result


async def run_manual_reconciliation(
    *,
    apply: bool = False,
    acessorias_provider: AcessoriasReconciliationProvider | None = None,
    digisac_provider: DigisacReconciliationProvider | None = None,
    per_page: int | None = None,
) -> ManualReconciliationResult:
    """Run one explicit dry-run or apply operation.

    ``apply=False`` is the safe default.  Provider acquisition is complete before
    the first business-table write, and matching is a post-publication stage.
    """
    mode = "apply" if apply else "dry_run"
    execution_id = uuid4()
    _start_execution(execution_id, mode)
    acessorias_snapshot: AcessoriasSnapshot | None = None
    digisac_snapshot: DigisacContactBackfillSnapshot | None = None
    plan: ReconciliationPlan | None = None
    try:
        acessorias_snapshot, digisac_snapshot = await _acquire_snapshots(
            acessorias_provider=acessorias_provider,
            digisac_provider=digisac_provider,
            per_page=per_page,
        )
        # Keep the publication boundary defensive even when a test or internal
        # caller supplies a custom acquisition implementation.
        validate_acessorias_snapshot(acessorias_snapshot)
        require_complete_acessorias_snapshot(acessorias_snapshot)
        if not apply:
            with get_database_pool().connection() as connection:
                with connection.transaction():
                    _lock_all_sources(connection)
                    plan = build_reconciliation_plan(
                        connection,
                        acessorias_snapshot=acessorias_snapshot,
                        digisac_snapshot=digisac_snapshot,
                    )
            report = _execution_report(
                execution_id=execution_id,
                mode=mode,
                status="dry_run",
                plan=plan,
                acessorias_snapshot=acessorias_snapshot,
                digisac_snapshot=digisac_snapshot,
            )
            _finish_execution(execution_id, status="dry_run", report=report)
            return ManualReconciliationResult(execution_id, mode, "dry_run", report)

        now = _utc_now()
        with get_database_pool().connection() as connection:
            with connection.transaction():
                _lock_all_sources(connection)
                plan = build_reconciliation_plan(
                    connection,
                    acessorias_snapshot=acessorias_snapshot,
                    digisac_snapshot=digisac_snapshot,
                )
                _apply_directory_delta(connection, acessorias_snapshot, now)
                _apply_digisac_delta(connection, digisac_snapshot, now)
                report = _execution_report(
                    execution_id=execution_id,
                    mode=mode,
                    status="succeeded",
                    plan=plan,
                    acessorias_snapshot=acessorias_snapshot,
                    digisac_snapshot=digisac_snapshot,
                )
                # Directory and Contacts are committed together. Matching is
                # intentionally outside this transaction so it is resumable.
                _write_execution(
                    connection,
                    execution_id=execution_id,
                    status="succeeded",
                    report=report,
                )

        try:
            identity = await discover_all_identities()
        except Exception:
            identity = {"processed_count": 0, "failed_count": len(digisac_snapshot.contacts)}
        status = "matching_failed" if identity.get("failed_count", 0) else "succeeded"
        report = _execution_report(
            execution_id=execution_id,
            mode=mode,
            status=status,
            plan=plan,
            acessorias_snapshot=acessorias_snapshot,
            digisac_snapshot=digisac_snapshot,
            identity=identity,
            failure_category="matching_failed" if status == "matching_failed" else None,
        )
        _finish_execution(
            execution_id,
            status=status,
            report=report,
            failure_category="matching_failed" if status == "matching_failed" else None,
            failure_message=(
                "identity discovery requires a later manual retry"
                if status == "matching_failed"
                else None
            ),
        )
        return ManualReconciliationResult(
            execution_id,
            mode,
            status,
            report,
            "matching_failed" if status == "matching_failed" else None,
        )
    except (AcessoriasDirectoryError, DigisacContactBackfillError, ManualReconciliationError) as exc:
        category = getattr(exc, "category", "provider")
        message = getattr(exc, "safe_message", "manual reconciliation failed")
        report = _execution_report(
            execution_id=execution_id,
            mode=mode,
            status="failed",
            plan=plan,
            acessorias_snapshot=acessorias_snapshot,
            digisac_snapshot=digisac_snapshot,
            failure_category=category,
        )
        _finish_execution(
            execution_id,
            status="failed",
            report=report,
            failure_category=category,
            failure_message=message,
        )
        return ManualReconciliationResult(execution_id, mode, "failed", report, category)
    except Exception:
        category = "internal_error"
        report = _execution_report(
            execution_id=execution_id,
            mode=mode,
            status="failed",
            plan=plan,
            acessorias_snapshot=acessorias_snapshot,
            digisac_snapshot=digisac_snapshot,
            failure_category=category,
        )
        _finish_execution(
            execution_id,
            status="failed",
            report=report,
            failure_category=category,
            failure_message="manual reconciliation failed",
        )
        logger.exception("Manual DigiSac/Acessórias reconciliation failed")
        return ManualReconciliationResult(execution_id, mode, "failed", report, category)


def run_manual_reconciliation_sync(**kwargs: Any) -> ManualReconciliationResult:
    """Synchronous bridge for internal callers and deterministic tests."""
    return asyncio.run(run_manual_reconciliation(**kwargs))
