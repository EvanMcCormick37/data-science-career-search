#!/usr/bin/env python3
"""
Semi-automated candidate skill/framework review.

Shows all skills and frameworks that were proposed by the LLM but not yet
accepted into the canonical taxonomy (is_candidate = 1), ordered by how many
jobs reference them.

For each candidate you choose one of:
  p — Promote  (accept into taxonomy; guided menus walk you through the
                domain hierarchy, with "add new" at each level)
  m — Merge    (it's a variant of an existing entry; remap and add alias)
  d — Discard  (noise, sentence fragment, etc.; delete it)
  s — Skip     (review later)
  q — Quit

Similarity detection:
  Before the interactive loop, each candidate is embedded and compared
  against all canonical entries.  The top-K most similar entries are always
  shown so you can decide whether to merge rather than promote.

Usage:
    python scripts/review_candidates.py
    python scripts/review_candidates.py --auto-discard-singles
        # automatically discard candidates that appear in only 1 job
    python scripts/review_candidates.py --top-k 8
        # show the top 8 similar canonical entries (default: 5)
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

DEFAULT_TOP_K = 5

# Sentinel returned by _menu when the user chooses "add new".
_NEW = "__NEW__"


# ── Taxonomy loading ──────────────────────────────────────────────────────────

def _load_taxonomy(kind: str) -> dict:
    """
    Load the canonical taxonomy from the DB as a nested dict.

    Skills:     {domain: {core_competency: set(competency)}}
    Frameworks: {domain: {subdomain: set(service)}}   (service entries may be None)

    Only canonical entries (is_candidate = 0) are included.
    """
    with connection() as conn:
        with conn.cursor() as cur:
            if kind == "skills":
                cur.execute(
                    """
                    SELECT DISTINCT domain, core_competency, competency
                    FROM skills
                    WHERE is_candidate = 0
                    ORDER BY domain, core_competency, competency
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT DISTINCT domain, subdomain, service
                    FROM frameworks
                    WHERE is_candidate = 0
                    ORDER BY domain, subdomain, service
                    """
                )
            rows = cur.fetchall()

    taxonomy: dict = {}
    for domain, level2, level3 in rows:
        if domain not in taxonomy:
            taxonomy[domain] = {}
        if level2 not in taxonomy[domain]:
            taxonomy[domain][level2] = set()
        taxonomy[domain][level2].add(level3)   # level3 may be None / empty
    return taxonomy


# ── Menu helpers ──────────────────────────────────────────────────────────────

def _menu(prompt: str, options: list[str]) -> str:
    """
    Display a numbered list.  The final option is always "[add new]".

    Returns the selected option string, or _NEW if the user chose "[add new]".
    """
    if not options:
        # Nothing to choose from — force "add new".
        print(f"\n  {prompt}  (no existing entries — you will add a new one)")
        return _NEW

    all_opts = list(options) + ["[add new]"]
    print(f"\n  {prompt}")
    for i, opt in enumerate(all_opts, 1):
        print(f"    {i:>2}. {opt}")

    while True:
        raw = input(f"  Choice [1–{len(all_opts)}]: ").strip()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(all_opts) - 1:
                return all_opts[idx]
            if idx == len(all_opts) - 1:
                return _NEW
        except ValueError:
            pass
        print(f"  Please enter a number between 1 and {len(all_opts)}.")


def _input_required(prompt: str) -> str:
    """Read a non-empty string from the user."""
    while True:
        val = input(f"  {prompt}: ").strip()
        if val:
            return val
        print("  Value cannot be empty.")


def _input_optional(prompt: str) -> str | None:
    """Read an optional string; blank → None."""
    val = input(f"  {prompt} (blank to skip): ").strip()
    return val or None


# ── Taxonomy pickers ──────────────────────────────────────────────────────────

def _pick_skill_taxonomy(taxonomy: dict) -> tuple[str, str | None, str | None]:
    """
    Guided cascade: domain → core_competency → competency.

    Choosing "add new" at any level drops into free-text entry for all
    remaining levels.  Returns (domain, core_competency, competency).
    """
    # ── Level 1: domain ───────────────────────────────────────────────────
    domains = sorted(d for d in taxonomy if d)
    choice = _menu("Domain:", domains)
    if choice is _NEW:
        domain          = _input_required("New domain")
        core_competency = _input_optional("New core_competency")
        competency      = _input_optional("New competency") if core_competency else None
        return domain, core_competency, competency
    domain = choice

    # ── Level 2: core_competency ──────────────────────────────────────────
    core_comps = sorted(c for c in taxonomy[domain] if c)
    choice = _menu("Core competency:", core_comps)
    if choice is _NEW:
        core_competency = _input_required("New core_competency")
        competency      = _input_optional("New competency")
        return domain, core_competency, competency
    core_competency = choice

    # ── Level 3: competency ───────────────────────────────────────────────
    competencies = sorted(c for c in taxonomy[domain][core_competency] if c)
    choice = _menu("Competency:", competencies)
    if choice is _NEW:
        competency = _input_required("New competency")
        return domain, core_competency, competency
    return domain, core_competency, choice


def _pick_framework_taxonomy(taxonomy: dict) -> tuple[str, str | None, str | None]:
    """
    Guided cascade: domain → subdomain → service (optional).

    Returns (domain, subdomain, service).  Service may be None.
    """
    # ── Level 1: domain ───────────────────────────────────────────────────
    domains = sorted(d for d in taxonomy if d)
    choice = _menu("Domain:", domains)
    if choice is _NEW:
        domain    = _input_required("New domain")
        subdomain = _input_optional("New subdomain")
        service   = _input_optional("New service") if subdomain else None
        return domain, subdomain, service
    domain = choice

    # ── Level 2: subdomain ────────────────────────────────────────────────
    subdomains = sorted(s for s in taxonomy[domain] if s)
    choice = _menu("Subdomain:", subdomains)
    if choice is _NEW:
        subdomain = _input_required("New subdomain")
        service   = _input_optional("New service")
        return domain, subdomain, service
    subdomain = choice

    # ── Level 3: service (optional) ───────────────────────────────────────
    existing_services = taxonomy[domain][subdomain]
    has_none_entries  = any(not s for s in existing_services)
    named_services    = sorted(s for s in existing_services if s)

    # Offer "(none — no service level)" if entries without a service already exist.
    service_opts = (["(none)"] if has_none_entries else []) + named_services
    choice = _menu("Service (optional):", service_opts)
    if choice is _NEW:
        service = _input_optional("New service (blank for no service level)")
        return domain, subdomain, service
    if choice == "(none)":
        return domain, subdomain, None
    return domain, subdomain, choice


# ── Similarity helpers ────────────────────────────────────────────────────────

def _load_canonical_embeddings(
    kind: str, embedder: Embedder
) -> dict[int, tuple[str, list[float]]]:
    """Embed all canonical (non-candidate) entries of the given kind."""
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
        result[row_id] = (name, embedder.embed_text(name))
    return result


def _find_similar(
    candidate_name: str,
    canonical: dict[int, tuple[str, list[float]]],
    embedder: Embedder,
    top_k: int,
) -> list[tuple[float, int, str]]:
    """Return the top_k most similar canonical entries, sorted by similarity desc."""
    cand_vec = np.array(embedder.embed_text(candidate_name))
    scores = [
        (float(np.dot(cand_vec, np.array(vec))), canon_id, name)
        for canon_id, (name, vec) in canonical.items()
    ]
    scores.sort(reverse=True)
    return scores[:top_k]


# ── Interactive review ────────────────────────────────────────────────────────

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
    taxonomy: dict,
    embedder: Embedder,
    top_k: int,
    auto_discard_singles: bool,
) -> str:
    """Interactive review of a single candidate. Returns the action taken."""
    name    = item["name"]
    count   = item["job_count"]
    item_id = item["skill_id" if kind == "skills" else "framework_id"]

    if auto_discard_singles and count <= 1:
        print(f"  [auto-discard] {name!r} (1 job)")
        discard_skill(item_id) if kind == "skills" else discard_framework(item_id)
        return "auto_discarded"

    similar = _find_similar(name, canonical, embedder, top_k)

    print(f"\n{'─'*60}")
    print(f"  Candidate: {name!r}")
    print(f"  Jobs:      {count}")
    print(f"  Type:      {kind}")
    if similar:
        print(f"  Top {top_k} most similar canonical entries:")
        for sim, sid, sname in similar:
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
            discard_skill(item_id) if kind == "skills" else discard_framework(item_id)
            print(f"  Discarded {name!r}")
            return "discarded"

        if choice == "p":
            print(f"\n  Placing {name!r} into the taxonomy …")
            if kind == "skills":
                domain, core_competency, competency = _pick_skill_taxonomy(taxonomy)
                promote_skill(item_id, domain, core_competency, competency)
            else:
                domain, subdomain, service = _pick_framework_taxonomy(taxonomy)
                promote_framework(item_id, domain, subdomain, service)
            print(f"  Promoted {name!r}")
            return "promoted"

        if choice == "m":
            if similar:
                print("  Suggested merge targets (from similarity search):")
                for sim, sid, sname in similar:
                    print(f"    {sid}: {sname!r}  [{sim:.3f}]")
            target_input = input("  Enter canonical name or ID to merge into: ").strip()
            try:
                canonical_id = int(target_input)
            except ValueError:
                canonical_id = _lookup_id_by_name(kind, target_input)
                if canonical_id is None:
                    print(f"  Not found: {target_input!r}. Try again.")
                    continue
            merge_skill(item_id, canonical_id) if kind == "skills" else merge_framework(item_id, canonical_id)
            print(f"  Merged {name!r} → {canonical_id}")
            return "merged"

        print("  Unrecognised input. Try p / m / d / s / q.")


# ── Entry point ───────────────────────────────────────────────────────────────

def review(kind: str, top_k: int, auto_discard_singles: bool) -> None:
    candidates = get_candidate_skills() if kind == "skills" else get_candidate_frameworks()

    if not candidates:
        print(f"No candidate {kind} to review.")
        return

    print(f"\n=== Reviewing {len(candidates)} candidate {kind} ===")

    embedder  = Embedder()
    canonical = _load_canonical_embeddings(kind, embedder)
    taxonomy  = _load_taxonomy(kind)

    counts: dict[str, int] = {
        "promoted": 0, "merged": 0, "discarded": 0,
        "auto_discarded": 0, "skipped": 0,
    }
    for item in candidates:
        action = _review_item(
            item, kind, canonical, taxonomy, embedder,
            top_k=top_k,
            auto_discard_singles=auto_discard_singles,
        )
        counts[action] = counts.get(action, 0) + 1

    print(f"\n{kind.capitalize()} review complete: {counts}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Review candidate skills/frameworks.")
    p.add_argument("--type", choices=["skills", "frameworks", "both"], default="both")
    p.add_argument(
        "--auto-discard-singles", action="store_true",
        help="Automatically discard candidates referenced by only 1 job",
    )
    p.add_argument(
        "--top-k", type=int, default=DEFAULT_TOP_K,
        help=f"Number of similar canonical entries to display (default: {DEFAULT_TOP_K})",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if args.type in ("skills", "both"):
        review("skills", top_k=args.top_k, auto_discard_singles=args.auto_discard_singles)

    if args.type in ("frameworks", "both"):
        review("frameworks", top_k=args.top_k, auto_discard_singles=args.auto_discard_singles)


if __name__ == "__main__":
    main()
