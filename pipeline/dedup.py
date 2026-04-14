"""
Fuzzy deduplication.

Two-stage check:
  1. Exact dedup_hash match — SHA-256 of (normalised title + company + location)
  2. Fuzzy title check within same company — thefuzz token_sort_ratio ≥ threshold

The fuzzy check restricts candidates to the same company via a pg_trgm similarity
query, avoiding a full-table O(n) scan.  The GIN index on company_name makes this
fast even with tens of thousands of jobs.
"""
from __future__ import annotations

import hashlib
import logging
import re

from thefuzz import fuzz

from config.settings import DEDUP_FUZZY_THRESHOLD
from db.connection import connection

logger = logging.getLogger(__name__)

_ABBREV: list[tuple[str, str]] = [
    (r"\bsr\.?\b",    "senior"),
    (r"\bjr\.?\b",    "junior"),
    (r"\beng\.?\b",   "engineer"),
    (r"\bmgr\.?\b",   "manager"),
    (r"\bdir\.?\b",   "director"),
    (r"\bvp\.?\b",    "vice president"),
    (r"\bdev\.?\b",   "developer"),
    (r"\bspec\.?\b",  "specialist"),
    (r"\bassoc\.?\b", "associate"),
    (r"\binc\.?\b",   ""),
    (r"\bllc\.?\b",   ""),
    (r"\bltd\.?\b",   ""),
    (r"\bcorp\.?\b",  ""),
]


def _normalise(text: str) -> str:
    text = (text or "").lower()
    for pattern, replacement in _ABBREV:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def make_dedup_hash(job: dict) -> str:
    """Deterministic SHA-256 based on normalised title + company + location."""
    title    = _normalise(job.get("title", ""))
    company  = _normalise(job.get("company_name", ""))
    location = _normalise(job.get("location", ""))
    return hashlib.sha256(f"{title}|{company}|{location}".encode()).hexdigest()


class Deduplicator:
    """
    Stateless deduplicator — each call queries the DB.
    Instantiate once per pipeline run and reuse.
    """

    def is_duplicate(self, job: dict) -> tuple[bool, str]:
        """
        Returns (is_duplicate, reason).
        reason is 'exact_hash', 'fuzzy', or '' (not a duplicate).

        Sets job['dedup_hash'] as a side effect for use downstream.
        """
        h = make_dedup_hash(job)
        job["dedup_hash"] = h

        with connection() as conn:
            with conn.cursor() as cur:
                # ── Stage 1: exact hash ──────────────────────────────────
                cur.execute(
                    "SELECT 1 FROM jobs WHERE dedup_hash = %s LIMIT 1", (h,)
                )
                if cur.fetchone():
                    logger.debug(f"Exact duplicate: {job.get('title')!r}")
                    return True, "exact_hash"

                # ── Stage 2: fuzzy title within same company ─────────────
                cur.execute(
                    """
                    SELECT title
                    FROM jobs
                    WHERE company_name % %s
                    LIMIT 200
                    """,
                    (job.get("company_name", ""),),
                )
                candidates = [row[0] for row in cur.fetchall()]

        norm_title = _normalise(job.get("title", ""))
        for candidate in candidates:
            if fuzz.token_sort_ratio(norm_title, _normalise(candidate)) >= DEDUP_FUZZY_THRESHOLD:
                logger.debug(
                    f"Fuzzy duplicate: {job.get('title')!r} ~ {candidate!r}"
                )
                return True, "fuzzy"

        return False, ""
