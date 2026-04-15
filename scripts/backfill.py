#!/usr/bin/env python3
"""
One-time historical ingestion (backfill mode).

Iterates through every query in queries.yaml, paginating each up to
BACKFILL_MAX_PAGES.  Progress is tracked in data/backfill_state.json so
the process can resume if interrupted — completed queries are skipped.

Usage:
    python scripts/backfill.py
    python scripts/backfill.py --dry-run        # fetch only, no DB writes
    python scripts/backfill.py --reset-state    # clear progress and start over
    python scripts/backfill.py --query "Data Scientist - Seattle"  # single query
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from config.settings import BACKFILL_STATE_PATH
from pipeline.fetcher import fetch_jobs, load_queries
from pipeline.orchestrator import Orchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill historical job listings.")
    p.add_argument("--dry-run",     action="store_true", help="Fetch jobs but skip DB writes")
    p.add_argument("--reset-state", action="store_true", help="Clear backfill progress file and restart")
    p.add_argument("--query",       metavar="NAME",      help="Run a single named query only")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if args.reset_state and BACKFILL_STATE_PATH.exists():
        BACKFILL_STATE_PATH.unlink()
        logger.info("Backfill state cleared.")

    queries = load_queries()
    if args.query:
        queries = [q for q in queries if q.get("name") == args.query]
        if not queries:
            logger.error(f"No query named {args.query!r} found in queries.yaml")
            sys.exit(1)

    logger.info(f"Starting backfill — {len(queries)} queries, dry_run={args.dry_run}")

    orchestrator = None if args.dry_run else Orchestrator()
    total_fetched = 0

    for raw_job in fetch_jobs(mode="backfill", queries=queries):
        total_fetched += 1
        if args.dry_run:
            logger.debug(f"[dry-run] {raw_job.get('title')!r} @ {raw_job.get('company_name')!r}")
            continue
        # Process one at a time so the state file stays current and we can
        # resume mid-query if the process is killed
        orchestrator.process_batch([raw_job])

    if args.dry_run:
        logger.info(f"Dry run complete — fetched {total_fetched} jobs (nothing written)")
    else:
        logger.info(f"Backfill complete — {total_fetched} jobs fetched and processed")


if __name__ == "__main__":
    main()
