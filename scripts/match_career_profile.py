#!/usr/bin/env python3
"""
Run the 3-tier career_profile matching pipeline and print a ranked shortlist.

Reads your career_profile from data/career_profile.md, embeds it, and runs:
  Tier 1 — pgvector cosine similarity (top 100 candidates)
  Tier 2 — cheap LLM relevance scoring (top 15 after scoring)
  Tier 3 — Claude deep analysis (top 15 → detailed fit reports)

Usage:
    python scripts/match_career_profile.py
    python scripts/match_career_profile.py --tier 1          # stop after Tier 1
    python scripts/match_career_profile.py --tier 2          # stop after Tier 2
    python scripts/match_career_profile.py --tier 3          # full pipeline (default)
    python scripts/match_career_profile.py --top-n 20        # show top 20 in Tier 1 output
    python scripts/match_career_profile.py --no-persist      # don't write scores to DB
    python scripts/match_career_profile.py --output results.json   # write JSON to file
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from config.settings import RESUME_PATH, TIER1_CANDIDATES, TIER2_TOP_N
from pipeline.embedder import Embedder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("match_career_profile")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Match career_profile against job database.")
    p.add_argument("--tier", "-t", type=int, default=3, choices=[1, 2, 3],
                   help="Stop after this tier (default: 3)")
    p.add_argument("--top-k", "-k", type=int, default=None,
                   help="Override number of Tier 1 candidates (default: TIER1_CANDIDATES from .env)")
    p.add_argument("--no-persist", action="store_true",
                   help="Do not write scores back to the DB")
    p.add_argument("--output",     metavar="FILE",
                   help="Write full results to a JSON file")
    return p.parse_args()


def load_career_profile() -> str:
    if not RESUME_PATH.exists():
        logger.error(f"Resume not found at {RESUME_PATH}. Create data/career_profile.md first.")
        sys.exit(1)
    text = RESUME_PATH.read_text(encoding="utf-8").strip()
    if not text or text.startswith("# Resume\n\n<!--"):
        logger.error("data/career_profile.md is still a placeholder. Fill it in before running matching.")
        sys.exit(1)
    return text


def parse_career_profile_for_embedding(career_profile_text: str) -> dict:
    """
    Extract structured fields from career_profile.md for the embedding composition string.
    Falls back gracefully if sections are missing — the full text is always used
    for LLM prompts regardless of this parsing.
    """
    import re
    sections: dict[str, list[str]] = {}
    current = None
    for line in career_profile_text.splitlines():
        heading = re.match(r"^#{1,3}\s+(.+)", line)
        if heading:
            current = heading.group(1).strip().lower()
            sections[current] = []
        elif current is not None:
            stripped = line.strip()
            if stripped and not stripped.startswith("<!--"):
                sections[current].append(stripped)

    def section_text(*keys: str) -> str:
        for k in keys:
            for section_key, lines in sections.items():
                if k in section_key:
                    return " ".join(lines)
        return ""

    def section_list(*keys: str) -> list[str]:
        text = section_text(*keys)
        # Split on bullets, commas, or newlines
        items = re.split(r"[,\n•\-\*]+", text)
        return [i.strip() for i in items if i.strip()]

    return {
        "target_role":            section_text("target", "objective", "role"),
        "qualifications_summary": section_text("summary", "qualif", "profile"),
        "experience_summary":     section_text("experience", "work", "employ"),
        "skills":                 section_list("skill"),
        "frameworks":             section_list("framework", "tool", "technolog"),
    }


def print_tier1(jobs: list[dict], top_k: int) -> None:
    print(f"\n{'='*70}")
    print(f"TIER 1 — Top {min(top_k, len(jobs))} by vector similarity")
    print(f"{'='*70}")
    for i, job in enumerate(jobs[:top_k], 1):
        sim = job.get("cosine_similarity", 0)
        print(
            f"{i:>3}. [{sim:.3f}] {job.get('title', '?'):<40} "
            f"@ {job.get('company_name', '?')}"
        )
        if job.get("location") or job.get("attendance"):
            loc  = job.get("location", "")
            att  = job.get("attendance", "")
            print(f"       {loc}  {att}")


def print_tier2(jobs: list[dict]) -> None:
    print(f"\n{'='*70}")
    print(f"TIER 2 — Scored by {len(jobs)} candidates (cheap LLM)")
    print(f"{'='*70}")
    for i, job in enumerate(jobs, 1):
        score = job.get("tier2_score", 0)
        print(
            f"{i:>3}. [score={score:>3}] {job.get('title', '?'):<40} "
            f"@ {job.get('company_name', '?')}"
        )
        if job.get("tier2_explanation"):
            print(f"       {job['tier2_explanation']}")


def print_tier3(jobs: list[dict]) -> None:
    print(f"\n{'='*70}")
    print(f"TIER 3 — Deep Analysis ({len(jobs)} jobs)")
    print(f"{'='*70}")
    for i, job in enumerate(jobs, 1):
        score  = job.get("fit_score", 0)
        rec    = job.get("recommendation", "")
        rec_label = {"apply": "✓ APPLY", "apply_with_caveats": "~ APPLY w/ CAVEATS", "skip": "✗ SKIP"}.get(rec, rec)
        print(f"\n{'─'*70}")
        print(f"#{i}  {rec_label}  [fit={score}/100]")
        print(f"    {job.get('title', '?')} @ {job.get('company_name', '?')}")
        if job.get("location"):
            print(f"    {job['location']}  {job.get('attendance', '')}")
        if job.get("url"):
            print(f"    {job['url']}")

        if job.get("strengths"):
            print("\n    STRENGTHS:")
            for s in job["strengths"]:
                print(f"      + {s}")

        if job.get("gaps"):
            print("\n    GAPS:")
            for g in job["gaps"]:
                print(f"      - {g}")

        if job.get("career_profile_tips"):
            print("\n    RESUME TIPS:")
            for t in job["career_profile_tips"]:
                print(f"      → {t}")


def main() -> None:
    args = parse_args()
    career_profile_text = load_career_profile()
    career_profile_dict = parse_career_profile_for_embedding(career_profile_text)
    persist     = not args.no_persist
    tier1_limit = args.top_k or TIER1_CANDIDATES

    # ── Tier 1 ────────────────────────────────────────────────────────────
    logger.info("Tier 1: embedding career_profile and searching …")
    embedder  = Embedder()
    embedding = embedder.embed_career_profile(career_profile_dict)

    from matching.tier1_vector import search
    tier1_results = search(embedding, limit=tier1_limit)

    if not tier1_results:
        logger.error("No jobs found. Has the database been populated? Run scripts/backfill.py first.")
        sys.exit(1)

    print_tier1(tier1_results, top_k=min(20, len(tier1_results)))

    if args.tier == 1:
        if args.output:
            Path(args.output).write_text(json.dumps(tier1_results, indent=2, default=str))
            logger.info(f"Results written to {args.output}")
        return

    # ── Tier 2 ────────────────────────────────────────────────────────────
    from matching.tier2_cheap_llm import score_batch
    tier2_results = score_batch(tier1_results, career_profile_text, persist=persist, top_k=TIER2_TOP_N)
    print_tier2(tier2_results)

    if args.tier == 2:
        if args.output:
            Path(args.output).write_text(json.dumps(tier2_results, indent=2, default=str))
            logger.info(f"Results written to {args.output}")
        return

    # ── Tier 3 ────────────────────────────────────────────────────────────
    from matching.tier3_deep_analysis import analyse_batch
    tier3_results = analyse_batch(tier2_results, career_profile_text, persist=persist)
    print_tier3(tier3_results)

    if args.output:
        Path(args.output).write_text(json.dumps(tier3_results, indent=2, default=str))
        logger.info(f"Results written to {args.output}")


if __name__ == "__main__":
    main()
