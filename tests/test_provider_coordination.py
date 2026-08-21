from __future__ import annotations

from src.core.acessorias_directory import AcessoriasDirectoryAdapter
from src.core.acessorias_requests import AcessoriasRequestAdapter
from src.core.provider_coordination import SlidingWindowRateLimiter


def test_acessorias_adapters_use_the_neutral_coordination_boundary() -> None:
    directory = AcessoriasDirectoryAdapter(token="test-token", rate_limit_per_minute=100)
    request = AcessoriasRequestAdapter(
        base_url="https://api.example.test",
        token="test-token",
        rate_limit_per_minute=100,
    )

    assert type(directory.rate_limiter) is SlidingWindowRateLimiter
    assert type(request.rate_limiter) is SlidingWindowRateLimiter
    assert type(directory.rate_limiter).__module__ == "src.core.provider_coordination"
    assert type(request.rate_limiter).__module__ == "src.core.provider_coordination"
