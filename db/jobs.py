"""SQL operations for the jobs table and its taxonomy junction tables."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import psycopg2.extras

from db.connection import connection

logger = logging.getLogger(__name__)


# ── Writes ────────────────────────────────────────────────────────────────

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
                    t2_score, t2_explanation
                ) VALUES (
                    %(title)s, %(url)s, %(company_name)s, %(location)s, %(description)s,
                    %(employment_type)s, %(attendance)s, %(seniority)s,
                    %(experience_years_min)s, %(experience_years_max)s,
                    %(salary_min)s, %(salary_max)s, %(salary_currency)s, %(salary_period)s,
                    %(qualifications)s, %(responsibilities)s,
                    %(date_listed)s, %(status)s, %(serp_api_json)s,
                    %(embedding)s::vector, %(dedup_hash)s,
                    %(t2_score)s, %(t2_explanation)s
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
                "UPDATE jobs SET t2_score = %s, t2_explanation = %s WHERE job_id = %s",
                (score, explanation, job_id),
            )


def update_tier3_scores(
    job_id: int,
    *,
    t3_score: float,
    t3_explanation: str,
    t3_qualification: float,
    t3_fit: float,
) -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET t3_score = %s, t3_explanation = %s,
                    t3_qualification = %s, t3_fit = %s
                WHERE job_id = %s
                """,
                (t3_score, t3_explanation, t3_qualification, t3_fit, job_id),
            )


def upsert_reprocessed_job(
    job_record: dict,
    embedding: list[float],
    skill_ids: list[int],
    framework_ids: list[int],
    job_status: str,
) -> None:
    """Update a reprocessed job's fields, embedding, and taxonomy junction rows."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs SET
                    title = %(title)s,
                    url = %(url)s,
                    company_name = %(company_name)s,
                    location = %(location)s,
                    description = %(description)s,
                    employment_type = %(employment_type)s,
                    attendance = %(attendance)s,
                    seniority = %(seniority)s,
                    experience_years_min = %(experience_years_min)s,
                    experience_years_max = %(experience_years_max)s,
                    salary_min = %(salary_min)s,
                    salary_max = %(salary_max)s,
                    salary_currency = %(salary_currency)s,
                    salary_period = %(salary_period)s,
                    qualifications = %(qualifications)s,
                    responsibilities = %(responsibilities)s,
                    embedding = %(embedding)s::vector,
                    status = %(job_status)s,
                    date_updated = NOW()
                WHERE dedup_hash = %(dedup_hash)s
                """,
                {**job_record, "embedding": embedding, "job_status": job_status},
            )
            cur.execute(
                "SELECT job_id FROM jobs WHERE dedup_hash = %s",
                (job_record["dedup_hash"],),
            )
            row = cur.fetchone()
            if row:
                job_id = row[0]
                cur.execute("DELETE FROM job_skills WHERE job_id = %s", (job_id,))
                cur.execute("DELETE FROM job_frameworks WHERE job_id = %s", (job_id,))
                if skill_ids:
                    psycopg2.extras.execute_values(
                        cur,
                        "INSERT INTO job_skills (job_id, skill_id) VALUES %s",
                        [(job_id, sid) for sid in skill_ids],
                    )
                if framework_ids:
                    psycopg2.extras.execute_values(
                        cur,
                        "INSERT INTO job_frameworks (job_id, framework_id) VALUES %s",
                        [(job_id, fid) for fid in framework_ids],
                    )
        conn.commit()


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


# ── Reads ─────────────────────────────────────────────────────────────────

def get_top_scored_jobs(
    top_k: int,
    min_score: int = 0,
    unscored_only: bool = True,
) -> list[dict]:
    """
    Return the top_k active jobs ordered by cheap-LLM fit score descending.

    Args:
        top_k:         Maximum number of jobs to return.
        min_score:     Only include jobs with t2_score >= this value.
        unscored_only: When True (default), exclude jobs that already have a
                       t3_qualification score so the expensive LLM is not run twice.
                       Pass False to re-score all qualifying jobs.
    """
    t3_filter = "AND t3_qualification IS NULL" if unscored_only else ""
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
                    t2_score, t2_explanation
                FROM jobs
                WHERE status = 'active'
                  AND t2_score IS NOT NULL
                  AND t2_score >= %(min_score)s
                  {t3_filter}
                ORDER BY t2_score DESC
                LIMIT %(top_k)s
                """,
                {"top_k": top_k, "min_score": min_score},
            )
            return [dict(row) for row in cur.fetchall()]


def get_active_t3_scored_jobs() -> list[dict]:
    """
    Return all active jobs that already have a t3_score (i.e. were previously
    deep-analysed).  Used by the rescore migration script to re-evaluate only
    jobs that have existing T3 data.
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
                    t2_score, t2_explanation
                FROM jobs
                WHERE status = 'active'
                  AND t3_score IS NOT NULL
                ORDER BY t2_score DESC NULLS LAST
                """
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


def fetch_jobs(
    where_clause: str,
    params: list,
    order_clause: str,
    page_size: int,
    offset: int,
) -> tuple[list[dict], int]:
    """
    Execute a pre-built filtered jobs query.  Returns (rows, total_count).

    where_clause  — a full "WHERE …" string (or empty string) with %s placeholders
    params        — positional values for the where_clause placeholders
    order_clause  — validated ORDER BY expression (no trailing keyword needed)
    """
    select_sql = f"""
        SELECT
            j.job_id, j.title, j.company_name, j.location, j.attendance, j.seniority,
            j.employment_type, j.salary_min, j.salary_max, j.salary_currency, j.salary_period,
            j.date_listed, j.status, j.url,
            j.t2_score, j.t2_explanation, j.t3_score,
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
                    j.t2_score, j.t2_explanation,
                    j.t3_score, j.t3_explanation, j.t3_qualification, j.t3_fit,
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
