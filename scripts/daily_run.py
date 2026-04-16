#!/usr/bin/env python3
"""
Daily ingestion pipeline entry point — fetches jobs posted in the past 24 hours
(chips=date_posted:today) across all queries in queries.yaml.

Intended to be called by cron or a task scheduler once per day.

Steps:
  1. Mark jobs older than JOB_EXPIRY_DAYS as expired.
  2. Fetch new listings (first DAILY_MAX_PAGES pages per query, today only).
  3. Run full ingestion pipeline on fetched jobs.
  4. Log a summary.

Usage:
    python scripts/daily_run.py
    python scripts/daily_run.py --dry-run   # fetch only, no DB writes
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from config.settings import JOB_EXPIRY_DAYS
from db.operations import expire_old_jobs, get_active_job_count
from pipeline.fetcher import fetch_jobs
from pipeline.orchestrator import Orchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("daily_run")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Daily job ingestion pipeline.")
    p.add_argument("--dry-run", action="store_true", help="Fetch jobs but skip all DB writes")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    logger.info(f"=== Daily run started (dry_run={args.dry_run}) ===")

    # ── Step 1: expire old listings ───────────────────────────────────────
    if not args.dry_run:
        expired = expire_old_jobs(JOB_EXPIRY_DAYS)
        logger.info(f"Expired {expired} old listings (>{JOB_EXPIRY_DAYS} days old)")
    else:
        logger.info("[dry-run] Skipping expiry step")

    # ── Step 2: fetch + ingest new listings ───────────────────────────────
    raw_jobs = list(fetch_jobs(mode="daily"))
    logger.info(f"Fetched {len(raw_jobs)} raw jobs from SerpAPI")

    if args.dry_run:
        logger.info("[dry-run] Skipping ingestion — nothing written to DB")
        return

    orchestrator = Orchestrator()
    stats = orchestrator.process_batch(raw_jobs)

    active_count = get_active_job_count()
    logger.info(
        f"=== Daily run complete === "
        f"inserted={stats['inserted']}, "
        f"duplicates={stats['duplicates']}, "
        f"failed={stats['failed']}, "
        f"total_active={active_count}"
    )


if __name__ == "__main__":
    main()
