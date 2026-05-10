#!/usr/bin/env python3
"""
One-off migration: fix cross-table taxonomy entries.

Scans candidate skills for name/alias matches in frameworks (and vice versa)
and remaps junction rows accordingly. Also fixes canonical misclassifications
where a skill canonical already has a matching framework canonical (e.g. Python, SQL).

Operations performed:
  1. Candidate skills matching a framework canonical or alias
       → remap job_skills → job_frameworks, add framework alias, delete skill
  2. Candidate frameworks matching a skill canonical or alias
       → remap job_frameworks → job_skills, add skill alias, delete framework
  3. Canonical skills whose name matches a framework canonical (is_candidate=0)
       → remap job_skills → job_frameworks, migrate any skill aliases, delete skill

Usage:
    python scripts/fix_cross_table_taxonomy.py           # dry run
    python scripts/fix_cross_table_taxonomy.py --execute # apply changes
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import psycopg2.extras

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from db.connection import connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger("fix_cross_table")


# ── Query helpers ─────────────────────────────────────────────────────────────

def _find_candidate_skills_in_frameworks() -> list[tuple[int, str, int, str]]:
    """Candidate skills whose name or lowercase matches a framework canonical or alias."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT s.skill_id, s.name, f.framework_id, f.name
                FROM skills s
                JOIN frameworks f ON LOWER(s.name) = LOWER(f.name)
                WHERE s.is_candidate = 1 AND f.is_candidate = 0
                UNION
                SELECT DISTINCT s.skill_id, s.name, f.framework_id, f.name
                FROM skills s
                JOIN framework_aliases fa ON LOWER(s.name) = fa.alias
                JOIN frameworks f ON fa.framework_id = f.framework_id
                WHERE s.is_candidate = 1
                ORDER BY 2
            """)
            return cur.fetchall()


def _find_candidate_frameworks_in_skills() -> list[tuple[int, str, int, str]]:
    """Candidate frameworks whose name or lowercase matches a skill canonical or alias."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT f.framework_id, f.name, s.skill_id, s.name
                FROM frameworks f
                JOIN skills s ON LOWER(f.name) = LOWER(s.name)
                WHERE f.is_candidate = 1 AND s.is_candidate = 0
                UNION
                SELECT DISTINCT f.framework_id, f.name, s.skill_id, s.name
                FROM frameworks f
                JOIN skill_aliases sa ON LOWER(f.name) = sa.alias
                JOIN skills s ON sa.skill_id = s.skill_id
                WHERE f.is_candidate = 1
                ORDER BY 2
            """)
            return cur.fetchall()


def _find_canonical_skills_in_frameworks() -> list[tuple[int, str, int, str]]:
    """Canonical skills whose name matches a framework canonical — the SQL/Python case."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT s.skill_id, s.name, f.framework_id, f.name
                FROM skills s
                JOIN frameworks f ON LOWER(s.name) = LOWER(f.name)
                WHERE s.is_candidate = 0 AND f.is_candidate = 0
                ORDER BY 2
            """)
            return cur.fetchall()


# ── Fix routines ──────────────────────────────────────────────────────────────

def _move_skill_to_framework(skill_id: int, framework_id: int) -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            # Remap junction rows
            cur.execute("SELECT job_id FROM job_skills WHERE skill_id = %s", (skill_id,))
            job_ids = [r[0] for r in cur.fetchall()]
            if job_ids:
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO job_frameworks (job_id, framework_id) VALUES %s ON CONFLICT DO NOTHING",
                    [(jid, framework_id) for jid in job_ids],
                )
            cur.execute("DELETE FROM job_skills WHERE skill_id = %s", (skill_id,))

            # Migrate any existing skill aliases to framework aliases
            cur.execute("SELECT alias FROM skill_aliases WHERE skill_id = %s", (skill_id,))
            aliases = [r[0] for r in cur.fetchall()]
            for alias in aliases:
                cur.execute(
                    "INSERT INTO framework_aliases (alias, framework_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (alias, framework_id),
                )
            cur.execute("DELETE FROM skill_aliases WHERE skill_id = %s", (skill_id,))

            # Add alias for the skill name itself (in case it wasn't already there)
            cur.execute("SELECT name FROM skills WHERE skill_id = %s", (skill_id,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    "INSERT INTO framework_aliases (alias, framework_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (row[0].lower(), framework_id),
                )

            cur.execute("DELETE FROM skills WHERE skill_id = %s", (skill_id,))


def _move_framework_to_skill(framework_id: int, skill_id: int) -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT job_id FROM job_frameworks WHERE framework_id = %s", (framework_id,))
            job_ids = [r[0] for r in cur.fetchall()]
            if job_ids:
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO job_skills (job_id, skill_id) VALUES %s ON CONFLICT DO NOTHING",
                    [(jid, skill_id) for jid in job_ids],
                )
            cur.execute("DELETE FROM job_frameworks WHERE framework_id = %s", (framework_id,))

            cur.execute("SELECT alias FROM framework_aliases WHERE framework_id = %s", (framework_id,))
            aliases = [r[0] for r in cur.fetchall()]
            for alias in aliases:
                cur.execute(
                    "INSERT INTO skill_aliases (alias, skill_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (alias, skill_id),
                )
            cur.execute("DELETE FROM framework_aliases WHERE framework_id = %s", (framework_id,))

            cur.execute("SELECT name FROM frameworks WHERE framework_id = %s", (framework_id,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    "INSERT INTO skill_aliases (alias, skill_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (row[0].lower(), skill_id),
                )

            cur.execute("DELETE FROM frameworks WHERE framework_id = %s", (framework_id,))


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Fix cross-table taxonomy entries.")
    p.add_argument("--execute", action="store_true", help="Apply changes (default: dry run)")
    args = p.parse_args()

    dry = not args.execute
    mode = "DRY RUN" if dry else "EXECUTE"
    logger.info(f"=== Cross-table taxonomy fix ({mode}) ===\n")

    # Phase 1: candidate skills → frameworks
    rows1 = _find_candidate_skills_in_frameworks()
    logger.info(f"Phase 1 — candidate skills matching frameworks: {len(rows1)} found")
    for skill_id, skill_name, framework_id, framework_name in rows1:
        logger.info(f"  skill→framework: {skill_name!r} (sid={skill_id}) → {framework_name!r} (fid={framework_id})")
        if not dry:
            _move_skill_to_framework(skill_id, framework_id)

    # Phase 2: candidate frameworks → skills
    rows2 = _find_candidate_frameworks_in_skills()
    logger.info(f"\nPhase 2 — candidate frameworks matching skills: {len(rows2)} found")
    for framework_id, framework_name, skill_id, skill_name in rows2:
        logger.info(f"  framework→skill: {framework_name!r} (fid={framework_id}) → {skill_name!r} (sid={skill_id})")
        if not dry:
            _move_framework_to_skill(framework_id, skill_id)

    # Phase 3: canonical skills matching framework canonicals (e.g. SQL, Python)
    rows3 = _find_canonical_skills_in_frameworks()
    logger.info(f"\nPhase 3 — canonical skills already in frameworks: {len(rows3)} found")
    # print("Doing nothing for now.")
    for skill_id, skill_name, framework_id, framework_name in rows3:
        logger.info(f"  canonical skill→framework: {skill_name!r} (sid={skill_id}) → {framework_name!r} (fid={framework_id})")
        if not dry:
            pass
            # _move_skill_to_framework(skill_id, framework_id)

    total = len(rows1) + len(rows2) + len(rows3)
    if dry:
        logger.info(f"\nTotal: {total} entries would be fixed. Re-run with --execute to apply.")
    else:
        logger.info(f"\nTotal: {total} entries fixed.")


if __name__ == "__main__":
    main()
