"""SQL operations for the skills/frameworks taxonomy."""
from __future__ import annotations

import logging

import psycopg2.extras

from db.connection import connection

logger = logging.getLogger(__name__)


# ── Reads ─────────────────────────────────────────────────────────────────

def get_candidate_skills() -> list[dict]:
    """Return candidate skills with their job counts, ordered by frequency."""
    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT s.skill_id, s.name, COUNT(js.job_id) AS job_count
                FROM skills s
                LEFT JOIN job_skills js ON s.skill_id = js.skill_id
                WHERE s.is_candidate = 1
                GROUP BY s.skill_id, s.name
                ORDER BY job_count DESC
                """
            )
            return [dict(row) for row in cur.fetchall()]


def get_candidate_frameworks() -> list[dict]:
    """Return candidate frameworks with their job counts, ordered by frequency."""
    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT f.framework_id, f.name, COUNT(jf.job_id) AS job_count
                FROM frameworks f
                LEFT JOIN job_frameworks jf ON f.framework_id = jf.framework_id
                WHERE f.is_candidate = 1
                GROUP BY f.framework_id, f.name
                ORDER BY job_count DESC
                """
            )
            return [dict(row) for row in cur.fetchall()]


def get_candidate_skills_above_threshold(min_jobs: int) -> list[dict]:
    """Return candidate skills referenced by at least min_jobs jobs, ordered by frequency."""
    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT s.skill_id, s.name, COUNT(js.job_id) AS job_count
                FROM skills s
                LEFT JOIN job_skills js ON s.skill_id = js.skill_id
                WHERE s.is_candidate = 1
                GROUP BY s.skill_id, s.name
                HAVING COUNT(js.job_id) >= %s
                ORDER BY job_count DESC
                """,
                (min_jobs,),
            )
            return [dict(row) for row in cur.fetchall()]


def get_candidate_frameworks_above_threshold(min_jobs: int) -> list[dict]:
    """Return candidate frameworks referenced by at least min_jobs jobs, ordered by frequency."""
    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT f.framework_id, f.name, COUNT(jf.job_id) AS job_count
                FROM frameworks f
                LEFT JOIN job_frameworks jf ON f.framework_id = jf.framework_id
                WHERE f.is_candidate = 1
                GROUP BY f.framework_id, f.name
                HAVING COUNT(jf.job_id) >= %s
                ORDER BY job_count DESC
                """,
                (min_jobs,),
            )
            return [dict(row) for row in cur.fetchall()]


def get_all_canonical_skills() -> list[dict]:
    """Return all canonical (non-candidate) skills as {skill_id, name}."""
    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT skill_id, name FROM skills WHERE is_candidate = 0 ORDER BY name"
            )
            return [dict(row) for row in cur.fetchall()]


def get_all_canonical_frameworks() -> list[dict]:
    """Return all canonical (non-candidate) frameworks as {framework_id, name}."""
    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT framework_id, name FROM frameworks WHERE is_candidate = 0 ORDER BY name"
            )
            return [dict(row) for row in cur.fetchall()]


def get_taxonomy_prompt_text(kind: str) -> str:
    """
    Generate a formatted taxonomy block for use in the extraction system prompt.
    Groups canonical entries by domain for readability.
    kind must be 'skills' or 'frameworks'.
    """
    with connection() as conn:
        with conn.cursor() as cur:
            if kind == "skills":
                cur.execute(
                    """
                    SELECT COALESCE(domain, 'Other') AS domain, name
                    FROM skills WHERE is_candidate = 0
                    ORDER BY domain NULLS LAST, name
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT COALESCE(domain, 'Other') AS domain, name
                    FROM frameworks WHERE is_candidate = 0
                    ORDER BY domain NULLS LAST, name
                    """
                )
            rows = cur.fetchall()

    grouped: dict[str, list[str]] = {}
    for domain, name in rows:
        grouped.setdefault(domain, []).append(name)

    parts = []
    for domain, names in grouped.items():
        parts.append(f"### {domain}")
        parts.append(", ".join(names))
    return "\n".join(parts)


# ── Promotion ─────────────────────────────────────────────────────────────

def mark_skill_promoted(skill_id: int) -> None:
    """Promote a candidate skill without assigning taxonomy placement fields."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE skills SET is_candidate = 0 WHERE skill_id = %s",
                (skill_id,),
            )


def mark_framework_promoted(framework_id: int) -> None:
    """Promote a candidate framework without assigning taxonomy placement fields."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE frameworks SET is_candidate = 0 WHERE framework_id = %s",
                (framework_id,),
            )


def promote_skill(skill_id: int, domain: str, core_competency: str, competency: str) -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE skills
                SET is_candidate = 0, domain = %s, core_competency = %s, competency = %s
                WHERE skill_id = %s
                """,
                (domain, core_competency, competency, skill_id),
            )


def promote_framework(framework_id: int, domain: str, subdomain: str, service: str) -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE frameworks
                SET is_candidate = 0, domain = %s, subdomain = %s, service = %s
                WHERE framework_id = %s
                """,
                (domain, subdomain, service, framework_id),
            )


# ── Merge / Discard ───────────────────────────────────────────────────────

def merge_skill(candidate_id: int, canonical_id: int) -> None:
    """Remap all job_skills from candidate → canonical, add alias, delete candidate."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM skills WHERE skill_id = %s", (candidate_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"skill_id {candidate_id} not found")
            candidate_name = row[0]

            # Jobs that already have the canonical skill would violate the PK
            # on update; delete those duplicates first, then remap the rest.
            cur.execute(
                "DELETE FROM job_skills WHERE skill_id = %s"
                " AND job_id IN (SELECT job_id FROM job_skills WHERE skill_id = %s)",
                (candidate_id, canonical_id),
            )
            cur.execute(
                "UPDATE job_skills SET skill_id = %s WHERE skill_id = %s",
                (canonical_id, candidate_id),
            )
            cur.execute(
                "INSERT INTO skill_aliases (alias, skill_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (candidate_name.lower(), canonical_id),
            )
            cur.execute("DELETE FROM skills WHERE skill_id = %s", (candidate_id,))


def merge_framework(candidate_id: int, canonical_id: int) -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM frameworks WHERE framework_id = %s", (candidate_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"framework_id {candidate_id} not found")
            candidate_name = row[0]

            cur.execute(
                "DELETE FROM job_frameworks WHERE framework_id = %s"
                " AND job_id IN (SELECT job_id FROM job_frameworks WHERE framework_id = %s)",
                (candidate_id, canonical_id),
            )
            cur.execute(
                "UPDATE job_frameworks SET framework_id = %s WHERE framework_id = %s",
                (canonical_id, candidate_id),
            )
            cur.execute(
                "INSERT INTO framework_aliases (alias, framework_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (candidate_name.lower(), canonical_id),
            )
            cur.execute("DELETE FROM frameworks WHERE framework_id = %s", (candidate_id,))


def discard_skill(skill_id: int) -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM job_skills WHERE skill_id = %s", (skill_id,))
            cur.execute("DELETE FROM skills WHERE skill_id = %s", (skill_id,))


def discard_framework(framework_id: int) -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM job_frameworks WHERE framework_id = %s", (framework_id,))
            cur.execute("DELETE FROM frameworks WHERE framework_id = %s", (framework_id,))
