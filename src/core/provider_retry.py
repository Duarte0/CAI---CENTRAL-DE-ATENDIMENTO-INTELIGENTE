"""Shared provider retry classification and Retry-After parsing."""

import email.utils
import re
from datetime import datetime, timezone

from groq import APIConnectionError, APITimeoutError


TRANSIENT_PROVIDER_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class TransientProviderError(RuntimeError):
    """A provider/network failure that should remain durably retryable."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        category: str = "transient_provider",
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.category = category


class ProviderRetryWindowActive(TransientProviderError):
    """Local guard indicating that no provider request was attempted."""

    def __init__(self, *, retry_after_seconds: float) -> None:
        super().__init__(
            "Groq provider retry window is still active",
            retry_after_seconds=retry_after_seconds,
            category="local_retry_window",
        )


def retry_after_from_text(message: str) -> float | None:
    match = re.search(
        r"(?:please\s+)?(?:try again|retry)(?:\s+after|\s+in)?\s+"
        r"(?:(?P<hours>\d+(?:\.\d+)?)h)?\s*"
        r"(?:(?P<minutes>\d+(?:\.\d+)?)m)?\s*"
        r"(?:(?P<seconds>\d+(?:\.\d+)?)s)?",
        message,
        flags=re.IGNORECASE,
    )
    if not match or not any(match.groupdict().values()):
        return None
    hours = float(match.group("hours") or 0)
    minutes = float(match.group("minutes") or 0)
    seconds = float(match.group("seconds") or 0)
    return max(0.0, hours * 3600 + minutes * 60 + seconds)


def retry_after_seconds(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or getattr(exc, "headers", None)
    if headers is not None:
        try:
            retry_after = (
                headers.get("retry-after") or headers.get("Retry-After")
                or headers.get("x-ratelimit-reset-tokens")
                or headers.get("X-RateLimit-Reset-Tokens")
            )
        except Exception:
            retry_after = None
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except (TypeError, ValueError):
                duration = retry_after_from_text(
                    f"try again in {retry_after}"
                )
                if duration is not None:
                    return duration
                try:
                    parsed = email.utils.parsedate_to_datetime(str(retry_after))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    return max(
                        0.0,
                        (parsed - datetime.now(timezone.utc)).total_seconds(),
                    )
                except (TypeError, ValueError, OverflowError):
                    pass
    return retry_after_from_text(str(exc))


def transient_provider_error(
    exc: BaseException, operation: str
) -> TransientProviderError | None:
    status_code = getattr(exc, "status_code", None)
    if status_code in TRANSIENT_PROVIDER_STATUSES or isinstance(
        exc, (APIConnectionError, ConnectionError, TimeoutError)
    ):
        if status_code == 429:
            category = "rate_limit"
        elif status_code in {500, 502, 503, 504}:
            category = "provider_server_error"
        elif status_code in {408, 425}:
            category = "transient_http"
        elif isinstance(exc, (APITimeoutError, TimeoutError)):
            category = "timeout"
        else:
            category = "connection"
        return TransientProviderError(
            f"{operation} failed: {exc}",
            retry_after_seconds=retry_after_seconds(exc),
            category=category,
        )
    return None


def retry_delay(
    *,
    attempt: int,
    base_seconds: float,
    max_delay_seconds: float,
    provider_margin_seconds: float,
    provider_delay_seconds: float | None,
) -> float:
    exponent = max(0, attempt - 1)
    local_delay = min(base_seconds * (2**exponent), max_delay_seconds)
    if provider_delay_seconds is None:
        return local_delay
    return max(
        local_delay,
        provider_delay_seconds + provider_margin_seconds,
    )


def provider_cooldown_delay(
    *,
    base_seconds: float,
    provider_margin_seconds: float,
    provider_delay_seconds: float | None,
) -> float:
    """Return a provider-wide cooldown independent of any cycle retry count."""

    if provider_delay_seconds is None:
        return base_seconds
    return provider_delay_seconds + provider_margin_seconds
