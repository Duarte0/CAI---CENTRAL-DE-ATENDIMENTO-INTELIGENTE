"""Typed Acessórias directory acquisition and durable reconciliation.

The provider adapter is deliberately separate from the existing DigiSac
directory.  It turns the observed read-only provider contract into typed
records, validates a complete snapshot, and publishes that snapshot in one
PostgreSQL transaction.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from threading import Lock
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Mapping, Protocol, cast
from uuid import UUID, uuid4

import psycopg
import requests

from src.core.config import settings
from src.core.db import get_database_pool

logger = logging.getLogger(__name__)

TRANSIENT_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
AUTHENTICATION_STATUSES = frozenset({401, 403})


class AcessoriasDirectoryError(RuntimeError):
    """A sanitized directory failure safe to expose in operational state."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.safe_message = message


class AcessoriasRefreshInProgress(AcessoriasDirectoryError):
    def __init__(self) -> None:
        super().__init__("refresh_in_progress", "another refresh is publishing")


@dataclass(frozen=True)
class AcessoriasContact:
    name: str
    raw_email: str | None
    normalized_email: str | None
    raw_mobile: str | None
    normalized_mobile: str | None
    external_key: str


@dataclass(frozen=True)
class AcessoriasDepartment:
    external_id: str
    name: str
    responsible_name: str | None = None
    responsible_email: str | None = None


@dataclass(frozen=True)
class AcessoriasCompany:
    external_id: str
    provider_id: str
    legal_name: str
    trade_name: str
    provider_status: str | None
    phone: str | None
    uf: str | None
    client_since: str | None
    client_until: str | None
    registered_at: str | None
    contacts: tuple[AcessoriasContact, ...]
    department_ids: tuple[str, ...]
    is_active: bool | None


@dataclass(frozen=True)
class AcessoriasSnapshot:
    departments: tuple[AcessoriasDepartment, ...]
    companies: tuple[AcessoriasCompany, ...]
    page_count: int = 0
    request_attempt_count: int = 0

    @property
    def relationship_count(self) -> int:
        return sum(len(company.department_ids) for company in self.companies)

    @property
    def contact_count(self) -> int:
        return sum(len(company.contacts) for company in self.companies)

    @property
    def snapshot_hash(self) -> str:
        canonical = {
            "departments": [
                {
                    "external_id": item.external_id,
                    "name": item.name,
                    "responsible_name": item.responsible_name,
                    "responsible_email": item.responsible_email,
                }
                for item in sorted(self.departments, key=lambda value: value.external_id)
            ],
            "companies": [
                {
                    "external_id": item.external_id,
                    "provider_id": item.provider_id,
                    "legal_name": item.legal_name,
                    "trade_name": item.trade_name,
                    "provider_status": item.provider_status,
                    "phone": item.phone,
                    "uf": item.uf,
                    "client_since": item.client_since,
                    "client_until": item.client_until,
                    "registered_at": item.registered_at,
                    "is_active": item.is_active,
                    "contacts": [
                        {
                            "name": contact.name,
                            "raw_email": contact.raw_email,
                            "raw_mobile": contact.raw_mobile,
                            "external_key": contact.external_key,
                        }
                        for contact in sorted(
                            item.contacts, key=lambda value: value.external_key
                        )
                    ],
                    "department_ids": sorted(item.department_ids),
                }
                for item in sorted(self.companies, key=lambda value: value.external_id)
            ],
        }
        encoded = json.dumps(
            canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class SnapshotProvider(Protocol):
    def fetch_snapshot(self) -> AcessoriasSnapshot:
        """Acquire and validate one complete provider snapshot."""
        ...


@dataclass(frozen=True)
class AcessoriasSyncResult:
    execution_id: UUID
    status: str
    snapshot_hash: str | None
    counts: Mapping[str, int]
    failure_category: str | None = None


def normalize_mobile(value: str | None) -> str | None:
    """Keep only Unicode decimal digits, represented as ASCII digits."""
    if value is None or value == "":
        return None
    digits: list[str] = []
    for character in value:
        if character.isdecimal():
            digits.append(str(unicodedata.digit(character)))
    return "".join(digits) or None


def normalize_email(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    return normalized or None


def _required_identifier(value: object, field: str) -> str:
    if isinstance(value, bool) or value is None:
        raise AcessoriasDirectoryError("invalid_payload", f"missing {field}")
    text = str(value).strip()
    if not text:
        raise AcessoriasDirectoryError("invalid_payload", f"blank {field}")
    return text


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AcessoriasDirectoryError("invalid_payload", f"invalid {field}")
    return value


def _status_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    raise AcessoriasDirectoryError("invalid_payload", "invalid provider status")


def _activity_from_status(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = unicodedata.normalize("NFKD", value).casefold().strip()
        normalized = "".join(
            character for character in normalized
            if not unicodedata.combining(character)
        )
        if normalized in {"1", "ativo", "ativa", "active", "enabled", "sim", "s"}:
            return True
        if normalized in {"0", "inativo", "inativa", "inactive", "disabled", "nao", "n"}:
            return False
    return None


def _contact_key(company_id: str, name: str, email: str | None, mobile: str | None) -> str:
    material = "\x1f".join((company_id, name, email or "", mobile or ""))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class _RateLimiter:
    _shared_states: dict[str, "_RateLimiterState"] = {}
    _shared_states_lock = Lock()

    def __init__(
        self,
        limit_per_minute: int,
        *,
        sleep: Callable[[float], None],
        clock: Callable[[], float],
        shared_key: str | None = None,
    ) -> None:
        if not 1 <= limit_per_minute <= 100:
            raise ValueError("Acessórias request rate must be between 1 and 100")
        self.limit = limit_per_minute
        self.sleep = sleep
        self.clock = clock
        if shared_key is None:
            self._state = _RateLimiterState(limit_per_minute)
        else:
            with self._shared_states_lock:
                state = self._shared_states.get(shared_key)
                if state is None:
                    state = _RateLimiterState(limit_per_minute)
                    self._shared_states[shared_key] = state
                self._state = state

    def before_request(self) -> None:
        with self._state.lock:
            while True:
                now = self.clock()
                while self._state.requests and now - self._state.requests[0] >= 60:
                    self._state.requests.popleft()
                if len(self._state.requests) < self._state.limit:
                    self._state.requests.append(now)
                    return
                delay = max(0.0, 60 - (now - self._state.requests[0]))
                self.sleep(delay)


class _RateLimiterState:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.requests: deque[float] = deque()
        self.lock = Lock()


class AcessoriasDirectoryAdapter:
    """Read-only adapter for the observed Acessórias directory endpoints."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout_seconds: float | None = None,
        max_attempts: int | None = None,
        retry_base_seconds: float | None = None,
        retry_max_delay_seconds: float | None = None,
        retry_provider_margin_seconds: float | None = None,
        rate_limit_per_minute: int | None = None,
        page_safety_limit: int | None = None,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        selected_base_url = base_url or settings.acessorias_api_base_url
        if not selected_base_url.startswith(("http://", "https://")):
            raise ValueError("Acessórias base URL must use HTTP or HTTPS")
        self.base_url = selected_base_url.rstrip("/")
        self.token = token if token is not None else settings.acessorias_api_token
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.acessorias_request_timeout_seconds
        )
        self.max_attempts = (
            max_attempts if max_attempts is not None else settings.acessorias_max_attempts
        )
        self.retry_base_seconds = (
            retry_base_seconds
            if retry_base_seconds is not None
            else settings.acessorias_retry_base_seconds
        )
        self.retry_max_delay_seconds = (
            retry_max_delay_seconds
            if retry_max_delay_seconds is not None
            else settings.acessorias_retry_max_delay_seconds
        )
        self.retry_provider_margin_seconds = (
            retry_provider_margin_seconds
            if retry_provider_margin_seconds is not None
            else settings.acessorias_retry_provider_margin_seconds
        )
        self.page_safety_limit = (
            page_safety_limit
            if page_safety_limit is not None
            else settings.acessorias_page_safety_limit
        )
        if self.timeout_seconds <= 0 or self.max_attempts <= 0:
            raise ValueError("Acessórias timeout and attempts must be positive")
        if self.retry_base_seconds < 0 or self.retry_max_delay_seconds < 0:
            raise ValueError("Acessórias retry delays cannot be negative")
        if self.retry_max_delay_seconds < self.retry_base_seconds:
            raise ValueError("Acessórias retry max delay must cover the base delay")
        if self.page_safety_limit <= 0:
            raise ValueError("Acessórias page safety limit must be positive")
        self.session = session or requests.Session()
        self.sleep = sleep
        self.clock = clock
        self.rate_limiter = _RateLimiter(
            (
                rate_limit_per_minute
                if rate_limit_per_minute is not None
                else settings.acessorias_rate_limit_per_minute
            ),
            sleep=sleep,
            clock=clock,
        )
        self.request_attempt_count = 0

    def _retry_after(self, response: requests.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            try:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None

    def _retry_delay(self, attempt: int, provider_delay: float | None) -> float:
        local_delay = min(
            self.retry_base_seconds * (2 ** max(0, attempt - 1)),
            self.retry_max_delay_seconds,
        )
        if provider_delay is None:
            return local_delay
        return max(local_delay, provider_delay + self.retry_provider_margin_seconds)

    def _get_json(
        self,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
    ) -> object:
        if not self.token or not self.token.strip():
            raise AcessoriasDirectoryError(
                "missing_credentials", "ACESSORIAS_API_TOKEN is not configured"
            )
        headers = {"Authorization": f"Bearer {self.token}"}
        for attempt in range(1, max(1, self.max_attempts) + 1):
            self.rate_limiter.before_request()
            self.request_attempt_count += 1
            try:
                response = self.session.get(
                    f"{self.base_url}{path}",
                    params=params,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
            except (requests.Timeout, requests.ConnectionError):
                if attempt >= self.max_attempts:
                    raise AcessoriasDirectoryError(
                        "connection", "provider connection failed after bounded retries"
                    )
                self.sleep(self._retry_delay(attempt, None))
                continue
            except requests.RequestException:
                raise AcessoriasDirectoryError("request_error", "provider request failed")

            status_code = response.status_code
            if status_code in TRANSIENT_HTTP_STATUSES:
                if attempt >= self.max_attempts:
                    category = "rate_limit" if status_code == 429 else "transient_http"
                    raise AcessoriasDirectoryError(
                        category,
                        f"provider returned HTTP {status_code} after bounded retries",
                    )
                self.sleep(self._retry_delay(attempt, self._retry_after(response)))
                continue
            if status_code in AUTHENTICATION_STATUSES:
                raise AcessoriasDirectoryError(
                    "authentication", f"provider authentication failed with HTTP {status_code}"
                )
            if status_code >= 400:
                raise AcessoriasDirectoryError(
                    "http_error", f"provider returned HTTP {status_code}"
                )
            try:
                return response.json()
            except (ValueError, requests.RequestException):
                raise AcessoriasDirectoryError("invalid_payload", "provider returned invalid JSON")
        raise AcessoriasDirectoryError("request_error", "provider request did not complete")

    def _parse_department(self, raw: object) -> AcessoriasDepartment:
        if not isinstance(raw, Mapping):
            raise AcessoriasDirectoryError("invalid_payload", "invalid department record")
        record = cast(Mapping[str, object], raw)
        external_id = _required_identifier(record.get("ID"), "department ID")
        name = _required_identifier(record.get("Nome"), "department name")
        return AcessoriasDepartment(
            external_id=external_id,
            name=name,
            responsible_name=_optional_text(record.get("RespNome"), "department responsible name"),
            responsible_email=_optional_text(record.get("RespEmail"), "department responsible email"),
        )

    def _parse_contact(self, company_id: str, raw: object) -> AcessoriasContact:
        if not isinstance(raw, Mapping):
            raise AcessoriasDirectoryError("invalid_payload", "invalid company contact record")
        record = cast(Mapping[str, object], raw)
        for field in ("Nome", "E-mail", "Celular"):
            if field not in record:
                raise AcessoriasDirectoryError("invalid_payload", "incomplete company contact")
        name = _optional_text(record.get("Nome"), "contact name") or ""
        email = _optional_text(record.get("E-mail"), "contact email")
        mobile = _optional_text(record.get("Celular"), "contact mobile")
        return AcessoriasContact(
            name=name,
            raw_email=email,
            normalized_email=normalize_email(email),
            raw_mobile=mobile,
            normalized_mobile=normalize_mobile(mobile),
            external_key=_contact_key(company_id, name, email, mobile),
        )

    def _parse_company(self, raw: object) -> AcessoriasCompany:
        if not isinstance(raw, Mapping):
            raise AcessoriasDirectoryError("invalid_payload", "invalid company record")
        record = cast(Mapping[str, object], raw)
        for field in ("ID", "Identificador", "Status", "ContatosNaEmpresa", "Departamentos"):
            if field not in record:
                raise AcessoriasDirectoryError("invalid_payload", "incomplete company record")
        provider_id = _required_identifier(record.get("ID"), "company ID")
        external_id = _required_identifier(record.get("Identificador"), "company identifier")
        raw_contacts = record.get("ContatosNaEmpresa")
        raw_departments = record.get("Departamentos")
        if not isinstance(raw_contacts, list) or not isinstance(raw_departments, list):
            raise AcessoriasDirectoryError("invalid_payload", "invalid company child lists")
        department_ids: list[str] = []
        for item in cast(list[object], raw_departments):
            if not isinstance(item, Mapping):
                raise AcessoriasDirectoryError("invalid_payload", "invalid company department")
            department_ids.append(
                _required_identifier(
                    cast(Mapping[str, object], item).get("ID"), "company department ID"
                )
            )
        if len(set(department_ids)) != len(department_ids):
            raise AcessoriasDirectoryError("invalid_payload", "duplicate company department")
        contacts = tuple(
            self._parse_contact(external_id, item) for item in cast(list[object], raw_contacts)
        )
        status = record.get("Status")
        return AcessoriasCompany(
            external_id=external_id,
            provider_id=provider_id,
            legal_name=_optional_text(record.get("Razao"), "company legal name") or "",
            trade_name=_optional_text(record.get("Fantasia"), "company trade name") or "",
            provider_status=_status_text(status),
            phone=_optional_text(record.get("Telefone"), "company phone"),
            uf=_optional_text(record.get("UF"), "company state"),
            client_since=_optional_text(record.get("ClienteDesde"), "company client since"),
            client_until=_optional_text(record.get("ClienteAte"), "company client until"),
            registered_at=_optional_text(record.get("DataDoCadastro"), "company registration date"),
            contacts=contacts,
            department_ids=tuple(department_ids),
            is_active=_activity_from_status(status),
        )

    def fetch_snapshot(self) -> AcessoriasSnapshot:
        self.request_attempt_count = 0
        raw_departments = self._get_json("/departments/ListAll")
        if not isinstance(raw_departments, list):
            raise AcessoriasDirectoryError("invalid_payload", "departments response must be a list")
        departments = tuple(
            self._parse_department(item) for item in cast(list[object], raw_departments)
        )
        department_ids = {department.external_id for department in departments}
        if len(department_ids) != len(departments):
            raise AcessoriasDirectoryError("invalid_payload", "duplicate department ID")

        companies: list[AcessoriasCompany] = []
        seen_companies: set[str] = set()
        seen_pages: set[str] = set()
        page = 1
        page_count = 0
        while True:
            if page > self.page_safety_limit:
                raise AcessoriasDirectoryError("pagination_limit", "company page safety limit reached")
            raw_page = self._get_json(
                "/companies/ListAll",
                params={"contacts": "", "departments": "", "ativa": "S", "Pagina": page},
            )
            if not isinstance(raw_page, list):
                raise AcessoriasDirectoryError("invalid_payload", "company page must be a list")
            if not raw_page:
                break
            page_fingerprint = json.dumps(
                raw_page, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
            )
            if page_fingerprint in seen_pages:
                raise AcessoriasDirectoryError("pagination_loop", "repeated company page")
            seen_pages.add(page_fingerprint)
            page_count += 1
            for item in cast(list[object], raw_page):
                company = self._parse_company(item)
                if company.external_id in seen_companies:
                    raise AcessoriasDirectoryError("pagination_loop", "repeated company record")
                seen_companies.add(company.external_id)
                companies.append(company)
            if page_count >= self.page_safety_limit:
                raise AcessoriasDirectoryError("pagination_limit", "company page safety limit reached")
            page += 1

        for company in companies:
            if any(department_id not in department_ids for department_id in company.department_ids):
                raise AcessoriasDirectoryError("invalid_parent", "company department has no parent")
        snapshot = AcessoriasSnapshot(
            departments=departments,
            companies=tuple(companies),
            page_count=page_count,
            request_attempt_count=self.request_attempt_count,
        )
        _validate_snapshot(snapshot)
        return snapshot


def _validate_snapshot(snapshot: AcessoriasSnapshot) -> None:
    department_ids = {item.external_id for item in snapshot.departments}
    if len(department_ids) != len(snapshot.departments):
        raise AcessoriasDirectoryError("invalid_payload", "duplicate department identity")
    company_ids = {item.external_id for item in snapshot.companies}
    provider_ids = {item.provider_id for item in snapshot.companies}
    if len(company_ids) != len(snapshot.companies) or len(provider_ids) != len(snapshot.companies):
        raise AcessoriasDirectoryError("invalid_payload", "duplicate company identity")
    for company in snapshot.companies:
        if any(department_id not in department_ids for department_id in company.department_ids):
            raise AcessoriasDirectoryError("invalid_parent", "company department has no parent")
        contact_keys = {contact.external_key for contact in company.contacts}
        if len(contact_keys) != len(company.contacts):
            raise AcessoriasDirectoryError("invalid_payload", "duplicate company contact")


def _execution_counts(snapshot: AcessoriasSnapshot, *, inactivated: int = 0) -> dict[str, int]:
    return {
        "page_count": snapshot.page_count,
        "request_attempt_count": snapshot.request_attempt_count,
        "company_count": len(snapshot.companies),
        "contact_count": snapshot.contact_count,
        "department_count": len(snapshot.departments),
        "relationship_count": snapshot.relationship_count,
        "inactivated_count": inactivated,
    }


def _start_execution(execution_id: UUID) -> None:
    now = datetime.now(timezone.utc)
    with get_database_pool().connection() as connection:
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO acessorias_directory_sync_executions
                    (execution_id, status, started_at, created_at, updated_at)
                VALUES (%s, 'started', %s, %s, %s)
                """,
                (execution_id, now, now, now),
            )


def _fail_execution(
    execution_id: UUID,
    *,
    category: str,
    message: str,
    snapshot: AcessoriasSnapshot | None = None,
) -> AcessoriasSyncResult:
    safe_category = category[:80]
    safe_message = message[:240]
    counts = _execution_counts(snapshot) if snapshot is not None else {
        "page_count": 0,
        "request_attempt_count": 0,
        "company_count": 0,
        "contact_count": 0,
        "department_count": 0,
        "relationship_count": 0,
        "inactivated_count": 0,
    }
    now = datetime.now(timezone.utc)
    with get_database_pool().connection() as connection:
        with connection.transaction():
            connection.execute(
                """
                UPDATE acessorias_directory_sync_executions
                SET status = 'failed', completed_at = %s,
                    page_count = %s, request_attempt_count = %s,
                    company_count = %s, contact_count = %s,
                    department_count = %s, relationship_count = %s,
                    inactivated_count = %s, failure_category = %s,
                    failure_message = %s, updated_at = %s
                WHERE execution_id = %s
                """,
                (
                    now,
                    counts["page_count"],
                    counts["request_attempt_count"],
                    counts["company_count"],
                    counts["contact_count"],
                    counts["department_count"],
                    counts["relationship_count"],
                    counts["inactivated_count"],
                    safe_category,
                    safe_message,
                    now,
                    execution_id,
                ),
            )
    logger.warning("Acessórias directory refresh failed: execution=%s category=%s", execution_id, safe_category)
    return AcessoriasSyncResult(execution_id, "failed", None, counts, safe_category)


def _publish_snapshot(execution_id: UUID, snapshot: AcessoriasSnapshot) -> AcessoriasSyncResult:
    _validate_snapshot(snapshot)
    now = datetime.now(timezone.utc)
    counts = _execution_counts(snapshot)
    inactivated = 0
    with get_database_pool().connection() as connection:
        with connection.transaction():
            lock = connection.execute(
                "SELECT pg_try_advisory_xact_lock(hashtext('cai:acessorias-directory'))"
            ).fetchone()
            if not lock or not lock[0]:
                raise AcessoriasRefreshInProgress()

            for table in (
                "acessorias_company_contacts",
                "acessorias_company_departments",
                "acessorias_companies",
                "acessorias_departments",
            ):
                cursor = connection.execute(
                    f"""
                    UPDATE {table}
                    SET is_present = FALSE, is_active = FALSE, updated_at = %s
                    WHERE is_present IS TRUE OR is_active IS TRUE
                    RETURNING id
                    """,
                    (now,),
                )
                inactivated += len(cursor.fetchall())

            department_db_ids: dict[str, int] = {}
            for department in snapshot.departments:
                row = connection.execute(
                    """
                    INSERT INTO acessorias_departments
                        (external_id, name, responsible_name, responsible_email,
                         is_present, is_active, synced_at, updated_at)
                    VALUES (%s, %s, %s, %s, TRUE, TRUE, %s, %s)
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
                    raise psycopg.DataError("department upsert returned no row")
                department_db_ids[department.external_id] = int(row[0])

            company_db_ids: dict[str, int] = {}
            for company in snapshot.companies:
                row = connection.execute(
                    """
                    INSERT INTO acessorias_companies
                        (external_id, provider_id, legal_name, trade_name, provider_status,
                         phone, uf, client_since, client_until, registered_at, is_present,
                         is_active, synced_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s)
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
                    raise psycopg.DataError("company upsert returned no row")
                company_db_ids[company.external_id] = int(row[0])

            for company in snapshot.companies:
                company_id = company_db_ids[company.external_id]
                for contact in company.contacts:
                    connection.execute(
                        """
                        INSERT INTO acessorias_company_contacts
                            (company_id, external_key, name, raw_mobile, normalized_mobile,
                             raw_email, normalized_email, is_present, is_active, synced_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, TRUE, %s, %s)
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
                for department_external_id in company.department_ids:
                    department_id = department_db_ids.get(department_external_id)
                    if department_id is None:
                        raise AcessoriasDirectoryError("invalid_parent", "company department has no parent")
                    connection.execute(
                        """
                        INSERT INTO acessorias_company_departments
                            (company_id, department_id, is_present, is_active, synced_at, updated_at)
                        VALUES (%s, %s, TRUE, TRUE, %s, %s)
                        ON CONFLICT (company_id, department_id) DO UPDATE SET
                            is_present = TRUE,
                            is_active = TRUE,
                            synced_at = EXCLUDED.synced_at,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (company_id, department_id, now, now),
                    )

            counts = _execution_counts(snapshot, inactivated=inactivated)
            existing = connection.execute(
                """
                SELECT execution_id
                FROM acessorias_directory_sync_executions
                WHERE snapshot_hash = %s AND status = 'succeeded'
                LIMIT 1
                """,
                (snapshot.snapshot_hash,),
            ).fetchone()
            if existing is None:
                status = "succeeded"
                failure_category = None
            else:
                status = "deduplicated"
                failure_category = "duplicate_snapshot"
            connection.execute(
                """
                UPDATE acessorias_directory_sync_executions
                SET status = %s, snapshot_hash = %s, completed_at = %s,
                    page_count = %s, request_attempt_count = %s,
                    company_count = %s, contact_count = %s,
                    department_count = %s, relationship_count = %s,
                    inactivated_count = %s, failure_category = %s,
                    failure_message = NULL, updated_at = %s
                WHERE execution_id = %s
                """,
                (
                    status,
                    snapshot.snapshot_hash,
                    now,
                    counts["page_count"],
                    counts["request_attempt_count"],
                    counts["company_count"],
                    counts["contact_count"],
                    counts["department_count"],
                    counts["relationship_count"],
                    counts["inactivated_count"],
                    failure_category,
                    now,
                    execution_id,
                ),
            )
    logger.info(
        "Acessórias directory refresh published: execution=%s status=%s companies=%s contacts=%s departments=%s relationships=%s",
        execution_id,
        status,
        counts["company_count"],
        counts["contact_count"],
        counts["department_count"],
        counts["relationship_count"],
    )
    return AcessoriasSyncResult(execution_id, status, snapshot.snapshot_hash, counts, failure_category)


def sync_acessorias_directory_sync(
    *,
    adapter: SnapshotProvider | None = None,
) -> AcessoriasSyncResult:
    """Acquire and publish one complete snapshot through the canonical path."""
    execution_id = uuid4()
    _start_execution(execution_id)
    selected_adapter = adapter or AcessoriasDirectoryAdapter()
    snapshot: AcessoriasSnapshot | None = None
    try:
        snapshot = selected_adapter.fetch_snapshot()
        _validate_snapshot(snapshot)
        return _publish_snapshot(execution_id, snapshot)
    except AcessoriasDirectoryError as exc:
        return _fail_execution(
            execution_id,
            category=exc.category,
            message=exc.safe_message,
            snapshot=snapshot,
        )
    except Exception:
        return _fail_execution(
            execution_id,
            category="internal_error",
            message="directory refresh failed before publication completed",
            snapshot=snapshot,
        )


async def sync_acessorias_directory(
    *,
    adapter: SnapshotProvider | None = None,
) -> AcessoriasSyncResult:
    return await asyncio.to_thread(sync_acessorias_directory_sync, adapter=adapter)


def main() -> int:
    result = sync_acessorias_directory_sync()
    print(f"Acessórias directory refresh {result.status}: execution={result.execution_id}")
    return 0 if result.status in {"succeeded", "deduplicated"} else 1


if __name__ == "__main__":
    raise SystemExit(main())


AcessoriasClient = AcessoriasDirectoryAdapter
