#!/usr/bin/env python3
"""
Run the full ingestion pipeline on a single ad-hoc SerpAPI query.

Useful for quickly pulling jobs for a specific role or niche search without
having to add it to queries.yaml first.

Usage:
    python scripts/single_query.py "Machine Learning Engineer"
    python scripts/single_query.py "Data Scientist" --location "New York, NY"
    python scripts/single_query.py "Analytics Engineer" -l "Remote" --pages 3
    python scripts/single_query.py "MLOps Engineer" --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from db.operations import get_active_job_count
from pipeline.fetcher import fetch_jobs
from pipeline.orchestrator import Orchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("single_query")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fetch and ingest jobs for a single SerpAPI query.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python scripts/single_query.py "Data Scientist"\n'
            '  python scripts/single_query.py "ML Engineer" -l "San Francisco, CA" -p 3\n'
            '  python scripts/single_query.py "Analytics Engineer" --dry-run\n'
        ),
    )
    p.add_argument(
        "query",
        help='Search query string, e.g. "Data Scientist" or "Machine Learning Engineer"',
    )
    p.add_argument(
        "--location", "-l",
        default=None,
        metavar="LOCATION",
        help='Geographic filter, e.g. "United States" or "Seattle, WA" (optional)',
    )
    p.add_argument(
        "--pages", "-p",
        type=int,
        default=1,
        metavar="N",
        help="Number of SerpAPI result pages to fetch (default: 1, ~10 jobs/page)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and print jobs but do not write anything to the database",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    query_dict: dict = {"q": args.query}
    if args.location:
        query_dict["location"] = args.location

    label = args.query
    if args.location:
        label += f" @ {args.location}"

    logger.info(
        f"Single query: {label!r} | pages={args.pages} | dry_run={args.dry_run}"
    )

    raw_jobs = list(fetch_jobs(queries=[query_dict], max_pages=args.pages))
    logger.info(f"Fetched {len(raw_jobs)} raw results from SerpAPI")

    if not raw_jobs:
        logger.warning("No results returned — check your query string or SerpAPI quota.")
        sys.exit(0)

    if args.dry_run:
        print(f"\n{'='*60}")
        print(f"DRY RUN — {len(raw_jobs)} jobs fetched (nothing written to DB)")
        print(f"{'='*60}")
        for i, job in enumerate(raw_jobs, 1):
            title   = job.get("title", "?")
            company = job.get("company_name", "?")
            loc     = job.get("location", "")
            print(f"  {i:>3}. {title} @ {company}" + (f"  [{loc}]" if loc else ""))
        print()
        return

    before = get_active_job_count()
    orchestrator = Orchestrator()
    stats = orchestrator.process_batch(raw_jobs)
    after = get_active_job_count()

    logger.info(
        f"Done — inserted={stats['inserted']}, "
        f"duplicates={stats['duplicates']}, "
        f"failed={stats['failed']} | "
        f"active jobs in DB: {before} → {after}"
    )


if __name__ == "__main__":
    main()
