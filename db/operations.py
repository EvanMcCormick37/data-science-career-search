"""
Centralised SQL operations — all writes and structured reads go through here.

Keeps raw SQL out of pipeline modules and makes it easy to audit what the
pipeline does to the database.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import psycopg2.extras

from db.connection import connection

logger = logging.getLogger(__name__)


# ── Job writes ────────────────────────────────────────────────────────────

def insert_job(
    job: dict,
    embedding: list[float],
    skill_ids: list[int],
    framework_ids: list[int],
) -> int:
    """
    Insert a job record, its embedding, and taxonomy junction rows.
    Returns the new job_id.
    """
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (
                    title, url, company_name, location, description,
                    employment_type, attendance, seniority,
                    experience_years_min, experience_years_max,
                    salary_min, salary_max, salary_currency, salary_period,
                    qualifications, responsibilities,
                    date_listed, status, serp_api_json, embedding, dedup_hash,
                    tier2_score, tier2_explanation
                ) VALUES (
                    %(title)s, %(url)s, %(company_name)s, %(location)s, %(description)s,
                    %(employment_type)s, %(attendance)s, %(seniority)s,
                    %(experience_years_min)s, %(experience_years_max)s,
                    %(salary_min)s, %(salary_max)s, %(salary_currency)s, %(salary_period)s,
                    %(qualifications)s, %(responsibilities)s,
                    %(date_listed)s, 'active', %(serp_api_json)s,
                    %(embedding)s::vector, %(dedup_hash)s,
                    %(tier2_score)s, %(tier2_explanation)s
                )
                RETURNING job_id
                """,
                {
                    **job,
                    "serp_api_json": json.dumps(job.get("serp_api_json")),
                    "embedding": embedding,
                },
            )
            job_id: int = cur.fetchone()[0]

            if skill_ids:
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO job_skills (job_id, skill_id) VALUES %s ON CONFLICT DO NOTHING",
                    [(job_id, sid) for sid in skill_ids],
                )
            if framework_ids:
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO job_frameworks (job_id, framework_id) VALUES %s ON CONFLICT DO NOTHING",
                    [(job_id, fid) for fid in framework_ids],
                )
        conn.commit()

    logger.debug(f"Inserted job_id={job_id}: {job.get('title')!r} @ {job.get('company_name')!r}")
    return job_id


def mark_job_failed(dedup_hash: str, serp_api_json: dict) -> None:
    """Store a job that failed LLM extraction so it can be reprocessed later."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (title, url, company_name, status, serp_api_json, dedup_hash)
                VALUES ('(extraction failed)', '', '', 'extraction_failed', %s, %s)
                ON CONFLICT (dedup_hash) DO NOTHING
                """,
                (json.dumps(serp_api_json), dedup_hash),
            )


def update_tier2_scores(job_id: int, score: float, explanation: str) -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET tier2_score = %s, tier2_explanation = %s WHERE job_id = %s",
                (score, explanation, job_id),
            )


def update_tier3_scores(job_id: int, score: float, explanation: str) -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET tier3_score = %s, tier3_explanation = %s WHERE job_id = %s",
                (score, explanation, job_id),
            )


def expire_old_jobs(expiry_days: int) -> int:
    """Mark active jobs older than expiry_days as expired. Returns count updated."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET status = 'expired'
                WHERE date_listed < NOW() - INTERVAL '%s days'
                  AND status = 'active'
                """,
                (expiry_days,),
            )
            count = cur.rowcount
    logger.info(f"Expired {count} jobs older than {expiry_days} days")
    return count


# ── Job reads ─────────────────────────────────────────────────────────────

def get_top_scored_jobs(top_k: int, min_score: int = 0) -> list[dict]:
    """
    Return the top_k active jobs ordered by cheap-LLM fit score descending.

    Only jobs that have already been scored (tier2_score IS NOT NULL) are
    included.  Use min_score to filter out clear non-starters before passing
    to the expensive LLM.
    """
    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    job_id, title, company_name, location,
                    attendance, seniority, employment_type,
                    salary_min, salary_max, salary_currency, salary_period,
                    description, qualifications, responsibilities,
                    date_listed, url,
                    tier2_score, tier2_explanation
                FROM jobs
                WHERE status = 'active'
                  AND tier2_score IS NOT NULL
                  AND tier2_score >= %(min_score)s
                ORDER BY tier2_score DESC
                LIMIT %(top_k)s
                """,
                {"top_k": top_k, "min_score": min_score},
            )
            return [dict(row) for row in cur.fetchall()]


def get_jobs_for_reprocessing() -> list[dict]:
    """Return all jobs with status='extraction_failed' that have stored serp_api_json."""
    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT job_id, dedup_hash, serp_api_json
                FROM jobs
                WHERE status = 'extraction_failed'
                  AND serp_api_json IS NOT NULL
                """
            )
            return [dict(row) for row in cur.fetchall()]


def get_active_job_count() -> int:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM jobs WHERE status = 'active'")
            return cur.fetchone()[0]


def get_jobs_by_ids(job_ids: list[int]) -> list[dict]:
    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM jobs WHERE job_id = ANY(%s)",
                (job_ids,),
            )
            return [dict(row) for row in cur.fetchall()]


# ── Taxonomy reads ────────────────────────────────────────────────────────

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


def merge_skill(candidate_id: int, canonical_id: int) -> None:
    """Remap all job_skills from candidate → canonical, add alias, delete candidate."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM skills WHERE skill_id = %s", (candidate_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"skill_id {candidate_id} not found")
            candidate_name = row[0]

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


# ── Application writes ────────────────────────────────────────────────────

_ASSISTANCE_LEVELS = {"ai", "assisted", "human"}


def create_application(
    job_id: int,
    *,
    date_applied: str | None = None,
    assistance_level: str | None = None,
    cover_letter: str | None = None,
    resume: str | None = None,
    cold_calls: int = 0,
    reached_human: int = 0,
    interviews: int = 0,
    offer: int = 0,
) -> int:
    """
    Insert a new application row and set jobs.application_id to point back to it.
    Returns the new application_id.

    assistance_level must be one of 'ai', 'assisted', 'human' (or None).
    reached_human and offer are booleans stored as 0/1.
    """
    if assistance_level is not None and assistance_level not in _ASSISTANCE_LEVELS:
        raise ValueError(
            f"assistance_level must be one of {_ASSISTANCE_LEVELS}, got {assistance_level!r}"
        )

    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO applications (
                    job_id, date_applied, assistance_level,
                    cover_letter, resume,
                    cold_calls, reached_human, interviews, offer
                ) VALUES (
                    %(job_id)s, %(date_applied)s, %(assistance_level)s,
                    %(cover_letter)s, %(resume)s,
                    %(cold_calls)s, %(reached_human)s, %(interviews)s, %(offer)s
                )
                RETURNING application_id
                """,
                {
                    "job_id":           job_id,
                    "date_applied":     date_applied,
                    "assistance_level": assistance_level,
                    "cover_letter":     cover_letter,
                    "resume":           resume,
                    "cold_calls":       cold_calls,
                    "reached_human":    reached_human,
                    "interviews":       interviews,
                    "offer":            offer,
                },
            )
            application_id: int = cur.fetchone()[0]

            # Write the back-pointer onto the job row.
            cur.execute(
                "UPDATE jobs SET application_id = %s WHERE job_id = %s",
                (application_id, job_id),
            )

    logger.debug(f"Created application_id={application_id} for job_id={job_id}")
    return application_id


def update_application(application_id: int, **fields) -> None:
    """
    Update one or more fields on an existing application.

    Only the keys supplied in **fields are touched; everything else is left as-is.
    Valid field names mirror the applications table columns (excluding application_id
    and job_id, which are immutable after creation).
    """
    _UPDATABLE = {
        "date_applied", "assistance_level", "cover_letter", "resume",
        "cold_calls", "reached_human", "interviews", "offer",
    }
    invalid = set(fields) - _UPDATABLE
    if invalid:
        raise ValueError(f"Unknown application fields: {invalid}")
    if not fields:
        return

    if "assistance_level" in fields and fields["assistance_level"] not in _ASSISTANCE_LEVELS | {None}:
        raise ValueError(
            f"assistance_level must be one of {_ASSISTANCE_LEVELS}, "
            f"got {fields['assistance_level']!r}"
        )

    set_clause = ", ".join(f"{col} = %({col})s" for col in fields)
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE applications SET {set_clause} WHERE application_id = %(application_id)s",
                {**fields, "application_id": application_id},
            )


# ── Application reads ─────────────────────────────────────────────────────

def get_application(application_id: int) -> dict | None:
    """Return the application row as a dict, or None if not found."""
    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM applications WHERE application_id = %s",
                (application_id,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def get_application_by_job(job_id: int) -> dict | None:
    """Return the application linked to a job, or None if no application exists."""
    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM applications WHERE job_id = %s ORDER BY application_id DESC LIMIT 1",
                (job_id,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def get_all_applications() -> list[dict]:
    """
    Return all applications joined with basic job info, ordered by date_applied desc.
    """
    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    a.application_id,
                    a.job_id,
                    j.title,
                    j.company_name,
                    j.location,
                    j.url,
                    a.date_applied,
                    a.assistance_level,
                    a.cold_calls,
                    a.reached_human,
                    a.interviews,
                    a.offer
                FROM applications a
                JOIN jobs j ON j.job_id = a.job_id
                ORDER BY a.date_applied DESC NULLS LAST, a.application_id DESC
                """
            )
            return [dict(row) for row in cur.fetchall()]
