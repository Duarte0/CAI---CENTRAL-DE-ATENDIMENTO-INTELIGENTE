"""Manually reconcile complete DigiSac and Acessórias directory views.

The command is dry-run by default.  Use ``--apply`` only after reviewing the
sanitized report from a successful dry-run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any

from src.core.db import close_database, initialize_database
from src.core.digisac_acessorias_reconciliation import (
    ManualReconciliationResult,
    run_manual_reconciliation,
)

logger = logging.getLogger(__name__)


async def reconcile(*, apply: bool = False, per_page: int | None = None) -> dict[str, Any]:
    """Initialize/verify PostgreSQL, run once, and always close the pool."""
    await initialize_database()
    try:
        result: ManualReconciliationResult = await run_manual_reconciliation(
            apply=apply,
            per_page=per_page,
        )
        return result.as_dict()
    finally:
        await close_database()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="publish the reviewed two-source delta; dry-run is the default",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="explicitly select the non-destructive default mode",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=None,
        help="DigiSac Contacts page size (default: configured value)",
    )
    args = parser.parse_args()
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")
    try:
        report = asyncio.run(reconcile(apply=args.apply, per_page=args.per_page))
    except Exception:
        logger.error("Manual DigiSac/Acessórias reconciliation could not start")
        raise SystemExit(1) from None
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if report.get("status") not in {"dry_run", "succeeded"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
