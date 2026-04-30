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
    status: str = "active",
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
                    %(date_listed)s, %(status)s, %(serp_api_json)s,
                    %(embedding)s::vector, %(dedup_hash)s,
                    %(tier2_score)s, %(tier2_explanation)s
                )
                RETURNING job_id
                """,
                {
                    **job,
                    "serp_api_json": json.dumps(job.get("serp_api_json")),
                    "embedding": embedding,
                    "status": status,
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

def get_top_scored_jobs(
    top_k: int,
    min_score: int = 0,
    unscored_only: bool = True,
) -> list[dict]:
    """
    Return the top_k active jobs ordered by cheap-LLM fit score descending.

    Args:
        top_k:         Maximum number of jobs to return.
        min_score:     Only include jobs with tier2_score >= this value.
        unscored_only: When True (default), exclude jobs that already have a
                       tier3_score so the expensive LLM is not run twice.
                       Pass False to re-score all qualifying jobs.
    """
    tier3_filter = "AND tier3_score IS NULL" if unscored_only else ""
    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
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
                  {tier3_filter}
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


# ── Dashboard reads/writes ────────────────────────────────────────────────

from datetime import datetime, timezone  # noqa: E402 — may duplicate top-level import


def _ago(dt: datetime | None) -> str:
    """Convert a datetime to a human-readable 'Xm ago' / 'Xh ago' / 'Xd ago' string."""
    if dt is None:
        return "never"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(tz=timezone.utc)
    delta = now - dt
    total_seconds = int(delta.total_seconds())
    if total_seconds < 3600:
        return f"{max(total_seconds // 60, 1)}m ago"
    if total_seconds < 86400:
        return f"{total_seconds // 3600}h ago"
    return f"{total_seconds // 86400}d ago"


def get_freshness_stats() -> dict:
    """
    Return a single-query summary of pipeline freshness for the dashboard nav.
    Keys: last_ingested, last_ingested_ago, ingested_today, active_total, awaiting_tier3.
    """
    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    MAX(date_ingested) AS last_ingested,
                    COUNT(*) FILTER (WHERE status = 'active') AS active_total,
                    COUNT(*) FILTER (WHERE status = 'active' AND date_ingested > NOW() - INTERVAL '24 hours') AS ingested_today,
                    COUNT(*) FILTER (WHERE status = 'applied' ) AS applied_total,
                    COUNT(*) FILTER (WHERE status = 'expired') AS expired_total,
                    COUNT(*) FILTER (WHERE status IN ('bad_fit','bad_listing')) AS bad_fit_total
                FROM jobs
                """
            )
            row = dict(cur.fetchone())
    last_ingested = row.get("last_ingested")
    row["last_ingested_ago"] = _ago(last_ingested)
    return row


def expire_stale_applications() -> int:
    """
    Mark submitted applications with date_applied older than 30 days as expired.
    Returns the number of rows updated.
    """
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE applications
                SET state = 'expired'
                WHERE state = 'submitted'
                  AND date_applied < CURRENT_DATE - INTERVAL '30 days'
                """
            )
            count = cur.rowcount
    logger.info(f"Expired {count} stale application(s)")
    return count


def get_application_stats() -> dict:
    """
    Return counts for the applications nav header.
    Keys: app_total, app_awaiting, app_reached_human, app_interviewed,
          app_offers, app_rejected, app_expired.
    """
    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*)                                         AS app_total,
                    COUNT(*) FILTER (WHERE state = 'submitted')      AS app_awaiting,
                    COUNT(*) FILTER (WHERE reached_human = 1)        AS app_reached_human,
                    COUNT(*) FILTER (WHERE interviews > 0)           AS app_interviewed,
                    COUNT(*) FILTER (WHERE offer = 1)                AS app_offers,
                    COUNT(*) FILTER (WHERE state = 'rejected')       AS app_rejected,
                    COUNT(*) FILTER (WHERE state = 'expired')        AS app_expired
                FROM applications
                """
            )
            return dict(cur.fetchone())


_VALID_JOB_SORTS = {
    "tier2_score": "j.tier2_score DESC NULLS LAST",
    "tier3_score": "j.tier3_score DESC NULLS LAST",
    "date_listed":  "j.date_listed DESC NULLS LAST",
    "salary_max":   "j.salary_max DESC NULLS LAST",
}


def list_jobs(
    *,
    statuses: list[str] | None = None,
    tier2_min: float | None = None,
    tier3_min: float | None = None,
    seniority: list[str] | None = None,
    attendance: list[str] | None = None,
    location: str | None = None,
    title: str | None = None,
    company: str | None = None,
    description: str | None = None,
    date_listed_from: str | None = None,
    date_listed_to: str | None = None,
    has_application: bool | None = None,
    sort: str = "tier2_score",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict], int]:
    """
    Dynamically filtered/sorted job listing with pagination.
    Returns (rows, total_count).
    """
    conditions: list[str] = []
    params: list = []

    # Status filter — default to active only; always exclude extraction_failed and duplicate
    if statuses:
        safe_statuses = [s for s in statuses if s not in ("extraction_failed", "duplicate")]
    else:
        safe_statuses = ["active"]
    placeholders = ", ".join(["%s"] * len(safe_statuses))
    conditions.append(f"j.status IN ({placeholders})")
    params.extend(safe_statuses)

    if tier2_min is not None:
        conditions.append("j.tier2_score >= %s")
        params.append(tier2_min)
    if tier3_min is not None:
        conditions.append("j.tier3_score >= %s")
        params.append(tier3_min)
    if seniority:
        placeholders = ", ".join(["%s"] * len(seniority))
        conditions.append(f"j.seniority IN ({placeholders})")
        params.extend(seniority)
    if attendance:
        placeholders = ", ".join(["%s"] * len(attendance))
        conditions.append(f"j.attendance IN ({placeholders})")
        params.extend(attendance)
    if location:
        conditions.append("j.location ILIKE %s")
        params.append(f"%{location}%")
    if title:
        conditions.append("j.title ILIKE %s")
        params.append(f"%{title}%")
    if company:
        conditions.append("j.company_name ILIKE %s")
        params.append(f"%{company}%")
    if description:
        conditions.append("(j.description ILIKE %s OR j.qualifications ILIKE %s OR j.responsibilities ILIKE %s)")
        params.extend([f"%{description}%", f"%{description}%", f"%{description}%"])
    if date_listed_from:
        conditions.append("j.date_listed >= %s")
        params.append(date_listed_from)
    if date_listed_to:
        conditions.append("j.date_listed <= %s")
        params.append(date_listed_to)
    if has_application is True:
        conditions.append("j.application_id IS NOT NULL")
    elif has_application is False:
        conditions.append("j.application_id IS NULL")

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    order_clause = _VALID_JOB_SORTS.get(sort, _VALID_JOB_SORTS["tier2_score"])
    offset = (page - 1) * page_size

    select_sql = f"""
        SELECT
            j.job_id, j.title, j.company_name, j.location, j.attendance, j.seniority,
            j.employment_type, j.salary_min, j.salary_max, j.salary_currency, j.salary_period,
            j.date_listed, j.status, j.url,
            j.tier2_score, j.tier2_explanation, j.tier3_score,
            j.application_id, a.state AS application_state
        FROM jobs j
        LEFT JOIN applications a ON j.application_id = a.application_id
        {where_clause}
        ORDER BY {order_clause}
        LIMIT %s OFFSET %s
    """
    count_sql = f"""
        SELECT COUNT(*) AS total FROM jobs j
        LEFT JOIN applications a ON j.application_id = a.application_id
        {where_clause}
    """

    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(count_sql, params)
            total_count: int = cur.fetchone()["total"]
            cur.execute(select_sql, params + [page_size, offset])
            rows = [dict(row) for row in cur.fetchall()]

    return rows, total_count


def get_job_detail(job_id: int) -> dict | None:
    """
    Return full job detail including application fields, skills, and frameworks.
    Returns None if job not found.
    """
    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 1. Main job record + application fields
            cur.execute(
                """
                SELECT
                    j.job_id, j.title, j.company_name, j.location, j.attendance, j.seniority,
                    j.employment_type, j.salary_min, j.salary_max, j.salary_currency, j.salary_period,
                    j.description, j.qualifications, j.responsibilities,
                    j.date_listed, j.date_ingested, j.status, j.url,
                    j.tier2_score, j.tier2_explanation, j.tier3_score, j.tier3_explanation,
                    j.application_id,
                    a.state AS application_state,
                    a.date_applied, a.assistance_level, a.cover_letter, a.resume,
                    a.cold_calls, a.reached_human, a.interviews, a.offer
                FROM jobs j
                LEFT JOIN applications a ON j.application_id = a.application_id
                WHERE j.job_id = %s
                """,
                (job_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            result = dict(row)

            # 2. Skills (non-candidate)
            cur.execute(
                """
                SELECT s.name
                FROM job_skills js
                JOIN skills s ON js.skill_id = s.skill_id
                WHERE js.job_id = %s AND s.is_candidate = 0
                ORDER BY s.name
                """,
                (job_id,),
            )
            result["skills"] = [r["name"] for r in cur.fetchall()]

            # 3. Frameworks (non-candidate)
            cur.execute(
                """
                SELECT f.name
                FROM job_frameworks jf
                JOIN frameworks f ON jf.framework_id = f.framework_id
                WHERE jf.job_id = %s AND f.is_candidate = 0
                ORDER BY f.name
                """,
                (job_id,),
            )
            result["frameworks"] = [r["name"] for r in cur.fetchall()]

    return result


_VALID_APP_SORTS = {
    "date_applied": "a.date_applied DESC NULLS LAST",
    "state":        "CASE a.state WHEN 'offer' THEN 0 WHEN 'interviewing' THEN 1 WHEN 'submitted' THEN 2 WHEN 'rejected' THEN 3 ELSE 4 END ASC",
    "company_name": "j.company_name ASC",
}


def list_applications(
    *,
    states: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    company: str | None = None,
    assistance_level: list[str] | None = None,
    has_offer: bool | None = None,
    sort: str = "date_applied",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict], int]:
    """
    Dynamically filtered/sorted application listing with pagination.
    Returns (rows, total_count).
    """
    conditions: list[str] = []
    params: list = []

    if states:
        placeholders = ", ".join(["%s"] * len(states))
        conditions.append(f"a.state IN ({placeholders})")
        params.extend(states)
    if date_from:
        conditions.append("a.date_applied >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("a.date_applied <= %s")
        params.append(date_to)
    if company:
        conditions.append("j.company_name ILIKE %s")
        params.append(f"%{company}%")
    if assistance_level:
        placeholders = ", ".join(["%s"] * len(assistance_level))
        conditions.append(f"a.assistance_level IN ({placeholders})")
        params.extend(assistance_level)
    if has_offer is True:
        conditions.append("a.offer = 1")
    elif has_offer is False:
        conditions.append("a.offer = 0")

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    order_clause = _VALID_APP_SORTS.get(sort, _VALID_APP_SORTS["date_applied"])
    offset = (page - 1) * page_size

    select_sql = f"""
        SELECT
            a.application_id, a.job_id, a.date_applied, a.state, a.assistance_level,
            a.cover_letter, a.resume, a.cold_calls, a.reached_human, a.interviews, a.offer, a.effort,
            j.title AS job_title, j.company_name, j.tier2_score, j.tier3_score
        FROM applications a
        JOIN jobs j ON a.job_id = j.job_id
        {where_clause}
        ORDER BY {order_clause}
        LIMIT %s OFFSET %s
    """
    count_sql = f"""
        SELECT COUNT(*) AS total FROM applications a
        JOIN jobs j ON a.job_id = j.job_id
        {where_clause}
    """

    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(count_sql, params)
            total_count: int = cur.fetchone()["total"]
            cur.execute(select_sql, params + [page_size, offset])
            rows = [dict(row) for row in cur.fetchall()]

    return rows, total_count


def get_application_detail(application_id: int) -> dict | None:
    """
    Return full application detail joined with job fields.
    Returns None if not found.
    """
    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    a.application_id, a.job_id, a.date_applied, a.state, a.assistance_level,
                    a.cover_letter, a.resume, a.cold_calls, a.reached_human, a.interviews, a.offer, a.effort,
                    j.title AS job_title, j.company_name, j.location,
                    j.salary_min, j.salary_max, j.salary_currency, j.salary_period,
                    j.tier2_score, j.tier3_score, j.url AS job_url, j.status AS job_status
                FROM applications a
                JOIN jobs j ON a.job_id = j.job_id
                WHERE a.application_id = %s
                """,
                (application_id,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


_VALID_JOB_STATUSES = frozenset({"active", "applied", "expired", "closed", "bad_listing", "bad_fit"})


def update_job_status(job_id: int, status: str) -> None:
    """Update a job's status. Raises ValueError if status is not in the allowed set."""
    if status not in _VALID_JOB_STATUSES:
        raise ValueError(f"Invalid job status {status!r}. Must be one of {_VALID_JOB_STATUSES}.")
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status = %s, date_updated = NOW() WHERE job_id = %s",
                (status, job_id),
            )


def create_application(  # type: ignore[override]  # shadows module-level function with dashboard signature
    *,
    job_id: int,
    date_applied: str | None = None,
    state: str = "submitted",
    assistance_level: str | None = None,
    cover_letter: str | None = None,
    resume: str | None = None,
    cold_calls: int = 0,
    reached_human: int = 0,
    interviews: int = 0,
    offer: int = 0,
    effort: float | None = None,
) -> int:
    """
    Dashboard version of create_application — includes the new 'state' column.
    INSERT into applications, set jobs.application_id back-pointer.
    Returns the new application_id.
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
                    job_id, date_applied, state, assistance_level,
                    cover_letter, resume,
                    cold_calls, reached_human, interviews, offer, effort
                ) VALUES (
                    %(job_id)s, %(date_applied)s, %(state)s, %(assistance_level)s,
                    %(cover_letter)s, %(resume)s,
                    %(cold_calls)s, %(reached_human)s, %(interviews)s, %(offer)s, %(effort)s
                )
                RETURNING application_id
                """,
                {
                    "job_id":           job_id,
                    "date_applied":     date_applied,
                    "state":            state,
                    "assistance_level": assistance_level,
                    "cover_letter":     cover_letter,
                    "resume":           resume,
                    "cold_calls":       cold_calls,
                    "reached_human":    reached_human,
                    "interviews":       interviews,
                    "offer":            offer,
                    "effort":           effort,
                },
            )
            application_id: int = cur.fetchone()[0]
            cur.execute(
                "UPDATE jobs SET application_id = %s, status = 'applied', date_updated = NOW() WHERE job_id = %s",
                (application_id, job_id),
            )

    logger.debug(f"Dashboard created application_id={application_id} for job_id={job_id}")
    return application_id


def update_application(application_id: int, **fields) -> None:  # type: ignore[override]
    """
    Dashboard version of update_application — includes the new 'state' column.
    Filters fields against allowlist. If state=='offer', also sets offer=1 in lockstep.
    """
    _UPDATABLE = frozenset({
        "date_applied", "state", "assistance_level", "cover_letter", "resume",
        "cold_calls", "reached_human", "interviews", "offer", "effort",
    })
    filtered = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not filtered:
        return

    # Keep offer in sync with state
    if filtered.get("state") == "offer":
        filtered["offer"] = 1

    set_clause = ", ".join(f"{col} = %({col})s" for col in filtered)
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE applications SET {set_clause} WHERE application_id = %(application_id)s",
                {**filtered, "application_id": application_id},
            )
