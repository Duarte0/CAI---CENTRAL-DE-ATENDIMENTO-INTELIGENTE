"""Small, retrying DigiSac client used by final conversation recovery."""

from __future__ import annotations

import email.utils
import logging
import random
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, cast
from urllib.parse import quote

import requests

from src.core.config import settings


logger = logging.getLogger(__name__)
TRANSIENT_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class DigisacClientError(RuntimeError):
    """Permanent or exhausted DigiSac request failure."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "provider",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code


class DigisacResponseError(DigisacClientError):
    """DigiSac returned a successful but structurally invalid response."""

    def __init__(self, message: str) -> None:
        super().__init__(message, category="invalid_response")


@dataclass(frozen=True)
class DigisacContact:
    """Safe, typed contact metadata retained by the local identity store."""

    external_id: str
    name: str | None = None
    alternative_name: str | None = None
    internal_name: str | None = None
    raw_number: str | None = None
    normalized_number: str | None = None
    is_group: bool | None = None
    account_id: str | None = None
    service_id: str | None = None
    provider_created_at: datetime | None = None
    provider_updated_at: datetime | None = None
    provider_deleted_at: datetime | None = None


def _optional_contact_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value)
    return normalized if normalized.strip() else None


def _contact_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_contact_number(value: Any) -> tuple[str | None, str | None]:
    raw = _optional_contact_string(value)
    if raw is None:
        return None, None
    digits: list[str] = []
    for character in raw:
        try:
            digits.append(str(unicodedata.decimal(character)))
        except (TypeError, ValueError):
            continue
    normalized = "".join(digits) or None
    return raw, normalized


def normalize_contact(payload: Mapping[str, Any]) -> DigisacContact:
    """Convert an observed provider object into the approved local shape."""
    if not isinstance(payload, Mapping):
        raise DigisacResponseError("DigiSac contact response is not an object")
    nested_data = payload.get("data")
    data = nested_data if isinstance(nested_data, Mapping) else {}

    external_id = _optional_contact_string(payload.get("id"))
    if external_id is None:
        raise DigisacResponseError("DigiSac contact response has no contact id")

    number_value = payload.get("number")
    if number_value is None:
        number_value = data.get("number")
    raw_number, normalized_number = _normalize_contact_number(number_value)

    is_group = payload.get("isGroup")
    if not isinstance(is_group, bool):
        is_group = None

    return DigisacContact(
        external_id=external_id,
        name=_optional_contact_string(payload.get("name")),
        alternative_name=_optional_contact_string(payload.get("alternativeName")),
        internal_name=_optional_contact_string(payload.get("internalName")),
        raw_number=raw_number,
        normalized_number=normalized_number,
        is_group=is_group,
        account_id=_optional_contact_string(payload.get("accountId")),
        service_id=_optional_contact_string(payload.get("serviceId")),
        provider_created_at=_contact_timestamp(payload.get("createdAt")),
        provider_updated_at=_contact_timestamp(payload.get("updatedAt")),
        provider_deleted_at=_contact_timestamp(payload.get("deletedAt")),
    )


@dataclass(frozen=True)
class DigisacHistory:
    ticket: dict[str, Any]
    messages: list[dict[str, Any]]
    page_count: int
    total: int
    duplicate_count: int
    complete: bool
    consistency_reasons: list[str]


class DigisacClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        max_attempts: int | None = None,
        retry_base_seconds: float | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = (base_url or settings.digisac_api_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.digisac_api_key
        if not self.api_key:
            raise DigisacClientError(
                "DigiSac credentials are not configured",
                category="credentials_missing",
            )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.digisac_history_request_timeout_seconds
        )
        self.max_attempts = (
            max_attempts
            if max_attempts is not None
            else settings.digisac_history_max_attempts
        )
        self.retry_base_seconds = (
            retry_base_seconds
            if retry_base_seconds is not None
            else settings.digisac_history_retry_base_seconds
        )
        self.session = session or requests.Session()

    def _retry_delay(
        self, attempt: int, response: requests.Response | None = None
    ) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return max(0.0, min(float(retry_after), 60.0))
                except ValueError:
                    try:
                        parsed = email.utils.parsedate_to_datetime(retry_after)
                    except (TypeError, ValueError, OverflowError):
                        parsed = None
                    if parsed is not None:
                        if parsed.tzinfo is None:
                            parsed = parsed.replace(tzinfo=timezone.utc)
                        return max(
                            0.0,
                            min(
                                (parsed - datetime.now(timezone.utc)).total_seconds(),
                                60.0,
                            ),
                        )
        base = self.retry_base_seconds * (2 ** max(0, attempt - 1))
        return base + random.uniform(0.0, min(base * 0.25, 1.0))

    def _get_json(
        self, path: str, *, params: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        response: requests.Response | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=self.timeout_seconds,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt >= self.max_attempts:
                    raise DigisacClientError(
                        f"DigiSac request failed after {attempt} attempts: "
                        f"{type(exc).__name__}",
                        category=(
                            "timeout"
                            if isinstance(exc, requests.Timeout)
                            else "connection"
                        ),
                    ) from exc
                time.sleep(self._retry_delay(attempt))
                continue
            if response.status_code in TRANSIENT_HTTP_STATUSES:
                if attempt >= self.max_attempts:
                    raise DigisacClientError(
                        f"DigiSac HTTP {response.status_code} after "
                        f"{attempt} attempts",
                        category="transient",
                        status_code=response.status_code,
                    )
                time.sleep(self._retry_delay(attempt, response))
                continue
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise DigisacClientError(
                    f"DigiSac permanent HTTP {response.status_code}",
                    category=(
                        "authentication"
                        if response.status_code in {401, 403}
                        else "permanent"
                    ),
                    status_code=response.status_code,
                ) from exc
            try:
                payload: Any = response.json()
            except ValueError as exc:
                raise DigisacResponseError(
                    "DigiSac response is not valid JSON"
                ) from exc
            if not isinstance(payload, Mapping):
                raise DigisacResponseError("DigiSac response must be a JSON object")
            return cast(Mapping[str, Any], payload)
        raise DigisacClientError("DigiSac request attempts exhausted")

    def get_ticket(self, conversation_id: str) -> dict[str, Any]:
        return dict(self._get_json(f"tickets/{conversation_id}"))

    def get_contact(self, contact_id: str) -> DigisacContact:
        """Fetch one contact through the configured individual endpoint."""
        normalized_id = contact_id.strip()
        if not normalized_id:
            raise DigisacResponseError("DigiSac contact id is blank")
        payload = self._get_json(f"contacts/{quote(normalized_id, safe='')}")
        candidate: Mapping[str, Any] = payload
        nested = payload.get("data")
        if (
            not _optional_contact_string(payload.get("id"))
            and isinstance(nested, Mapping)
        ):
            candidate = nested
        contact = normalize_contact(candidate)
        if contact.external_id != normalized_id:
            raise DigisacResponseError("DigiSac contact response id mismatch")
        return contact

    def get_ticket_history(self, conversation_id: str) -> DigisacHistory:
        ticket = self.get_ticket(conversation_id)
        page = 1
        last_page = 1
        total = 0
        page_count = 0
        duplicates = 0
        by_id: dict[str, dict[str, Any]] = {}
        reasons: list[str] = []
        while page <= last_page:
            payload = self._get_json(
                "messages",
                params={"where[ticketId]": conversation_id, "page": page},
            )
            data = payload.get("data")
            current_page = payload.get("currentPage")
            raw_last_page = payload.get("lastPage")
            raw_total = payload.get("total")
            if (
                not isinstance(data, list)
                or not isinstance(current_page, int)
                or not isinstance(raw_last_page, int)
                or not isinstance(raw_total, int)
                or current_page != page
                or raw_last_page < current_page
                or raw_total < 0
            ):
                raise DigisacResponseError(
                    "DigiSac message pagination has an invalid structure"
                )
            if not data and current_page < raw_last_page:
                raise DigisacResponseError(
                    "DigiSac returned an empty page before lastPage"
                )
            if page_count and raw_total != total:
                reasons.append("total_changed_during_pagination")
            total = raw_total
            last_page = raw_last_page
            page_count += 1
            for raw_message in data:
                if not isinstance(raw_message, Mapping):
                    reasons.append("non_object_message")
                    continue
                message = dict(raw_message)
                message_id = message.get("id")
                if not isinstance(message_id, str) or not message_id.strip():
                    reasons.append("message_without_id")
                    continue
                if message_id in by_id:
                    duplicates += 1
                by_id[message_id] = message
            page += 1
        messages = list(by_id.values())
        last_message_id = ticket.get("lastMessageId")
        if isinstance(last_message_id, str) and last_message_id:
            if last_message_id not in by_id:
                reasons.append("last_message_id_missing")
        elif total and not messages:
            reasons.append("reported_total_without_messages")
        if total != len(messages):
            reasons.append("total_unique_count_mismatch")
        return DigisacHistory(
            ticket=ticket,
            messages=messages,
            page_count=page_count,
            total=total,
            duplicate_count=duplicates,
            complete=not reasons,
            consistency_reasons=list(dict.fromkeys(reasons)),
        )
