#!/usr/bin/env python3
"""
One-time migration: re-score active jobs that already have a t3_score using
the new split qualification/fit prompt.

Targets: status='active' AND t3_score IS NOT NULL
Does NOT run automatically — review the prompt in matching/tier3_deep_analysis.py first.

Usage:
    python scripts/rescore_active_tier3.py --dry-run      # list targeted jobs without scoring
    python scripts/rescore_active_tier3.py --test-one     # score only the first job (verify prompt)
    python scripts/rescore_active_tier3.py                # score all targeted jobs
    python scripts/rescore_active_tier3.py --output results.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from config.settings import DEEP_ANALYSIS_MODEL, RESUME_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rescore_active_tier3")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Re-score active jobs that already have a t3_score using the new "
            "split qualification/fit prompt.  Review the prompt in "
            "matching/tier3_deep_analysis.py before running."
        )
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="List targeted jobs without running the expensive LLM",
    )
    p.add_argument(
        "--test-one", action="store_true",
        help="Score only the first matching job to verify the prompt before a full run",
    )
    p.add_argument(
        "--output", metavar="FILE",
        help="Write full results as JSON to this file",
    )
    return p.parse_args()


def load_career_profile() -> str:
    if not RESUME_PATH.exists():
        logger.error(f"Career profile not found at {RESUME_PATH}.")
        sys.exit(1)
    text = RESUME_PATH.read_text(encoding="utf-8").strip()
    if not text or "<!-- Fill in your" in text:
        logger.error("data/career_profile.md is still a placeholder.")
        sys.exit(1)
    return text


def print_results(results: list[dict], label: str) -> None:
    print(f"\n{'='*70}")
    print(f"{label} — {len(results)} job(s)  (model: {DEEP_ANALYSIS_MODEL})")
    print(f"{'='*70}")
    for i, job in enumerate(results, 1):
        print(
            f"{i:>3}. match={job.get('t3_score'):>5}  "
            f"qual={job.get('t3_qualification'):>3}  "
            f"fit={job.get('t3_fit'):>3}  "
            f"{job.get('title', '?'):<40} @ {job.get('company_name', '?')}"
        )
        if job.get("t3_explanation"):
            print(f"\n{job['t3_explanation']}\n")
    print()


def main() -> None:
    args = parse_args()

    from db.operations import get_active_t3_scored_jobs
    jobs = get_active_t3_scored_jobs()

    if not jobs:
        logger.info("No active jobs with existing t3_score found — nothing to rescore.")
        return

    logger.info(f"Found {len(jobs)} active job(s) with existing t3_score.")

    if args.dry_run:
        print(f"\n{'='*70}")
        print(f"DRY RUN — {len(jobs)} jobs would be re-scored with {DEEP_ANALYSIS_MODEL}")
        print(f"{'='*70}")
        print(f"\nRe-run without --dry-run to execute (--test-one to try just the first).\n")
        return

    career_profile_text = load_career_profile()

    batch = jobs[:1] if args.test_one else jobs
    if args.test_one:
        logger.info(f"--test-one: scoring only '{batch[0].get('title')}' @ '{batch[0].get('company_name')}'")

    from matching.tier3_deep_analysis import analyse_batch
    results = analyse_batch(batch, career_profile_text, persist=True)

    label = "TEST RUN (1 job)" if args.test_one else "RESCORE COMPLETE"
    print_results(results, label)

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        logger.info(f"Results written to {output_path}")


if __name__ == "__main__":
    main()
