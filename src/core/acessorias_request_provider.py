"""Acessórias Request provider transport boundary.

This module owns the external Request payload, outcome contract, and HTTP
adapter. It deliberately has no PostgreSQL, Redis, cycle, identity, mapping,
or durable-operation dependency.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Mapping, Protocol, cast
from urllib.parse import urlsplit

import requests

from src.core.config import settings
from src.core.provider_coordination import SlidingWindowRateLimiter


REQUEST_FIELDS = (
    "assunto",
    "empresa",
    "departamento",
    "prioridade",
    "descricao",
    "tipo",
)
DEFAULT_PRIORITY = 2
REQUEST_TYPE = "I"
SAFE_SOL_ID = re.compile(r"^[^\s\x00-\x1f\x7f]{1,160}$")
SAFE_DEPARTMENT_ID = re.compile(r"^[0-9]+$")


def _request_rate_limit_key(base_url: str, limit_per_minute: int) -> str:
    parsed = urlsplit(base_url)
    safe_netloc = parsed.netloc.rsplit("@", 1)[-1]
    endpoint = f"{parsed.scheme}://{safe_netloc}{parsed.path.rstrip('/')}"
    return f"request:{endpoint}:{limit_per_minute}"


class AcessoriasRequestPreSendError(requests.ConnectionError):
    """Transport failure explicitly proven before the POST could be sent.

    The stock ``requests`` exceptions do not identify whether a connection
    failed before or after request transmission. Only a transport boundary
    that can establish the pre-send fact may raise this marker; ordinary
    connection errors remain ambiguous and require reconciliation.
    """


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
    protocol: str | None = None,
    company_external_id: str,
    department_external_id: str,
) -> AcessoriasRequestPayload:
    """Build and locally validate the only six fields allowed by SPEC-0011."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("classification title is required")
    if not isinstance(description, str):
        raise ValueError("classification description is required")
    normalized_title = title.strip()
    normalized_protocol = protocol.strip() if isinstance(protocol, str) else ""
    subject_source = (
        f"[{normalized_protocol}] - {normalized_title}"
        if normalized_protocol
        else normalized_title
    )
    subject = subject_source[:100]
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
        """Create one internal Acessórias Request and classify safe metadata."""


def _safe_category(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_:-]+", "_", value.casefold()).strip("_")
    return normalized[:120] or "provider_error"


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
        self.rate_limiter = SlidingWindowRateLimiter(
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
