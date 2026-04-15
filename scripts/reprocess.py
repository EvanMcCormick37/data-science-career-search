#!/usr/bin/env python3
"""
Re-run LLM extraction on stored serp_api_json without re-fetching from SerpAPI.

Use this when:
  - The extraction prompt has improved
  - A model change produced better structured output
  - A batch of jobs failed extraction on the first run

Targets jobs with status='extraction_failed' by default.  Pass --all to
reprocess every active job in the database (slow, costs LLM credits).

Usage:
    python scripts/reprocess.py                  # all extraction_failed jobs
    python scripts/reprocess.py --job-ids 1 2 3  # specific job IDs
    python scripts/reprocess.py --all             # all active jobs (expensive)
    python scripts/reprocess.py --dry-run         # extract but don't write to DB
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from pipeline.orchestrator import Orchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("reprocess")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reprocess stored job JSON through the extraction pipeline.")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--job-ids", nargs="+", type=int, metavar="ID",
                       help="Reprocess specific job IDs")
    group.add_argument("--all", action="store_true",
                       help="Reprocess ALL jobs (active + failed). Expensive.")
    p.add_argument("--dry-run", action="store_true",
                   help="Extract but do not write to DB")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if args.all:
        from db.connection import connection
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT job_id FROM jobs WHERE serp_api_json IS NOT NULL"
                )
                job_ids = [row[0] for row in cur.fetchall()]
        logger.info(f"Reprocessing ALL {len(job_ids)} jobs with stored JSON")
    elif args.job_ids:
        job_ids = args.job_ids
        logger.info(f"Reprocessing {len(job_ids)} specified job(s): {job_ids}")
    else:
        job_ids = None  # orchestrator will load all extraction_failed
        logger.info("Reprocessing all extraction_failed jobs")

    orchestrator = Orchestrator()
    stats = orchestrator.reprocess(job_ids)

    logger.info(
        f"Reprocess complete — "
        f"reprocessed={stats['reprocessed']}, failed={stats['failed']}"
    )


if __name__ == "__main__":
    main()
