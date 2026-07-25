from functools import lru_cache
from typing import Any, Protocol, cast

import redis.asyncio as redis

from src.core.config import settings


class AsyncRedis(Protocol):
    """Subset of the decoded async Redis API used by this application."""

    async def aclose(self) -> None: ...
    async def delete(self, *names: str) -> int: ...
    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> Any: ...
    async def exists(self, name: str) -> int: ...
    async def get(self, name: str) -> str | None: ...
    async def llen(self, name: str) -> int: ...
    async def lpop(self, name: str) -> str | None: ...
    async def lpush(self, name: str, *values: str) -> int: ...
    async def lrange(self, name: str, start: int, end: int) -> list[str]: ...
    async def lrem(self, name: str, count: int, value: str) -> int: ...
    async def ping(self) -> bool: ...
    async def rpush(self, name: str, *values: str) -> int: ...
    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None: ...
    async def setex(self, name: str, time: int, value: str) -> bool: ...


@lru_cache()
def get_redis_client() -> AsyncRedis:
    """Factory para obter cliente Redis configurado"""
    return cast(AsyncRedis, redis.from_url(
        settings.redis_url,
        db=settings.redis_db,
        decode_responses=True,
        max_connections=settings.redis_max_connections,
    ))


def create_redis_client() -> AsyncRedis:
    """Return a Redis client for API/worker lifecycle management.

    This intentionally does not use the cached instance: each process owns and
    closes its client through FastAPI lifespan or the worker runner.
    """
    return cast(AsyncRedis, redis.from_url(
        settings.redis_url,
        db=settings.redis_db,
        decode_responses=True,
        max_connections=settings.redis_max_connections,
    ))


# Singleton para uso direto
redis_client = get_redis_client()
