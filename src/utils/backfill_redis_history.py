"""Copy existing Redis IA results into the durable PostgreSQL history.

Redis only retains the final result and status after ticket processing completes;
the original buffer (context and message IDs) has already been deleted.  The
backfill therefore preserves the available result fields and records empty
context/message IDs for legacy rows.
"""

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.core.config import settings
from src.core.db import (
    classification_exists,
    initialize_database,
    insert_classification,
)
from src.core.redis_client import create_redis_client


logger = logging.getLogger(__name__)
LEGACY_PROMPT_VERSION = "legacy-redis-backfill"


def _created_at(result: dict[str, Any]) -> str:
    value = result.get("processed_at")
    if isinstance(value, str) and value:
        return value
    return datetime.now(timezone.utc).isoformat()


async def backfill(dry_run: bool = False) -> tuple[int, int, int]:
    """Return ``(inserted, skipped, invalid)`` after scanning ``ia_result:*``."""
    redis_client = create_redis_client()
    inserted = skipped = invalid = 0
    try:
        await initialize_database()
        async for key in redis_client.scan_iter(match="ia_result:*"):
            conversation_id = key.removeprefix("ia_result:")
            raw_result = await redis_client.get(key)
            try:
                result = json.loads(raw_result)
            except (TypeError, json.JSONDecodeError):
                logger.warning("Skipping invalid Redis result: %s", key)
                invalid += 1
                continue
            if not isinstance(result, dict):
                logger.warning("Skipping non-object Redis result: %s", key)
                invalid += 1
                continue

            created_at = _created_at(result)
            if await classification_exists(conversation_id, created_at):
                skipped += 1
                continue

            if not dry_run:
                await insert_classification(
                    conversation_id=conversation_id,
                    message_ids=[],
                    created_at=created_at,
                    full_context="",
                    message_count=int(result.get("message_count") or 0),
                    result=result,
                    model=settings.model_name,
                    processing_time_ms=0,
                    prompt_version=LEGACY_PROMPT_VERSION,
                )
            inserted += 1
    finally:
        await redis_client.aclose()
    return inserted, skipped, invalid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="report rows without inserting them"
    )
    args = parser.parse_args()
    inserted, skipped, invalid = asyncio.run(backfill(dry_run=args.dry_run))
    action = "would insert" if args.dry_run else "inserted"
    print(f"{action}={inserted} skipped={skipped} invalid={invalid}")


if __name__ == "__main__":
    main()
