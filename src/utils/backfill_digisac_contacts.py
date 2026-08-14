"""Run a complete, validated DigiSac Contacts backfill into PostgreSQL."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict

from src.core.config import settings
from src.core.db import close_database, initialize_database
from src.core.digisac_client import DigisacClientError
from src.core.digisac_contact_backfill import (
    DigisacContactBackfillError,
    run_contact_backfill,
)


logger = logging.getLogger(__name__)


async def backfill(*, per_page: int | None = None) -> dict[str, int]:
    """Acquire and publish a complete Contacts snapshot."""
    await initialize_database()
    try:
        result = await run_contact_backfill(per_page=per_page)
        report = asdict(result)
        logger.info(
            "DigiSac contacts backfill completed: pages=%d acquired=%d "
            "duplicates=%d published=%d",
            report["page_count"],
            report["acquired_count"],
            report["duplicate_count"],
            report["published_count"],
        )
        return report
    finally:
        await close_database()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--per-page",
        type=int,
        default=settings.digisac_contact_backfill_per_page,
        help="requested Contacts page size (default: configured value)",
    )
    args = parser.parse_args()
    try:
        report = asyncio.run(backfill(per_page=args.per_page))
    except (DigisacClientError, DigisacContactBackfillError) as exc:
        logger.error(
            "DigiSac contacts backfill failed: category=%s message=%s",
            getattr(exc, "category", "provider"),
            str(exc),
        )
        raise SystemExit(1) from None
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
