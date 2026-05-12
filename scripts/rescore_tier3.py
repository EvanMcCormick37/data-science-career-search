#!/usr/bin/env python3
"""
Rescore all jobs that already have a T3 score using the current scoring prompt.

Useful after changes to the T3 prompt or scoring formula — re-runs deep analysis
on every job with an existing t3_score (active, expired, bad_fit, applied, etc.)
and overwrites t3_score, t3_qualification, t3_fit, and t3_explanation in-place.

Usage:
    python scripts/rescore_tier3.py                  # rescore all, with confirmation
    python scripts/rescore_tier3.py --yes             # skip confirmation prompt
    python scripts/rescore_tier3.py --no-persist      # dry run (no DB writes)
    python scripts/rescore_tier3.py --status active   # limit to one or more statuses
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from db.jobs import get_all_t3_scored_jobs
from matching.career_profile import load as load_career_profile
from matching.tier3_deep_analysis import analyse_batch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rescore_tier3")

VALID_STATUSES = {"active", "expired", "closed", "bad_listing", "bad_fit", "applied"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Rescore all T3-scored jobs.")
    p.add_argument(
        "--status", "-s",
        nargs="+",
        metavar="STATUS",
        choices=sorted(VALID_STATUSES),
        help="Limit rescoring to specific job statuses (default: all)",
    )
    p.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )
    p.add_argument(
        "--no-persist",
        action="store_true",
        help="Run analysis but do not write results to the DB",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    career_profile = load_career_profile()
    if career_profile is None:
        logger.error("Career profile not found. Create data/career_profile.md first.")
        sys.exit(1)

    jobs = get_all_t3_scored_jobs()

    if args.status:
        filter_set = set(args.status)
        jobs = [j for j in jobs if j.get("status") in filter_set]

    if not jobs:
        logger.info("No T3-scored jobs found matching the given criteria. Nothing to do.")
        return

    status_counts: dict[str, int] = {}
    for j in jobs:
        s = j.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    print(f"\nJobs to rescore: {len(jobs)}")
    for status, count in sorted(status_counts.items()):
        print(f"  {status:<20} {count}")
    print()

    if not args.yes and not args.no_persist:
        answer = input("Proceed with rescoring? This will call the LLM for each job. [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return

    results = analyse_batch(jobs, career_profile, persist=not args.no_persist)

    print(f"\nRescoring complete. {len(results)} jobs processed.")
    if args.no_persist:
        print("(--no-persist: no changes written to DB)")


if __name__ == "__main__":
    main()
