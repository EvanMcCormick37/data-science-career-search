"""SQL operations for the applications table."""
from __future__ import annotations

import logging

import psycopg2.extras

from db.connection import connection

logger = logging.getLogger(__name__)

_ASSISTANCE_LEVELS = {"ai", "assisted", "human"}

_VALID_APP_SORTS = {
    "date_applied": "a.date_applied DESC NULLS LAST",
    "state":        "CASE a.state WHEN 'offer' THEN 0 WHEN 'interviewing' THEN 1 WHEN 'submitted' THEN 2 WHEN 'rejected' THEN 3 ELSE 4 END ASC",
    "company_name": "j.company_name ASC",
}


# ── Writes ────────────────────────────────────────────────────────────────

def create_application(
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
    Insert a new application row, set jobs.application_id and jobs.status = 'applied'.
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

    logger.debug(f"Created application_id={application_id} for job_id={job_id}")
    return application_id


def update_application(application_id: int, **fields) -> None:
    """
    Update one or more fields on an existing application.

    Only the keys supplied in **fields are touched; everything else is left as-is.
    If state is set to 'offer', offer is also set to 1 to keep them in sync.
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


# ── Reads ─────────────────────────────────────────────────────────────────

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
    """Return all applications joined with basic job info, ordered by date_applied desc."""
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
            j.title AS job_title, j.company_name, j.t2_score, j.t3_score
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
                    j.t2_score, j.t3_score, j.url AS job_url, j.status AS job_status
                FROM applications a
                JOIN jobs j ON a.job_id = j.job_id
                WHERE a.application_id = %s
                """,
                (application_id,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


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
