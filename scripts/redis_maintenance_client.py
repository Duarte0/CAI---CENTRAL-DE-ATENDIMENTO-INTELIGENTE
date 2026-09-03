"""Redis client boundary for historical maintenance commands only.

The application runtime deliberately does not install or import Redis. These
helpers are copied into the separate ``maintenance`` image and require an
explicit ``MAINTENANCE_REDIS_URL`` so an old Redis endpoint cannot silently
become a runtime dependency again.
"""

from collections.abc import AsyncIterator
import os
from typing import Protocol, cast

import redis.asyncio as redis


class MaintenanceRedis(Protocol):
    async def aclose(self) -> None: ...
    async def delete(self, *names: str) -> int: ...
    async def get(self, name: str) -> str | None: ...
    async def llen(self, name: str) -> int: ...
    async def lpop(self, name: str) -> str | None: ...
    async def lrange(self, name: str, start: int, end: int) -> list[str]: ...
    async def lrem(self, name: str, count: int, value: str) -> int: ...
    async def ping(self) -> bool: ...
    async def rpush(self, name: str, *values: str) -> int: ...
    async def scan_iter(
        self, *, match: str | None = None, count: int | None = None
    ) -> AsyncIterator[str]: ...
    async def ttl(self, name: str) -> int: ...
    async def type(self, name: str) -> str: ...


def maintenance_redis_url_configured() -> bool:
    """Return whether an operator explicitly supplied the maintenance endpoint."""
    return bool(os.getenv("MAINTENANCE_REDIS_URL"))


def create_redis_client() -> MaintenanceRedis:
    """Create a client for a maintenance command, never for application startup."""
    url = os.getenv("MAINTENANCE_REDIS_URL")
    if not url:
        raise RuntimeError(
            "MAINTENANCE_REDIS_URL is required for Redis maintenance commands"
        )
    try:
        database = int(os.getenv("MAINTENANCE_REDIS_DB", "0"))
        max_connections = int(os.getenv("MAINTENANCE_REDIS_MAX_CONNECTIONS", "10"))
    except ValueError as exc:
        raise RuntimeError("Maintenance Redis settings must be integers") from exc
    return cast(
        MaintenanceRedis,
        redis.from_url(
            url,
            db=database,
            decode_responses=True,
            max_connections=max_connections,
        ),
    )
