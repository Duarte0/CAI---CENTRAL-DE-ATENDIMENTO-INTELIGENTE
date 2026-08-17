"""Durable Acessórias Request creation and conservative reconciliation.

The provider adapter owns HTTP, multipart encoding, authentication, throttling,
and response classification.  The orchestration layer owns the PostgreSQL
operation, claim lease, idempotency, and the boundary between a persisted CAI
classification and an external side effect.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Mapping, Protocol, cast
from urllib.parse import urlsplit

import psycopg
import requests
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from src.core.acessorias_directory import _RateLimiter
from src.core.config import settings
from src.core.db import get_database_pool


REQUEST_FIELDS = (
    "assunto",
    "empresa",
    "departamento",
    "prioridade",
    "descricao",
    "tipo",
)
DEFAULT_PRIORITY = 2
REQUEST_TYPE = "E"
SAFE_REASON = re.compile(r"^[a-z0-9_:-]{1,120}$")
SAFE_OPERATION_KEY = re.compile(r"^[a-zA-Z0-9_.:@-]{1,240}$")
SAFE_SOL_ID = re.compile(r"^[^\s\x00-\x1f\x7f]{1,160}$")
SAFE_DEPARTMENT_ID = re.compile(r"^[0-9]+$")


def _request_rate_limit_key(base_url: str, limit_per_minute: int) -> str:
    parsed = urlsplit(base_url)
    safe_netloc = parsed.netloc.rsplit("@", 1)[-1]
    endpoint = f"{parsed.scheme}://{safe_netloc}{parsed.path.rstrip('/')}"
    return f"request:{endpoint}:{limit_per_minute}"


class AcessoriasRequestError(RuntimeError):
    """A sanitized local or provider-boundary error."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.safe_message = message


class AcessoriasRequestPreSendError(requests.ConnectionError):
    """Transport failure explicitly proven before the POST could be sent.

    The stock ``requests`` exceptions do not identify whether a connection
    failed before or after request transmission.  Only a transport boundary
    that can establish the pre-send fact may raise this marker; ordinary
    connection errors remain ambiguous and require reconciliation.
    """


class AcessoriasRequestConflictError(AcessoriasRequestError):
    def __init__(self, message: str = "request reconciliation conflicts") -> None:
        super().__init__("reconciliation_conflict", message)


@dataclass(frozen=True)
class AcessoriasRequestPayload:
    subject: str
    company: str
    department: str
    description: str

    @property
    def form(self) -> dict[str, str]:
        return {
            "assunto": self.subject,
            "empresa": self.company,
            "departamento": self.department,
            "prioridade": str(DEFAULT_PRIORITY),
            "descricao": self.description,
            "tipo": REQUEST_TYPE,
        }

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.form, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "fields": list(REQUEST_FIELDS),
            "subject_length": len(self.subject),
            "description_length": len(self.description),
            "company_external_id": self.company,
            "department_external_id": self.department,
            "priority": DEFAULT_PRIORITY,
            "type": REQUEST_TYPE,
        }


def build_request_payload(
    *,
    title: str,
    description: str,
    company_external_id: str,
    department_external_id: str,
) -> AcessoriasRequestPayload:
    """Build and locally validate the only six fields allowed by SPEC-0011."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("classification title is required")
    if not isinstance(description, str):
        raise ValueError("classification description is required")
    subject = title.strip()[:100]
    if not subject:
        raise ValueError("classification title is required")
    company = company_external_id.strip()
    department = department_external_id.strip()
    if not company or any(character.isspace() for character in company):
        raise ValueError("company identifier is invalid")
    if not SAFE_DEPARTMENT_ID.fullmatch(department):
        raise ValueError("department identifier must be numeric")
    return AcessoriasRequestPayload(
        subject=subject,
        company=company,
        department=department,
        description=description,
    )


@dataclass(frozen=True)
class AcessoriasRequestOutcome:
    state: str
    category: str
    solid_id: str | None = None
    provider_status: int | None = None

    @classmethod
    def success(cls, solid_id: str, *, provider_status: int | None = None) -> "AcessoriasRequestOutcome":
        value = solid_id.strip()
        if not SAFE_SOL_ID.fullmatch(value):
            raise ValueError("provider id is invalid")
        return cls("completed", "provider_success", value, provider_status)

    @classmethod
    def definitive(cls, category: str, *, provider_status: int | None = None) -> "AcessoriasRequestOutcome":
        return cls("definitive_failure", _safe_category(category), None, provider_status)

    @classmethod
    def retryable(cls, category: str, *, provider_status: int | None = None) -> "AcessoriasRequestOutcome":
        return cls("retryable_failure", _safe_category(category), None, provider_status)

    @classmethod
    def reconciliation(cls, category: str, *, provider_status: int | None = None) -> "AcessoriasRequestOutcome":
        return cls("reconciliation_required", _safe_category(category), None, provider_status)


class AcessoriasRequestProvider(Protocol):
    def create_request(self, payload: AcessoriasRequestPayload) -> AcessoriasRequestOutcome:
        """Create one external Request and classify only safe outcome metadata."""


def _safe_category(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_:-]+", "_", value.casefold()).strip("_")
    return normalized[:120] or "provider_error"


def _safe_reason(value: str) -> str:
    normalized = value.strip().casefold()
    if not SAFE_REASON.fullmatch(normalized):
        raise ValueError("reason must contain only safe operational characters")
    return normalized


def _safe_operation_key(value: str) -> str:
    normalized = value.strip()
    if not SAFE_OPERATION_KEY.fullmatch(normalized):
        raise ValueError("operation_key is invalid")
    return normalized


def _safe_actor(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if len(normalized) > 160 or not SAFE_OPERATION_KEY.fullmatch(normalized):
        raise ValueError("actor is invalid")
    return normalized


def _claim_owner(value: str | None, *, prefix: str) -> str:
    candidate = value or f"{prefix}:{os.getpid()}:{id(asyncio.current_task())}"
    normalized = re.sub(r"[^a-zA-Z0-9_.:@-]", "-", candidate)[:160]
    if not normalized:
        raise ValueError("claim owner is invalid")
    return normalized


class AcessoriasRequestAdapter:
    """Authenticated multipart write adapter for ``POST /requests``."""

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
        if self.timeout_seconds <= 0 or self.max_attempts <= 0:
            raise ValueError("Acessórias Request timeout and attempts must be positive")
        if self.retry_base_seconds < 0 or self.retry_max_delay_seconds < self.retry_base_seconds:
            raise ValueError("Acessórias Request retry limits are invalid")
        self.session = session or requests.Session()
        self.sleep = sleep
        self.clock = clock
        selected_rate_limit = (
            rate_limit_per_minute
            if rate_limit_per_minute is not None
            else settings.acessorias_rate_limit_per_minute
        )
        self.rate_limiter = _RateLimiter(
            selected_rate_limit,
            sleep=sleep,
            clock=clock,
            shared_key=_request_rate_limit_key(self.base_url, selected_rate_limit),
        )

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

    @staticmethod
    def _response_json(response: requests.Response) -> Mapping[str, Any] | None:
        try:
            body = response.json()
        except (ValueError, requests.RequestException):
            return None
        return cast(Mapping[str, Any], body) if isinstance(body, Mapping) else None

    def create_request(self, payload: AcessoriasRequestPayload) -> AcessoriasRequestOutcome:
        if not self.token or not self.token.strip():
            return AcessoriasRequestOutcome.definitive("missing_credentials")
        form = payload.form
        headers = {"Authorization": f"Bearer {self.token}"}
        for attempt in range(1, self.max_attempts + 1):
            self.rate_limiter.before_request()
            try:
                response = self.session.post(
                    f"{self.base_url}/requests",
                    files={field: (None, value) for field, value in form.items()},
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
            except AcessoriasRequestPreSendError:
                if attempt < self.max_attempts:
                    self.sleep(self._retry_delay(attempt, None))
                    continue
                return AcessoriasRequestOutcome.retryable("pre_send_connection")
            except requests.Timeout:
                return AcessoriasRequestOutcome.reconciliation("timeout_after_send")
            except requests.ConnectionError:
                return AcessoriasRequestOutcome.reconciliation("uncertain_transport")
            except requests.RequestException:
                return AcessoriasRequestOutcome.reconciliation("uncertain_transport")

            body = self._response_json(response)
            provider_id = body.get("id") if body is not None else None
            if isinstance(provider_id, (str, int)) and str(provider_id).strip():
                try:
                    return AcessoriasRequestOutcome.success(
                        str(provider_id), provider_status=response.status_code
                    )
                except ValueError:
                    return AcessoriasRequestOutcome.reconciliation(
                        "invalid_provider_id", provider_status=response.status_code
                    )

            status = response.status_code
            if status == 429:
                # Acessórias does not provide a documented non-creation proof
                # for this POST. Retry-After only describes admission timing;
                # it cannot establish that the provider did not process the
                # request, so the operation must be reconciled before replay.
                return AcessoriasRequestOutcome.reconciliation(
                    "uncertain_rate_limit", provider_status=status
                )
            if status in {408, 425}:
                if attempt < self.max_attempts:
                    self.sleep(self._retry_delay(attempt, self._retry_after(response)))
                    continue
                return AcessoriasRequestOutcome.retryable(
                    "safe_transient_rejection",
                    provider_status=status,
                )
            if status in {401, 403}:
                return AcessoriasRequestOutcome.definitive(
                    "authentication_or_permission", provider_status=status
                )
            if status >= 500:
                return AcessoriasRequestOutcome.reconciliation(
                    "uncertain_5xx", provider_status=status
                )
            if body is not None and any(key in body for key in ("Erro", "erro")):
                return AcessoriasRequestOutcome.definitive(
                    "provider_error", provider_status=status
                )
            if status >= 400:
                return AcessoriasRequestOutcome.definitive(
                    "provider_rejection", provider_status=status
                )
            return AcessoriasRequestOutcome.reconciliation(
                "missing_id", provider_status=status
            )
        return AcessoriasRequestOutcome.retryable("bounded_retry_exhausted")


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _serialize_operation(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if row is None:
        raise RuntimeError("Acessórias Request operation is unavailable")
    return {key: _iso(value) for key, value in row.items()}


def _cycle_snapshot(connection: psycopg.Connection[Any], cycle_public_id: str) -> dict[str, Any] | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            """
            SELECT cycle.id, cycle.public_id, cycle.conversation_id, cycle.status,
                   cycle.warning_count, cycle.classification_id,
                   classification.title, classification.description
            FROM conversation_processing_cycles AS cycle
            LEFT JOIN ia_classifications AS classification
              ON classification.id = cycle.classification_id
            WHERE cycle.public_id = %s
            """,
            (cycle_public_id,),
        ).fetchone()
    return cast(dict[str, Any] | None, row)


def _mapping_snapshot(
    connection: psycopg.Connection[Any], cycle_id: int
) -> dict[str, Any] | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            """
            SELECT *
            FROM conversation_cycle_department_mappings
            WHERE cycle_id = %s AND evaluation_key = 'default'
            ORDER BY id DESC
            LIMIT 1
            """,
            (cycle_id,),
        ).fetchone()
    return cast(dict[str, Any] | None, row)


def _operation_state(
    connection: psycopg.Connection[Any],
    cycle: Mapping[str, Any],
    mapping: Mapping[str, Any] | None,
) -> tuple[str, str | None, dict[str, Any] | None, AcessoriasRequestPayload | None]:
    status = str(cycle["status"])
    classification_id = cycle.get("classification_id")
    if status not in {"completed", "completed_with_warnings"}:
        return "definitive_failure", "cycle_not_eligible", None, None
    if classification_id is None:
        return "definitive_failure", "classification_missing", None, None
    title = cycle.get("title")
    description = cycle.get("description")
    if not isinstance(title, str) or not title.strip():
        return "definitive_failure", "classification_title_missing", None, None
    if not isinstance(description, str):
        return "definitive_failure", "classification_description_missing", None, None
    if mapping is None:
        return "definitive_failure", "mapping_missing", None, None
    if mapping.get("state") != "resolved":
        return "definitive_failure", _safe_category(str(mapping.get("reason") or "mapping_unresolved")), None, None
    company_id = mapping.get("company_id")
    department_id = mapping.get("acessorias_department_external_id")
    if company_id is None or not isinstance(department_id, str):
        return "definitive_failure", "mapping_facts_missing", None, None
    with connection.cursor(row_factory=dict_row) as cursor:
        company = cursor.execute(
            """
            SELECT external_id
            FROM acessorias_companies
            WHERE id = %s AND is_present IS TRUE AND is_active IS TRUE
            """,
            (company_id,),
        ).fetchone()
        relationship = cursor.execute(
            """
            SELECT 1
            FROM acessorias_company_departments AS relation
            JOIN acessorias_departments AS department
              ON department.id = relation.department_id
            WHERE relation.company_id = %s
              AND department.external_id = %s
              AND relation.is_present IS TRUE
              AND relation.is_active IS TRUE
              AND department.is_present IS TRUE
              AND department.is_active IS TRUE
            """,
            (company_id, department_id),
        ).fetchone()
    if company is None:
        return "definitive_failure", "company_unavailable", None, None
    if relationship is None:
        return "definitive_failure", "company_department_unavailable", None, None
    try:
        payload = build_request_payload(
            title=title,
            description=description,
            company_external_id=str(company["external_id"]),
            department_external_id=department_id,
        )
    except ValueError as exc:
        message = str(exc)
        if "department" in message:
            reason = "department_identifier_invalid"
        elif "description" in message:
            reason = "classification_description_missing"
        else:
            reason = "classification_title_missing"
        return "definitive_failure", reason, None, None
    return "not_started", None, {
        "company_id": int(company_id),
        "company_external_id": str(company["external_id"]),
        "department_mapping_id": int(mapping["id"]),
        "department_external_id": department_id,
    }, payload


def _ensure_operation_sync(cycle_public_id: str) -> dict[str, Any]:
    with get_database_pool().connection() as connection:
        with connection.transaction():
            cycle = _cycle_snapshot(connection, cycle_public_id)
            if cycle is None:
                raise LookupError("conversation cycle not found")
            mapping = _mapping_snapshot(connection, int(cycle["id"]))
            state, reason, facts, payload = _operation_state(connection, cycle, mapping)
            metadata = payload.metadata if payload is not None else {}
            fingerprint = payload.fingerprint if payload is not None else None
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    """
                    INSERT INTO acessorias_request_operations (
                        source_cycle_id, source_classification_id, conversation_id,
                        company_id, company_external_id, department_mapping_id,
                        department_external_id, payload_fingerprint,
                        payload_metadata_json, state, failure_category,
                        failure_message
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (source_cycle_id) DO NOTHING
                    RETURNING *
                    """,
                    (
                        cycle["id"],
                        cycle.get("classification_id"),
                        cycle["conversation_id"],
                        None if facts is None else facts["company_id"],
                        None if facts is None else facts["company_external_id"],
                        None if mapping is None else mapping.get("id"),
                        None if facts is None else facts["department_external_id"],
                        fingerprint,
                        Jsonb(metadata),
                        state,
                        reason,
                        reason,
                    ),
                ).fetchone()
            if row is None:
                with connection.cursor(row_factory=dict_row) as cursor:
                    row = cursor.execute(
                        """
                        SELECT *
                        FROM acessorias_request_operations
                        WHERE source_cycle_id = %s
                        FOR UPDATE
                        """,
                        (cycle["id"],),
                    ).fetchone()
            return _serialize_operation(cast(Mapping[str, Any] | None, row))


def _claim_operation_sync(operation_id: int, owner: str, lease_seconds: int) -> dict[str, Any] | None:
    with get_database_pool().connection() as connection:
        with connection.transaction():
            stale = connection.execute(
                """
                SELECT id, post_started_at
                FROM acessorias_request_operations
                WHERE id = %s AND state = 'attempting'
                  AND claim_expires_at IS NOT NULL
                  AND claim_expires_at <= CURRENT_TIMESTAMP
                FOR UPDATE
                """,
                (operation_id,),
            ).fetchone()
            if stale is not None:
                if stale[1] is None:
                    connection.execute(
                        """
                        UPDATE acessorias_request_operations
                        SET state = 'retryable_failure',
                            failure_category = 'crash_before_post',
                            failure_message = 'crash_before_post',
                            claim_owner = NULL, claim_expires_at = NULL,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                        """,
                        (operation_id,),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE acessorias_request_operations
                        SET state = 'reconciliation_required',
                            failure_category = 'claim_expired_after_post_start',
                            failure_message = 'claim_expired_after_post_start',
                            claim_owner = NULL, claim_expires_at = NULL,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                        """,
                        (operation_id,),
                    )
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    """
                    UPDATE acessorias_request_operations
                    SET state = 'attempting', claim_owner = %s,
                        claim_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                      AND state IN ('not_started', 'retryable_failure')
                    RETURNING *
                    """,
                    (owner, lease_seconds, operation_id),
                ).fetchone()
    return _serialize_operation(cast(Mapping[str, Any] | None, row)) if row else None


def _get_operation_for_cycle_sync(cycle_public_id: str) -> dict[str, Any] | None:
    with get_database_pool().connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute(
                """
                SELECT operation.*
                FROM acessorias_request_operations AS operation
                JOIN conversation_processing_cycles AS cycle
                  ON cycle.id = operation.source_cycle_id
                WHERE cycle.public_id = %s
                """,
                (cycle_public_id,),
            ).fetchone()
    return None if row is None else _serialize_operation(cast(Mapping[str, Any], row))


def _claim_mapping_missing_recovery_sync(
    cycle_public_id: str, owner: str, lease_seconds: int
) -> dict[str, Any] | None:
    with get_database_pool().connection() as connection:
        with connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    """
                    SELECT operation.*
                    FROM acessorias_request_operations AS operation
                    JOIN conversation_processing_cycles AS cycle
                      ON cycle.id = operation.source_cycle_id
                    WHERE cycle.public_id = %s
                      AND operation.state = 'definitive_failure'
                      AND operation.failure_category = 'mapping_missing'
                      AND operation.post_started_at IS NULL
                      AND operation.sol_id IS NULL
                      AND operation.attempt_count = 0
                      AND operation.first_attempt_at IS NULL
                      AND operation.last_attempt_at IS NULL
                      AND operation.reconciliation_json = '{}'::jsonb
                    FOR UPDATE
                    """,
                    (cycle_public_id,),
                ).fetchone()
                if row is None:
                    return None
                claim_expires_at = row.get("claim_expires_at")
                if (
                    row.get("claim_owner") is not None
                    and claim_expires_at is not None
                    and claim_expires_at > datetime.now(timezone.utc)
                    and row.get("claim_owner") != owner
                ):
                    return None
                claimed = cursor.execute(
                    """
                    UPDATE acessorias_request_operations
                    SET claim_owner = %s,
                        claim_expires_at = CURRENT_TIMESTAMP
                            + (%s * INTERVAL '1 second'),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                      AND state = 'definitive_failure'
                      AND failure_category = 'mapping_missing'
                      AND post_started_at IS NULL
                      AND sol_id IS NULL
                      AND attempt_count = 0
                      AND first_attempt_at IS NULL
                      AND last_attempt_at IS NULL
                    RETURNING *
                    """,
                    (owner, lease_seconds, row["id"]),
                ).fetchone()
    return _serialize_operation(cast(Mapping[str, Any] | None, claimed)) if claimed else None


def _record_preparation_recovery_blocked_sync(
    operation_id: int,
    owner: str,
    *,
    stage: str,
    reason: str,
) -> dict[str, Any]:
    safe_stage = _safe_category(stage)
    safe_reason = _safe_category(reason)
    with get_database_pool().connection() as connection:
        with connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    """
                    UPDATE acessorias_request_operations
                    SET claim_owner = NULL,
                        claim_expires_at = NULL,
                        preparation_recovery_json = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                      AND state = 'definitive_failure'
                      AND failure_category = 'mapping_missing'
                      AND claim_owner = %s
                      AND post_started_at IS NULL
                      AND sol_id IS NULL
                    RETURNING *
                    """,
                    (
                        Jsonb(
                            {
                                "source": "internal_preparation_recovery",
                                "status": "blocked",
                                "stage": safe_stage,
                                "reason": safe_reason,
                            }
                        ),
                        operation_id,
                        owner,
                    ),
                ).fetchone()
    if row is None:
        raise RuntimeError("preparation recovery claim was lost")
    return _serialize_operation(cast(Mapping[str, Any], row))


def _reopen_prepared_operation_sync(
    operation_id: int, owner: str, cycle_public_id: str
) -> dict[str, Any]:
    with get_database_pool().connection() as connection:
        with connection.transaction():
            cycle = _cycle_snapshot(connection, cycle_public_id)
            if cycle is None:
                raise LookupError("conversation cycle not found")
            mapping = _mapping_snapshot(connection, int(cycle["id"]))
            state, reason, facts, payload = _operation_state(connection, cycle, mapping)
            if state != "not_started" or facts is None or payload is None:
                raise AcessoriasRequestError(
                    _safe_category(reason or "preparation_not_ready"),
                    "prepared Request facts are not valid",
                )
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    """
                    UPDATE acessorias_request_operations
                    SET company_id = %s,
                        company_external_id = %s,
                        department_mapping_id = %s,
                        department_external_id = %s,
                        payload_fingerprint = %s,
                        payload_metadata_json = %s,
                        state = 'not_started',
                        failure_category = NULL,
                        failure_message = NULL,
                        preparation_recovery_json = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                      AND state = 'definitive_failure'
                      AND failure_category = 'mapping_missing'
                      AND claim_owner = %s
                      AND post_started_at IS NULL
                      AND sol_id IS NULL
                      AND attempt_count = 0
                      AND first_attempt_at IS NULL
                      AND last_attempt_at IS NULL
                    RETURNING *
                    """,
                    (
                        facts["company_id"],
                        facts["company_external_id"],
                        facts["department_mapping_id"],
                        facts["department_external_id"],
                        payload.fingerprint,
                        Jsonb(payload.metadata),
                        Jsonb(
                            {
                                "source": "internal_preparation_recovery",
                                "status": "prepared",
                                "stage": "ready",
                                "reason": "mapping_missing_recovered",
                            }
                        ),
                        operation_id,
                        owner,
                    ),
                ).fetchone()
    if row is None:
        raise RuntimeError("preparation recovery claim was lost")
    return _serialize_operation(cast(Mapping[str, Any], row))


def _load_payload_sync(operation_id: int) -> AcessoriasRequestPayload:
    with get_database_pool().connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute(
                """
                SELECT classification.title, classification.description,
                       operation.company_external_id,
                       operation.department_external_id
                FROM acessorias_request_operations AS operation
                JOIN ia_classifications AS classification
                  ON classification.id = operation.source_classification_id
                WHERE operation.id = %s
                """,
                (operation_id,),
            ).fetchone()
    if row is None:
        raise LookupError("Request classification facts not found")
    return build_request_payload(
        title=cast(str, row["title"]),
        description=cast(str, row["description"]),
        company_external_id=cast(str, row["company_external_id"]),
        department_external_id=cast(str, row["department_external_id"]),
    )


def _mark_post_started_sync(operation_id: int, owner: str) -> bool:
    with get_database_pool().connection() as connection:
        with connection.transaction():
            row = connection.execute(
                """
                UPDATE acessorias_request_operations
                SET post_started_at = CURRENT_TIMESTAMP,
                    attempt_count = attempt_count + 1,
                    first_attempt_at = COALESCE(first_attempt_at, CURRENT_TIMESTAMP),
                    last_attempt_at = CURRENT_TIMESTAMP,
                    failure_category = NULL, failure_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND state = 'attempting' AND claim_owner = %s
                RETURNING id
                """,
                (operation_id, owner),
            ).fetchone()
    return row is not None


def _finish_operation_sync(
    operation_id: int, owner: str, outcome: AcessoriasRequestOutcome
) -> dict[str, Any]:
    state = outcome.state
    if state not in {
        "completed",
        "definitive_failure",
        "retryable_failure",
        "reconciliation_required",
    }:
        raise ValueError("unsupported Request outcome")
    if state == "completed" and not outcome.solid_id:
        raise ValueError("completed Request outcome requires SolID")
    with get_database_pool().connection() as connection:
        with connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    """
                    UPDATE acessorias_request_operations
                    SET state = %s,
                        sol_id = %s,
                        last_provider_status = %s,
                        failure_category = %s,
                        failure_message = %s,
                        completed_at = CASE WHEN %s = 'completed' THEN CURRENT_TIMESTAMP ELSE NULL END,
                        claim_owner = NULL,
                        claim_expires_at = NULL,
                        reconciliation_json = CASE
                            WHEN %s = 'reconciliation_required'
                            THEN jsonb_build_object('required', TRUE, 'category', %s::text)
                            ELSE reconciliation_json
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND state = 'attempting' AND claim_owner = %s
                    RETURNING *
                    """,
                    (
                        state,
                        outcome.solid_id,
                        outcome.provider_status,
                        None if state == "completed" else outcome.category,
                        None if state == "completed" else outcome.category,
                        state,
                        state,
                        outcome.category,
                        operation_id,
                        owner,
                    ),
                ).fetchone()
    if row is None:
        with get_database_pool().connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    "SELECT * FROM acessorias_request_operations WHERE id = %s",
                    (operation_id,),
                ).fetchone()
    return _serialize_operation(cast(Mapping[str, Any] | None, row))


async def create_request_for_cycle(
    cycle_public_id: str,
    *,
    provider: AcessoriasRequestProvider | None = None,
    owner: str | None = None,
) -> dict[str, Any]:
    """Create or replay the one durable Request operation for a terminal cycle."""
    operation = await asyncio.to_thread(_ensure_operation_sync, cycle_public_id)
    if operation["state"] not in {"not_started", "retryable_failure"}:
        return operation
    operation_id = int(operation["id"])
    claim_owner = _claim_owner(owner, prefix="request")
    claimed = await asyncio.to_thread(
        _claim_operation_sync,
        operation_id,
        claim_owner,
        settings.finalization_lease_seconds,
    )
    if claimed is None:
        with get_database_pool().connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    "SELECT * FROM acessorias_request_operations WHERE id = %s",
                    (operation_id,),
                ).fetchone()
        return _serialize_operation(cast(Mapping[str, Any] | None, row))
    try:
        payload = await asyncio.to_thread(_load_payload_sync, operation_id)
    except Exception:
        return await asyncio.to_thread(
            _finish_operation_sync,
            operation_id,
            claim_owner,
            AcessoriasRequestOutcome.retryable("payload_load_failed"),
        )
    try:
        request_provider = provider or AcessoriasRequestAdapter()
    except Exception:
        return await asyncio.to_thread(
            _finish_operation_sync,
            operation_id,
            claim_owner,
            AcessoriasRequestOutcome.retryable("provider_setup_failed"),
        )
    if not await asyncio.to_thread(_mark_post_started_sync, operation_id, claim_owner):
        return claimed
    try:
        outcome = await asyncio.to_thread(request_provider.create_request, payload)
    except Exception:
        outcome = AcessoriasRequestOutcome.reconciliation("provider_exception")
    return await asyncio.to_thread(
        _finish_operation_sync, operation_id, claim_owner, outcome
    )


async def recover_mapping_missing_request(
    cycle_public_id: str,
    *,
    provider: AcessoriasRequestProvider | None = None,
    owner: str | None = None,
) -> dict[str, Any] | None:
    """Recover only a proven pre-POST operation after canonical preparation succeeds."""
    claim_owner = _claim_owner(owner, prefix="preparation-recovery")
    claimed = await asyncio.to_thread(
        _claim_mapping_missing_recovery_sync,
        cycle_public_id,
        claim_owner,
        settings.finalization_lease_seconds,
    )
    if claimed is None:
        return await asyncio.to_thread(_get_operation_for_cycle_sync, cycle_public_id)

    from src.core.acessorias_preparation import prepare_cycle_for_request

    preparation = await prepare_cycle_for_request(cycle_public_id)
    if not preparation.ready:
        return await asyncio.to_thread(
            _record_preparation_recovery_blocked_sync,
            int(claimed["id"]),
            claim_owner,
            stage=preparation.stage,
            reason=preparation.reason,
        )
    await asyncio.to_thread(
        _reopen_prepared_operation_sync,
        int(claimed["id"]),
        claim_owner,
        cycle_public_id,
    )
    return await create_request_for_cycle(
        cycle_public_id,
        provider=provider,
        owner=claim_owner,
    )


def _operation_for_cycle_sync(connection: psycopg.Connection[Any], cycle_public_id: str) -> dict[str, Any]:
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            """
            SELECT operation.*
            FROM acessorias_request_operations AS operation
            JOIN conversation_processing_cycles AS cycle
              ON cycle.id = operation.source_cycle_id
            WHERE cycle.public_id = %s
            FOR UPDATE
            """,
            (cycle_public_id,),
        ).fetchone()
    if row is None:
        raise LookupError("Acessórias Request operation not found")
    return cast(dict[str, Any], row)


def _record_reconciliation_sync(
    cycle_public_id: str,
    *,
    action: str,
    solid_id: str | None,
    reason: str,
    operation_key: str,
    actor: str | None,
    proof_of_absence: bool = False,
) -> dict[str, Any]:
    safe_reason = _safe_reason(reason)
    safe_key = _safe_operation_key(operation_key)
    safe_actor = _safe_actor(actor)
    if action == "record_solid":
        if solid_id is None or not SAFE_SOL_ID.fullmatch(solid_id.strip()):
            raise ValueError("solid_id is required and must be safe")
        safe_solid = solid_id.strip()
    else:
        safe_solid = None
        if not proof_of_absence:
            raise ValueError("release requires explicit proof of remote absence")
    with get_database_pool().connection() as connection:
        with connection.transaction():
            operation = _operation_for_cycle_sync(connection, cycle_public_id)
            existing_audit = connection.execute(
                "SELECT * FROM acessorias_request_reconciliations WHERE operation_key = %s",
                (safe_key,),
            ).fetchone()
            if existing_audit is not None:
                if (
                    int(existing_audit["operation_id"] if isinstance(existing_audit, Mapping) else existing_audit[1])
                    != int(operation["id"])
                ):
                    raise AcessoriasRequestConflictError()
                return _serialize_operation(operation)
            current_state = str(operation["state"])
            if action == "record_solid":
                existing_solid = operation.get("sol_id")
                if current_state not in {"reconciliation_required", "completed"}:
                    raise AcessoriasRequestError(
                        "reconciliation_not_required",
                        "operation does not require reconciliation",
                    )
                if current_state == "completed" and existing_solid != safe_solid:
                    raise AcessoriasRequestConflictError()
            elif current_state != "reconciliation_required":
                raise AcessoriasRequestConflictError(
                    "Request is not awaiting reconciliation"
                )
            connection.execute(
                """
                INSERT INTO acessorias_request_reconciliations (
                    operation_id, operation_key, action, source, sol_id,
                    reason, actor, evidence_json
                ) VALUES (%s, %s, %s, 'manual_db', %s, %s, %s, %s)
                """,
                (
                    operation["id"],
                    safe_key,
                    action,
                    safe_solid,
                    safe_reason,
                    safe_actor,
                    Jsonb({"verified_remote_absence": action == "release_retry"}),
                ),
            )
            if action == "record_solid":
                connection.execute(
                    """
                    UPDATE acessorias_request_operations
                    SET state = 'completed', sol_id = %s,
                        completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                        failure_category = NULL, failure_message = NULL,
                        claim_owner = NULL, claim_expires_at = NULL,
                        reconciliation_json = jsonb_build_object(
                            'source', 'manual_db', 'action', 'record_solid',
                            'operation_key', %s::text, 'reason', %s::text
                        ),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (safe_solid, safe_key, safe_reason, operation["id"]),
                )
            else:
                connection.execute(
                    """
                    UPDATE acessorias_request_operations
                    SET state = 'retryable_failure',
                        failure_category = 'released_after_remote_absence',
                        failure_message = 'released_after_remote_absence',
                        claim_owner = NULL, claim_expires_at = NULL,
                        reconciliation_json = jsonb_build_object(
                            'source', 'manual_db', 'action', 'release_retry',
                            'operation_key', %s::text, 'reason', %s::text
                        ),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (safe_key, safe_reason, operation["id"]),
                )
            with connection.cursor(row_factory=dict_row) as cursor:
                updated = cursor.execute(
                    "SELECT * FROM acessorias_request_operations WHERE id = %s",
                    (operation["id"],),
                ).fetchone()
    return _serialize_operation(cast(Mapping[str, Any] | None, updated))


async def reconcile_request_operation(
    cycle_public_id: str,
    *,
    solid_id: str,
    reason: str,
    operation_key: str,
    actor: str | None = None,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        _record_reconciliation_sync,
        cycle_public_id,
        action="record_solid",
        solid_id=solid_id,
        reason=reason,
        operation_key=operation_key,
        actor=actor,
    )


async def release_request_operation(
    cycle_public_id: str,
    *,
    reason: str,
    operation_key: str,
    proof_of_absence: bool = False,
    actor: str | None = None,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        _record_reconciliation_sync,
        cycle_public_id,
        action="release_retry",
        solid_id=None,
        reason=reason,
        operation_key=operation_key,
        actor=actor,
        proof_of_absence=proof_of_absence,
    )
