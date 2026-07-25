"""Synchronize DigiSac departments and users into PostgreSQL."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Mapping, cast

import requests  # type: ignore[import-untyped]

from src.core.config import settings
from src.core.db import (
    directory_refresh_is_due,
    mark_directory_sync_attempt,
    upsert_digisac_directory,
)

logger = logging.getLogger(__name__)
TRANSIENT_STATUSES = {408, 425, 429, 500, 502, 503, 504}
_sync_lock = asyncio.Lock()


class TransientDirectoryError(RuntimeError):
    """A DigiSac directory failure that may succeed on a controlled retry."""


def _fetch_resource(resource: str) -> list[Mapping[str, Any]]:
    if not settings.digisac_api_key:
        raise RuntimeError("DIGISAC_API_KEY is not configured")
    base_url = settings.digisac_api_base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {settings.digisac_api_key}"}
    entries: list[Mapping[str, Any]] = []
    page = 1
    last_page = 1
    while page <= last_page:
        last_error: Exception | None = None
        max_retries = max(1, settings.digisac_directory_max_retries)
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.get(
                    f"{base_url}/{resource}",
                    params={"page": page},
                    headers=headers,
                    timeout=settings.digisac_directory_timeout_seconds,
                )
                if response.status_code in TRANSIENT_STATUSES:
                    raise TransientDirectoryError(
                        f"DigiSac {resource} page {page} returned "
                        f"HTTP {response.status_code}"
                    )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError(
                        f"DigiSac {resource} response must be an object"
                    )
                raw_entries = payload.get("data")
                current_page = payload.get("currentPage")
                raw_last_page = payload.get("lastPage")
                if not isinstance(raw_entries, list):
                    raise ValueError(
                        f"DigiSac {resource} response is missing data list"
                    )
                if not isinstance(current_page, int) or not isinstance(
                    raw_last_page, int
                ):
                    raise ValueError(
                        f"DigiSac {resource} response has invalid pagination"
                    )
                entries.extend(
                    cast(Mapping[str, Any], item)
                    for item in raw_entries
                    if isinstance(item, Mapping)
                )
                last_page = max(raw_last_page, current_page)
                page = current_page + 1
                last_error = None
                break
            except (
                requests.Timeout,
                requests.ConnectionError,
                TransientDirectoryError,
            ) as exc:
                last_error = exc
                if attempt < max_retries:
                    time.sleep(min(2 ** (attempt - 1), 4))
        if last_error is not None:
            raise last_error
    return entries


async def sync_digisac_directories(*, force: bool = False) -> bool:
    """Refresh both directory tables, preserving the previous cache on failure."""
    if not settings.digisac_api_key:
        logger.warning("DigiSac directory sync skipped: API key is not configured")
        return False
    async with _sync_lock:
        if not force and not await directory_refresh_is_due(
            settings.digisac_directory_refresh_cooldown_seconds
        ):
            return False
        success = True
        for resource in ("departments", "users"):
            attempted_at = datetime.now(timezone.utc).isoformat()
            await mark_directory_sync_attempt(resource, attempted_at)
            try:
                entries = await asyncio.to_thread(_fetch_resource, resource)
                synced_at = datetime.now(timezone.utc).isoformat()
                count = await upsert_digisac_directory(
                    resource, entries, synced_at
                )
                logger.info(
                    "DigiSac directory synchronized: resource=%s count=%s",
                    resource,
                    count,
                )
            except Exception:
                success = False
                logger.exception(
                    "DigiSac directory synchronization failed: resource=%s",
                    resource,
                )
        return success


async def directory_sync_loop() -> None:
    """Run an immediate non-blocking refresh and then refresh once per day."""
    while True:
        try:
            await sync_digisac_directories(force=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected DigiSac directory sync loop failure")
        await asyncio.sleep(settings.digisac_directory_sync_interval_seconds)
