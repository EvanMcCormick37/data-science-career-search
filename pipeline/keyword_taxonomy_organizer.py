"""
Keyword taxonomy organizer.

Automates the first pass of candidate skill/framework review:
  - Queries candidates that have accumulated enough job references (CANDIDATE_MIN_JOBS)
  - Embeds all candidates + canonicals and orders candidates most-similar-first
  - Per candidate, calls a cheap LLM with top-k nearest neighbors as context
  - Writes a human-reviewable JSON plan to PENDING_TAXONOMY_PATH

The plan is not applied automatically. Run scripts/apply_taxonomy_changes.py
after reviewing data/pending_taxonomy_changes.json.
"""
from __future__ import annotations

import json
import logging
import re

import numpy as np
from tqdm import tqdm

from config.settings import (
    CANDIDATE_MIN_JOBS,
    CANDIDATE_REVIEW_MODEL,
    CANDIDATE_REVIEW_TOP_K,
    PENDING_TAXONOMY_PATH,
)
from db.operations import (
    get_all_canonical_frameworks,
    get_all_canonical_skills,
    get_candidate_frameworks_above_threshold,
    get_candidate_skills_above_threshold,
)
from llm.client import complete
from pipeline.embedder import Embedder

logger = logging.getLogger(__name__)

_SKILL_DEF = (
    "A SKILL is a competency or ability without a proper name — it describes what someone "
    "can do, not a specific tool. Examples: \"Predictive Modeling\", \"Data Cleaning\", "
    "\"System Design\". NOT skills: \"Python\", \"TensorFlow\", \"AWS\" (those are frameworks)."
)

_FRAMEWORK_DEF = (
    "A FRAMEWORK is a tool, language, library, service, platform, or software with a proper "
    "name. Examples: \"Python\", \"TensorFlow\", \"AWS\", \"PostgreSQL\". NOT frameworks: "
    "\"Predictive Modeling\", \"Data Cleaning\" (those are skills)."
)

_SYSTEM_TMPL = """You are consolidating a {kind} taxonomy for a job search system.

{kind_def}

For the candidate below, output EXACTLY one of:
  PROMOTE — genuinely new entry, keep as its own {kind}
  MERGE <id> — synonym or near-identical rephrasing of an existing entry; provide the integer id
  DELETE — noise, off-topic, too vague, sentence fragment, or not a valid {kind}

Rules:
  - MERGE only for true synonyms or near-identical rephrasings (e.g. "sklearn" → "scikit-learn")
  - DELETE for irrelevant terms, sentence fragments, overly broad non-{kind} concepts
  - PROMOTE everything else, even if similar in domain to existing entries
  - Respond with EXACTLY one line. No explanation."""

_USER_TMPL = """Candidate {kind}: "{name}" (appears in {job_count} job(s))

Top {k} most similar existing entries:
{neighbors}"""


def _embed_pool(
    entries: list[dict], id_key: str, embedder: Embedder
) -> dict[int, np.ndarray]:
    """Embed a list of {id_key, name} dicts. Returns {id: vector}."""
    result = {}
    for entry in entries:
        vec = np.array(embedder.embed_text(entry["name"]))
        result[entry[id_key]] = vec
    return result


def _top_k_neighbors(
    candidate_vec: np.ndarray,
    pool: dict[int, tuple[str, np.ndarray, str]],
    k: int,
    exclude_id: int,
) -> list[tuple[float, int, str, str]]:
    """
    Return top-k (score, id, name, label) from pool, excluding exclude_id.
    pool values are (name, vector, label) where label is 'canonical' or 'candidate'.
    """
    scores = []
    for entry_id, (name, vec, label) in pool.items():
        if entry_id == exclude_id:
            continue
        sim = float(np.dot(candidate_vec, vec))
        scores.append((sim, entry_id, name, label))
    scores.sort(reverse=True)
    return scores[:k]


def _parse_response(raw: str) -> tuple[str, int | None]:
    """
    Parse LLM response line into (action, target_id).
    action is 'promote', 'merge', or 'delete'.
    target_id is set only for 'merge'.
    """
    line = raw.strip().upper()
    if line.startswith("PROMOTE"):
        return "promote", None
    if line.startswith("DELETE"):
        return "delete", None
    if line.startswith("MERGE"):
        m = re.search(r"\d+", line)
        if m:
            return "merge", int(m.group())
        logger.warning(f"MERGE response missing id: {raw!r} — defaulting to PROMOTE")
        return "promote", None
    logger.warning(f"Unrecognised LLM response: {raw!r} — defaulting to PROMOTE")
    return "promote", None


def _process_kind(
    kind: str,
    candidates: list[dict],
    canonicals: list[dict],
    id_key: str,
    embedder: Embedder,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Run the LLM review pass for one kind ('skill' or 'framework').
    Returns (promote_list, merge_list, delete_list).
    """
    kind_def = _SKILL_DEF if kind == "skill" else _FRAMEWORK_DEF

    logger.info(f"Embedding {len(canonicals)} canonical {kind}s …")
    canonical_vecs = _embed_pool(canonicals, id_key, embedder)

    logger.info(f"Embedding {len(candidates)} candidate {kind}s …")
    candidate_vecs = _embed_pool(candidates, id_key, embedder)

    # Build unified pool: canonicals + candidates (for neighbor lookup)
    pool: dict[int, tuple[str, np.ndarray, str]] = {}
    canonical_ids = {e[id_key] for e in canonicals}
    for entry in canonicals:
        pool[entry[id_key]] = (entry["name"], canonical_vecs[entry[id_key]], "canonical")
    for entry in candidates:
        pool[entry[id_key]] = (entry["name"], candidate_vecs[entry[id_key]], "candidate")

    # Sort candidates by max similarity to nearest neighbor (most-similar first)
    def _nearest_sim(entry: dict) -> float:
        vec = candidate_vecs[entry[id_key]]
        neighbors = _top_k_neighbors(vec, pool, k=1, exclude_id=entry[id_key])
        return neighbors[0][0] if neighbors else 0.0

    sorted_candidates = sorted(candidates, key=_nearest_sim, reverse=True)

    promote_list: list[dict] = []
    merge_list:   list[dict] = []
    delete_list:  list[dict] = []

    for entry in tqdm(sorted_candidates, desc=f"Reviewing {kind}s", unit=kind):
        entry_id   = entry[id_key]
        name       = entry["name"]
        job_count  = entry["job_count"]
        cand_vec   = candidate_vecs[entry_id]

        neighbors = _top_k_neighbors(cand_vec, pool, k=CANDIDATE_REVIEW_TOP_K, exclude_id=entry_id)
        neighbor_lines = "\n".join(
            f"  [{sim:.3f}] (id={nid}) {nname!r} [{label}]"
            for sim, nid, nname, label in neighbors
        )

        system_msg = _SYSTEM_TMPL.format(kind=kind, kind_def=kind_def)
        user_msg   = _USER_TMPL.format(
            kind=kind, name=name, job_count=job_count,
            k=len(neighbors), neighbors=neighbor_lines,
        )
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": user_msg},
        ]

        try:
            raw = complete(CANDIDATE_REVIEW_MODEL, messages, max_tokens=16)
            action, target_id = _parse_response(raw)
        except Exception as exc:
            logger.warning(f"LLM call failed for {name!r}: {exc} — skipping")
            continue

        if action == "promote":
            promote_list.append({"id": entry_id, "name": name, "kind": kind})
        elif action == "merge" and target_id is not None:
            # Resolve merge target name and kind
            if target_id in pool:
                target_name = pool[target_id][0]
                target_kind = "skill" if target_id in canonical_ids else kind
            else:
                target_name = str(target_id)
                target_kind = kind
            merge_list.append({
                "id": entry_id, "name": name, "kind": kind,
                "into_id": target_id, "into_name": target_name, "into_kind": target_kind,
            })
        elif action == "delete":
            delete_list.append({"id": entry_id, "name": name, "kind": kind})

    return promote_list, merge_list, delete_list


def _resolve_merge_chains(
    promote_list: list[dict],
    merge_list: list[dict],
    delete_list: list[dict],
) -> list[dict]:
    """
    Clean up the raw merge list before writing the plan:

      1. Collapse chains — A→B→C becomes A→C so every merge points directly
         at a terminal (canonical or promoted) entry.
      2. Detect cycles — all participants are demoted to PROMOTE so no entry
         disappears without its references being preserved.
      3. Skip dead-end chains — if the terminal is being deleted, promote the
         source instead of merging into a soon-to-be-absent target.

    Mutates promote_list in place. Returns the cleaned merge_list.
    """
    delete_ids  = {e["id"] for e in delete_list}
    merge_by_id = {e["id"]: e for e in merge_list}

    # --- detect all nodes that are part of a cycle ---
    cycle_nodes: set[int] = set()
    processed:   set[int] = set()
    for start_id in list(merge_by_id):
        if start_id in processed:
            continue
        path:     list[int] = []
        path_set: set[int]  = set()
        node_id = start_id
        while node_id in merge_by_id and node_id not in processed:
            if node_id in path_set:
                cycle_nodes.update(path[path.index(node_id):])
                break
            path.append(node_id)
            path_set.add(node_id)
            node_id = merge_by_id[node_id]["into_id"]
        processed.update(path)

    if cycle_nodes:
        names = [merge_by_id[cid]["name"] for cid in cycle_nodes if cid in merge_by_id]
        logger.warning(f"Circular merge dependencies detected — promoting instead: {names}")

    def _follow(start_id: int) -> tuple[int, str, str] | None:
        """Walk the chain from start_id to its terminal target.
        Returns (final_id, final_name, final_kind) or None if terminal is deleted."""
        node_id = start_id
        visited: set[int] = {node_id}
        while True:
            entry    = merge_by_id[node_id]
            tgt_id   = entry["into_id"]
            tgt_name = entry["into_name"]
            tgt_kind = entry["into_kind"]
            if tgt_id in delete_ids:
                return None
            # Stop at: canonical, cycle node, or already-visited (shouldn't happen
            # after cycle detection, but guards against unexpected shapes)
            if tgt_id not in merge_by_id or tgt_id in cycle_nodes or tgt_id in visited:
                return tgt_id, tgt_name, tgt_kind
            visited.add(tgt_id)
            node_id = tgt_id

    resolved: list[dict] = []
    for entry in merge_list:
        src_id = entry["id"]

        if src_id in cycle_nodes:
            promote_list.append({"id": src_id, "name": entry["name"], "kind": entry["kind"]})
            continue

        terminal = _follow(src_id)
        if terminal is None:
            logger.warning(
                f"Merge chain for {entry['name']!r} (id={src_id}) ends at a deleted entry "
                "— promoting instead"
            )
            promote_list.append({"id": src_id, "name": entry["name"], "kind": entry["kind"]})
            continue

        final_id, final_name, final_kind = terminal
        resolved.append({**entry, "into_id": final_id, "into_name": final_name, "into_kind": final_kind})

    return resolved


class KeywordTaxonomyOrganizer:
    def __init__(self) -> None:
        self._embedder = Embedder()

    def consolidate_candidates(self) -> None:
        """
        Run the LLM candidate review pass and write a pending changes file.

        Skips with a warning if a pending file already exists (apply or remove
        it before running again). Does nothing if no candidates meet the threshold.
        """
        if PENDING_TAXONOMY_PATH.exists():
            logger.warning(
                f"Pending taxonomy changes already exist at {PENDING_TAXONOMY_PATH}. "
                "Apply or remove them before running consolidation again."
            )
            return

        skill_candidates     = get_candidate_skills_above_threshold(CANDIDATE_MIN_JOBS)
        framework_candidates = get_candidate_frameworks_above_threshold(CANDIDATE_MIN_JOBS)

        if not skill_candidates and not framework_candidates:
            logger.info(
                f"No candidates with >= {CANDIDATE_MIN_JOBS} job references. Nothing to consolidate."
            )
            return

        logger.info(
            f"Consolidating {len(skill_candidates)} skill candidates, "
            f"{len(framework_candidates)} framework candidates …"
        )

        canonical_skills     = get_all_canonical_skills()
        canonical_frameworks = get_all_canonical_frameworks()

        promote_list: list[dict] = []
        merge_list:   list[dict] = []
        delete_list:  list[dict] = []

        if skill_candidates:
            p, m, d = _process_kind(
                "skill", skill_candidates, canonical_skills, "skill_id", self._embedder
            )
            promote_list.extend(p)
            merge_list.extend(m)
            delete_list.extend(d)

        if framework_candidates:
            p, m, d = _process_kind(
                "framework", framework_candidates, canonical_frameworks, "framework_id", self._embedder
            )
            promote_list.extend(p)
            merge_list.extend(m)
            delete_list.extend(d)

        merge_list = _resolve_merge_chains(promote_list, merge_list, delete_list)
        plan = {"promote": promote_list, "merge": merge_list, "delete": delete_list}
        PENDING_TAXONOMY_PATH.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        logger.info(
            f"Wrote taxonomy plan to {PENDING_TAXONOMY_PATH} — "
            f"promote={len(promote_list)}, merge={len(merge_list)}, delete={len(delete_list)}. "
            "Review and run scripts/apply_taxonomy_changes.py to apply."
        )
