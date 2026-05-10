#!/usr/bin/env python3
"""
Delete skills and frameworks that are not referenced by any job listing.

An entry is considered unreferenced when it has no rows in job_skills
(for skills) or job_frameworks (for frameworks).  Aliases are cascade-deleted
with the parent row.

Usage:
    python scripts/purge_unreferenced_taxonomy.py           # dry run
    python scripts/purge_unreferenced_taxonomy.py --execute # apply changes
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from db.connection import connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger("purge_unreferenced_taxonomy")

_UNREFERENCED_SKILLS_SQL = """
    SELECT skill_id, name
    FROM skills
    WHERE skill_id NOT IN (SELECT DISTINCT skill_id FROM job_skills)
    ORDER BY skill_id
"""

_UNREFERENCED_FRAMEWORKS_SQL = """
    SELECT framework_id, name
    FROM frameworks
    WHERE framework_id NOT IN (SELECT DISTINCT framework_id FROM job_frameworks)
    ORDER BY framework_id
"""

_DELETE_SKILLS_SQL = """
    DELETE FROM skills
    WHERE skill_id NOT IN (SELECT DISTINCT skill_id FROM job_skills)
"""

_DELETE_FRAMEWORKS_SQL = """
    DELETE FROM frameworks
    WHERE framework_id NOT IN (SELECT DISTINCT framework_id FROM job_frameworks)
"""


def main() -> None:
    p = argparse.ArgumentParser(description="Purge unreferenced taxonomy entries.")
    p.add_argument("--execute", action="store_true", help="Apply deletions (default: dry run)")
    args = p.parse_args()

    dry = not args.execute
    mode = "DRY RUN" if dry else "EXECUTE"
    logger.info(f"=== Purge unreferenced taxonomy ({mode}) ===\n")

    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_UNREFERENCED_SKILLS_SQL)
            orphan_skills = cur.fetchall()

            cur.execute(_UNREFERENCED_FRAMEWORKS_SQL)
            orphan_frameworks = cur.fetchall()

    logger.info(f"Unreferenced skills    : {len(orphan_skills)}")
    for skill_id, name in orphan_skills:
        logger.info(f"  DELETE skill     (id={skill_id}) {name!r}")

    logger.info(f"Unreferenced frameworks: {len(orphan_frameworks)}")
    for framework_id, name in orphan_frameworks:
        logger.info(f"  DELETE framework (id={framework_id}) {name!r}")

    if not orphan_skills and not orphan_frameworks:
        logger.info("Nothing to delete.")
        return

    if dry:
        logger.info(f"\nDry run complete — {len(orphan_skills)} skill(s), "
                    f"{len(orphan_frameworks)} framework(s) would be deleted. "
                    "Re-run with --execute to apply.")
        return

    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_DELETE_SKILLS_SQL)
            deleted_skills = cur.rowcount
            cur.execute(_DELETE_FRAMEWORKS_SQL)
            deleted_frameworks = cur.rowcount

    logger.info(f"\nDeleted {deleted_skills} skill(s), {deleted_frameworks} framework(s).")


if __name__ == "__main__":
    main()
