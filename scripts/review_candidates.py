#!/usr/bin/env python3
"""
Semi-automated candidate skill/framework review.

Shows all skills and frameworks that were proposed by the LLM but not yet
accepted into the canonical taxonomy (is_candidate = 1), ordered by how many
jobs reference them.

For each candidate you choose one of:
  p — Promote  (accept into taxonomy, assign domain/category)
  m — Merge    (it's a variant of an existing entry; remap and add alias)
  d — Discard  (noise, sentence fragment, etc.; delete it)
  s — Skip     (review later)
  q — Quit

Similarity detection:
  Before the interactive loop, each candidate is embedded and compared
  against all canonical entries.  Likely duplicates (cosine similarity >
  SIM_THRESHOLD) are flagged so you know to merge rather than promote.

Usage:
    python scripts/review_candidates.py
    python scripts/review_candidates.py --auto-discard-singles
        # automatically discard candidates that appear in only 1 job
    python scripts/review_candidates.py --sim-threshold 0.90
        # tighten the similarity threshold for flagging duplicates
    python scripts/review_candidates.py --type skills      # skills only
    python scripts/review_candidates.py --type frameworks  # frameworks only
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import numpy as np

from db.connection import connection
from db.operations import (
    discard_framework,
    discard_skill,
    get_candidate_frameworks,
    get_candidate_skills,
    merge_framework,
    merge_skill,
    promote_framework,
    promote_skill,
)
from pipeline.embedder import Embedder

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger("review_candidates")

DEFAULT_SIM_THRESHOLD = 0.85


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Review candidate skills/frameworks.")
    p.add_argument("--type", choices=["skills", "frameworks", "both"], default="both")
    p.add_argument("--auto-discard-singles", action="store_true",
                   help="Automatically discard candidates referenced by only 1 job")
    p.add_argument("--sim-threshold", type=float, default=DEFAULT_SIM_THRESHOLD,
                   help="Cosine similarity threshold for flagging duplicates (default: 0.85)")
    return p.parse_args()


# ── Similarity helpers ────────────────────────────────────────────────────

def _load_canonical_embeddings(kind: str, embedder: Embedder) -> dict[int, tuple[str, list[float]]]:
    """Load and embed all canonical (non-candidate) entries of the given kind."""
    logger.info(f"Embedding canonical {kind} for similarity detection …")
    with connection() as conn:
        with conn.cursor() as cur:
            if kind == "skills":
                cur.execute("SELECT skill_id, name FROM skills WHERE is_candidate = 0")
            else:
                cur.execute("SELECT framework_id, name FROM frameworks WHERE is_candidate = 0")
            rows = cur.fetchall()

    result = {}
    for row_id, name in rows:
        vec = embedder.embed_text(name)
        result[row_id] = (name, vec)
    return result


def _find_similar(
    candidate_name: str,
    canonical: dict[int, tuple[str, list[float]]],
    embedder: Embedder,
    threshold: float,
) -> list[tuple[float, int, str]]:
    """Return [(similarity, id, canonical_name)] for entries above threshold."""
    cand_vec = np.array(embedder.embed_text(candidate_name))
    matches = []
    for canon_id, (canon_name, canon_vec) in canonical.items():
        sim = float(np.dot(cand_vec, np.array(canon_vec)))
        if sim >= threshold:
            matches.append((sim, canon_id, canon_name))
    return sorted(matches, reverse=True)


# ── Interactive review ────────────────────────────────────────────────────

def _lookup_id_by_name(kind: str, name: str) -> int | None:
    with connection() as conn:
        with conn.cursor() as cur:
            if kind == "skills":
                cur.execute("SELECT skill_id FROM skills WHERE name = %s", (name,))
            else:
                cur.execute("SELECT framework_id FROM frameworks WHERE name = %s", (name,))
            row = cur.fetchone()
    return row[0] if row else None


def _review_item(
    item: dict,
    kind: str,
    canonical: dict[int, tuple[str, list[float]]],
    embedder: Embedder,
    threshold: float,
    auto_discard_singles: bool,
) -> str:
    """Interactive review of a single candidate. Returns action taken."""
    name      = item["name"]
    count     = item["job_count"]
    item_id   = item["skill_id" if kind == "skills" else "framework_id"]

    if auto_discard_singles and count <= 1:
        print(f"  [auto-discard] {name!r} (1 job)")
        if kind == "skills":
            discard_skill(item_id)
        else:
            discard_framework(item_id)
        return "auto_discarded"

    # Find similar canonical entries
    similar = _find_similar(name, canonical, embedder, threshold)

    print(f"\n{'─'*60}")
    print(f"  Name:     {name!r}")
    print(f"  Jobs:     {count}")
    print(f"  Type:     {kind}")
    if similar:
        print(f"  Similar canonical entries:")
        for sim, sid, sname in similar[:5]:
            print(f"    [{sim:.3f}] ({sid}) {sname!r}")

    while True:
        choice = input(
            "  Action — [p]romote  [m]erge  [d]iscard  [s]kip  [q]uit: "
        ).strip().lower()

        if choice == "q":
            print("Quitting review.")
            sys.exit(0)

        if choice == "s":
            return "skipped"

        if choice == "d":
            if kind == "skills":
                discard_skill(item_id)
            else:
                discard_framework(item_id)
            print(f"  Discarded {name!r}")
            return "discarded"

        if choice == "p":
            if kind == "skills":
                domain          = input("    domain: ").strip()
                core_competency = input("    core_competency (enter to skip): ").strip() or None
                competency      = input("    competency (enter to skip): ").strip() or None
                promote_skill(item_id, domain, core_competency, competency)
            else:
                domain    = input("    domain: ").strip()
                subdomain = input("    subdomain (enter to skip): ").strip() or None
                service   = input("    service (enter to skip): ").strip() or None
                promote_framework(item_id, domain, subdomain, service)
            print(f"  Promoted {name!r}")
            return "promoted"

        if choice == "m":
            if similar:
                print("  Suggested targets (from similarity search):")
                for sim, sid, sname in similar[:5]:
                    print(f"    {sid}: {sname!r} [{sim:.3f}]")
            target_input = input("  Enter canonical name or ID to merge into: ").strip()
            try:
                canonical_id = int(target_input)
            except ValueError:
                canonical_id = _lookup_id_by_name(kind, target_input)
                if canonical_id is None:
                    print(f"  Not found: {target_input!r}. Try again.")
                    continue
            if kind == "skills":
                merge_skill(item_id, canonical_id)
            else:
                merge_framework(item_id, canonical_id)
            print(f"  Merged {name!r} → {canonical_id}")
            return "merged"

        print("  Unrecognised input. Try p / m / d / s / q.")


def review(kind: str, threshold: float, auto_discard_singles: bool) -> None:
    if kind == "skills":
        candidates = get_candidate_skills()
    else:
        candidates = get_candidate_frameworks()

    if not candidates:
        print(f"No candidate {kind} to review.")
        return

    print(f"\n=== Reviewing {len(candidates)} candidate {kind} ===")

    embedder = Embedder()
    canonical = _load_canonical_embeddings(kind, embedder)

    counts = {"promoted": 0, "merged": 0, "discarded": 0,
              "auto_discarded": 0, "skipped": 0}

    for item in candidates:
        action = _review_item(item, kind, canonical, embedder, threshold, auto_discard_singles)
        counts[action] = counts.get(action, 0) + 1

    print(f"\n{kind.capitalize()} review complete: {counts}")


def main() -> None:
    args = parse_args()

    if args.type in ("skills", "both"):
        review("skills", args.sim_threshold, args.auto_discard_singles)

    if args.type in ("frameworks", "both"):
        review("frameworks", args.sim_threshold, args.auto_discard_singles)


if __name__ == "__main__":
    main()
