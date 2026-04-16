#!/usr/bin/env python3
"""
Deep career-fit analysis for the top K jobs in the database.

Queries active jobs ordered by their cheap-model fit score (tier2_score),
then runs the expensive LLM (DEEP_ANALYSIS_MODEL) on the top K to produce
a detailed per-job report including strengths, gaps, a recommendation, and
targeted resume tips.

Scores are persisted to jobs.tier3_score / jobs.tier3_explanation unless
--no-persist is passed.

Usage:
    python scripts/score_top_jobs.py
    python scripts/score_top_jobs.py --top-k 20
    python scripts/score_top_jobs.py --min-score 60   # skip jobs below this tier2_score
    python scripts/score_top_jobs.py --no-persist
    python scripts/score_top_jobs.py --output results.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from config.settings import DEEP_ANALYSIS_MODEL, DEEP_ANALYSIS_TOP_K, RESUME_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("score_top_jobs")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run expensive LLM deep-fit analysis on top-K jobs by cheap-model score."
    )
    p.add_argument(
        "--top-k", "-k", type=int, default=DEEP_ANALYSIS_TOP_K,
        help=f"Number of top-scored jobs to analyse (default: {DEEP_ANALYSIS_TOP_K})",
    )
    p.add_argument(
        "--min-score", "-m", type=int, default=70,
        help="Only consider jobs with a cheap-model score >= this value (default: 0)",
    )
    p.add_argument(
        "--no-persist", action="store_true",
        help="Do not write tier3 scores back to the database",
    )
    p.add_argument(
        "--rescore", action="store_true",
        help="Re-run analysis on jobs that already have a tier3 score",
    )
    p.add_argument(
        "--output", metavar="FILE",
        help="Write full results as JSON to this file",
    )
    return p.parse_args()


def load_career_profile() -> str:
    if not RESUME_PATH.exists():
        logger.error(
            f"Career profile not found at {RESUME_PATH}. "
            "Create data/career_profile.md before running this script."
        )
        sys.exit(1)
    text = RESUME_PATH.read_text(encoding="utf-8").strip()
    if not text or "<!-- Fill in your" in text:
        logger.error(
            "data/career_profile.md is still a placeholder. "
            "Fill it in before running this script."
        )
        sys.exit(1)
    return text


def print_results(jobs: list[dict]) -> None:
    if not jobs:
        print("No results.")
        return

    print(f"\n{'='*70}")
    print(f"DEEP ANALYSIS — {len(jobs)} jobs  (model: {DEEP_ANALYSIS_MODEL})")
    print(f"{'='*70}")

    for i, job in enumerate(jobs, 1):
        fit_score = job.get("fit_score", 0)
        cheap_score = job.get("tier2_score", "n/a")
        rec = job.get("recommendation", "")
        rec_label = {
            "apply":               "APPLY",
            "apply_with_caveats":  "APPLY w/ CAVEATS",
            "skip":                "SKIP",
        }.get(rec, rec.upper() if rec else "—")

        print(f"\n{'─'*70}")
        print(f"#{i}  [{rec_label}]  deep={fit_score}/100  cheap={cheap_score}/100")
        print(f"    {job.get('title', '?')} @ {job.get('company_name', '?')}")

        loc = job.get("location", "")
        att = job.get("attendance", "")
        if loc or att:
            print(f"    {loc}  {att}".strip())

        if job.get("salary_min"):
            period   = job.get("salary_period") or "yearly"
            currency = job.get("salary_currency") or "USD"
            hi       = f"–{job['salary_max']:,}" if job.get("salary_max") else ""
            print(f"    Salary: {currency} {job['salary_min']:,}{hi} ({period})")

        if job.get("url"):
            print(f"    {job['url']}")

        if job.get("tier2_explanation"):
            print(f"\n    Cheap-model note: {job['tier2_explanation']}")

        explanation = job.get("explanation", "")
        if explanation:
            print(f"\n    Analysis:\n    {explanation}")

    print(f"\n{'='*70}\n")


def main() -> None:
    args = parse_args()
    career_profile_text = load_career_profile()

    # ── Fetch top-K jobs by cheap-model score ─────────────────────────────
    from db.operations import get_top_scored_jobs
    jobs = get_top_scored_jobs(
        top_k=args.top_k,
        min_score=args.min_score,
        unscored_only=not args.rescore,
    )

    if not jobs:
        logger.error(
            "No scored jobs found in the database. "
            "Run the ingestion pipeline first (scripts/backfill.py or scripts/daily_run.py)."
        )
        sys.exit(1)

    logger.info(
        f"Found {len(jobs)} candidate jobs "
        f"(top cheap-model score: {jobs[0].get('tier2_score')})"
    )

    # ── Run expensive LLM deep analysis ───────────────────────────────────
    from matching.tier3_deep_analysis import analyse_batch
    results = analyse_batch(
        jobs,
        career_profile_text,
        persist=not args.no_persist,
    )

    # ── Output ────────────────────────────────────────────────────────────
    print_results(results)

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        logger.info(f"Results written to {output_path}")


if __name__ == "__main__":
    main()
