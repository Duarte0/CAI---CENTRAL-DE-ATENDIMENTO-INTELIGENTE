"""Small, retrying DigiSac client used by final conversation recovery."""

from __future__ import annotations

import email.utils
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, cast

import requests

from src.core.config import settings


logger = logging.getLogger(__name__)
TRANSIENT_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class DigisacClientError(RuntimeError):
    """Permanent or exhausted DigiSac request failure."""


class DigisacResponseError(DigisacClientError):
    """DigiSac returned a successful but structurally invalid response."""


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
            raise RuntimeError("DIGISAC_API_KEY is not configured")
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
                    parsed = email.utils.parsedate_to_datetime(retry_after)
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
                        f"{type(exc).__name__}"
                    ) from exc
                time.sleep(self._retry_delay(attempt))
                continue
            if response.status_code in TRANSIENT_HTTP_STATUSES:
                if attempt >= self.max_attempts:
                    raise DigisacClientError(
                        f"DigiSac HTTP {response.status_code} after "
                        f"{attempt} attempts"
                    )
                time.sleep(self._retry_delay(attempt, response))
                continue
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise DigisacClientError(
                    f"DigiSac permanent HTTP {response.status_code}"
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
