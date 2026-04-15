#!/usr/bin/env python3
"""
Database bootstrap script.

1. Creates all tables and indexes from db/schema.sql
2. Seeds canonical skills from db/seed/skills.csv
3. Seeds canonical frameworks from db/seed/frameworks.csv
4. Seeds skill aliases from db/seed/skill_aliases.csv
5. Seeds framework aliases from db/seed/framework_aliases.csv

Safe to re-run — all inserts use ON CONFLICT DO NOTHING.

Usage:
    python -m db.seed.seed
    # or from project root:
    python db/seed/seed.py
"""
from __future__ import annotations

import csv
import logging
import sys
from pathlib import Path

# Allow running as a script from anywhere in the project
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

import psycopg2

from config.settings import DATABASE_URL

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

SCHEMA_FILE          = ROOT_DIR / "db" / "schema.sql"
SEED_DIR             = ROOT_DIR / "db" / "seed"
SKILLS_CSV           = SEED_DIR / "skills.csv"
FRAMEWORKS_CSV       = SEED_DIR / "frameworks.csv"
SKILL_ALIASES_CSV    = SEED_DIR / "skill_aliases.csv"
FRAMEWORK_ALIASES_CSV = SEED_DIR / "framework_aliases.csv"


def run_schema(conn: psycopg2.extensions.connection) -> None:
    logger.info("Applying schema.sql …")
    with conn.cursor() as cur:
        cur.execute(SCHEMA_FILE.read_text())
    conn.commit()
    logger.info("Schema applied.")


def seed_skills(conn: psycopg2.extensions.connection) -> None:
    logger.info("Seeding skills …")
    count = 0
    with open(SKILLS_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        with conn.cursor() as cur:
            for row in reader:
                cur.execute(
                    """
                    INSERT INTO skills (domain, core_competency, competency, name, is_candidate)
                    VALUES (%s, %s, %s, %s, 0)
                    ON CONFLICT (name) DO NOTHING
                    """,
                    (
                        row["domain"],
                        row.get("core_competency") or None,
                        row.get("competency") or None,
                        row["skill"],
                    ),
                )
                count += cur.rowcount
    conn.commit()
    logger.info(f"  Inserted {count} skills.")


def seed_frameworks(conn: psycopg2.extensions.connection) -> None:
    logger.info("Seeding frameworks …")
    count = 0
    with open(FRAMEWORKS_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        with conn.cursor() as cur:
            for row in reader:
                cur.execute(
                    """
                    INSERT INTO frameworks (domain, subdomain, service, name, is_candidate)
                    VALUES (%s, %s, %s, %s, 0)
                    ON CONFLICT (name) DO NOTHING
                    """,
                    (
                        row["domain"],
                        row.get("subdomain") or None,
                        row.get("service") or None,
                        row["framework"],
                    ),
                )
                count += cur.rowcount
    conn.commit()
    logger.info(f"  Inserted {count} frameworks.")


def seed_skill_aliases(conn: psycopg2.extensions.connection) -> None:
    logger.info("Seeding skill aliases …")
    count = 0
    missing = []
    with open(SKILL_ALIASES_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        with conn.cursor() as cur:
            for row in reader:
                alias         = row["alias"].strip().lower()
                canonical     = row["canonical_name"].strip()
                cur.execute("SELECT skill_id FROM skills WHERE name = %s", (canonical,))
                result = cur.fetchone()
                if result is None:
                    missing.append(canonical)
                    continue
                skill_id = result[0]
                cur.execute(
                    "INSERT INTO skill_aliases (alias, skill_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (alias, skill_id),
                )
                count += cur.rowcount
    conn.commit()
    if missing:
        logger.warning(f"  Skipped {len(missing)} aliases with unknown canonical names: {missing[:5]}…")
    logger.info(f"  Inserted {count} skill aliases.")


def seed_framework_aliases(conn: psycopg2.extensions.connection) -> None:
    logger.info("Seeding framework aliases …")
    count = 0
    missing = []
    with open(FRAMEWORK_ALIASES_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        with conn.cursor() as cur:
            for row in reader:
                alias     = row["alias"].strip().lower()
                canonical = row["canonical_name"].strip()
                cur.execute("SELECT framework_id FROM frameworks WHERE name = %s", (canonical,))
                result = cur.fetchone()
                if result is None:
                    missing.append(canonical)
                    continue
                framework_id = result[0]
                cur.execute(
                    "INSERT INTO framework_aliases (alias, framework_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (alias, framework_id),
                )
                count += cur.rowcount
    conn.commit()
    if missing:
        logger.warning(f"  Skipped {len(missing)} aliases with unknown canonical names: {missing[:5]}…")
    logger.info(f"  Inserted {count} framework aliases.")


def main() -> None:
    logger.info(f"Connecting to database …")
    conn = psycopg2.connect(DATABASE_URL)
    try:
        run_schema(conn)
        seed_skills(conn)
        seed_frameworks(conn)
        seed_skill_aliases(conn)
        seed_framework_aliases(conn)
        logger.info("Bootstrap complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
