from types import SimpleNamespace

import httpx
import pytest
from groq import APITimeoutError

from src.core.provider_retry import (
    provider_cooldown_delay,
    retry_after_from_text,
    retry_after_seconds,
    retry_delay,
    transient_provider_error,
)


def test_retry_after_parses_provider_windows():
    assert retry_after_from_text("Please try again in 32.6625s.") == pytest.approx(
        32.6625
    )
    assert retry_after_from_text("Please try again in 18m8.64s.") == pytest.approx(
        1088.64
    )


def test_retry_after_reads_response_header():
    error = RuntimeError("rate limited")
    error.response = SimpleNamespace(headers={"Retry-After": "45"})  # type: ignore[attr-defined]
    assert retry_after_seconds(error) == pytest.approx(45)


def test_provider_window_can_exceed_local_backoff_cap():
    assert retry_delay(
        attempt=12,
        base_seconds=2,
        max_delay_seconds=900,
        provider_margin_seconds=1,
        provider_delay_seconds=1088.64,
    ) == pytest.approx(1089.64)


def test_provider_cooldown_is_independent_from_cycle_attempt():
    assert provider_cooldown_delay(
        base_seconds=2,
        provider_margin_seconds=1,
        provider_delay_seconds=2.445,
    ) == pytest.approx(3.445)


def test_provider_cooldown_uses_base_without_retry_after():
    assert provider_cooldown_delay(
        base_seconds=2,
        provider_margin_seconds=1,
        provider_delay_seconds=None,
    ) == pytest.approx(2)


def test_provider_cooldown_honors_retry_after_above_cycle_cap():
    assert provider_cooldown_delay(
        base_seconds=2,
        provider_margin_seconds=1,
        provider_delay_seconds=1088.64,
    ) == pytest.approx(1089.64)


def test_groq_sdk_timeout_is_a_real_transient_provider_failure():
    error = APITimeoutError(request=httpx.Request("POST", "https://api.groq.com"))
    transient = transient_provider_error(error, "Groq classification request")

    assert transient is not None
    assert transient.category == "timeout"
    assert transient.retry_after_seconds is None
