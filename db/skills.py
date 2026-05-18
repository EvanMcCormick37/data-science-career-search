"""
Read-only aggregation queries for the skills and frameworks dashboard tabs,
plus skill-level update helpers.
"""
from __future__ import annotations

import psycopg2.extras

from config.settings import CANDIDATE_MIN_JOBS
from db.connection import connection

_ORDER_MAP = {
    "improvement_priority": "improvement_priority DESC NULLS LAST",
    "avg_salary":           "avg_salary DESC NULLS LAST",
    "avg_fit":              "avg_fit DESC NULLS LAST",
    "avg_qual":             "avg_qual DESC NULLS LAST",
    "raw_count":            "raw_count DESC",
    "actionable_count":     "actionable_count DESC",
}

_VALID_LEVELS = {"Unskilled", "Novice", "Intermediate", "Advanced", "Expert"}

_SKILL_LEVEL_ORDINAL = """
    CASE s.user_skill_level
        WHEN 'Unskilled'    THEN 5
        WHEN 'Novice'       THEN 4
        WHEN 'Intermediate' THEN 3
        WHEN 'Advanced'     THEN 2
        WHEN 'Expert'       THEN 1
        ELSE                     5
    END
"""

_FRAMEWORK_LEVEL_ORDINAL = """
    CASE f.user_skill_level
        WHEN 'Unskilled'    THEN 5
        WHEN 'Novice'       THEN 4
        WHEN 'Intermediate' THEN 3
        WHEN 'Advanced'     THEN 2
        WHEN 'Expert'       THEN 1
        ELSE                     5
    END
"""


def list_skills(
    *,
    name: str | None = None,
    sort: str = "improvement_priority",
    show_candidates: bool = False,
    listing_mode: str = "active",
    date_listed_from: str | None = None,
    date_listed_to: str | None = None,
) -> list[dict]:
    order = _ORDER_MAP.get(sort, _ORDER_MAP["improvement_priority"])

    # Job-level conditions shared by both the global salary CTE and the main query
    job_conditions = ["j.t3_score IS NOT NULL"]
    if listing_mode == "active":
        job_conditions.append("j.status = 'active'")
    else:
        job_conditions.append("j.status IN ('active', 'applied', 'closed', 'expired')")
    if date_listed_from:
        job_conditions.append("j.date_listed >= %(date_listed_from)s")
    if date_listed_to:
        job_conditions.append("j.date_listed <= %(date_listed_to)s")

    # Entity-level conditions added only to the main query
    conditions = list(job_conditions)
    if not show_candidates:
        conditions.append("s.is_candidate = 0")
    if name:
        conditions.append("s.name ILIKE %(name_pattern)s")

    global_where = "WHERE " + " AND ".join(job_conditions)
    where = "WHERE " + " AND ".join(conditions)

    sql = f"""
        WITH global_salary AS (
            SELECT AVG(salary_max) FILTER (WHERE salary_period = 'yearly') AS avg
            FROM jobs j
            {global_where}
        )
        SELECT
            s.skill_id,
            s.name,
            s.user_skill_level,
            COUNT(*)                                                                            AS raw_count,
            COUNT(*) FILTER (WHERE j.t3_qualification >= 50 AND j.t3_qualification < 80)       AS actionable_count,
            ROUND(AVG(j.t3_fit)::numeric,           1)                                         AS avg_fit,
            ROUND(AVG(j.t3_qualification)::numeric,  1)                                        AS avg_qual,
            ROUND(COALESCE(
                AVG(CASE WHEN j.salary_period = 'yearly' THEN j.salary_max END),
                (SELECT avg FROM global_salary)
            )::numeric, 0)                                                                      AS avg_salary,
            CASE
                WHEN COUNT(*) FILTER (WHERE j.t3_qualification >= 50 AND j.t3_qualification < 80) = 0 THEN NULL
                ELSE ROUND((LN(
                    COALESCE(
                        AVG(CASE WHEN j.salary_period = 'yearly' THEN j.salary_max END),
                        (SELECT avg FROM global_salary)
                    ) *
                    AVG(j.t3_fit) *
                    COUNT(*) FILTER (WHERE j.t3_qualification >= 50 AND j.t3_qualification < 80) *
                    ({_SKILL_LEVEL_ORDINAL})
                ) - 15)::numeric, 1)
            END AS improvement_priority
        FROM skills s
        INNER JOIN job_skills js ON s.skill_id = js.skill_id
        INNER JOIN jobs j        ON js.job_id  = j.job_id
        {where}
        GROUP BY s.skill_id, s.name, s.user_skill_level
        HAVING COUNT(*) >= %(min_jobs)s
        ORDER BY {order}
    """
    params: dict = {"min_jobs": CANDIDATE_MIN_JOBS}
    if name:
        params["name_pattern"] = f"%{name}%"
    if date_listed_from:
        params["date_listed_from"] = date_listed_from
    if date_listed_to:
        params["date_listed_to"] = date_listed_to

    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [{**dict(row), "id": row["skill_id"]} for row in cur.fetchall()]


def list_frameworks(
    *,
    name: str | None = None,
    sort: str = "improvement_priority",
    show_candidates: bool = False,
    listing_mode: str = "active",
    date_listed_from: str | None = None,
    date_listed_to: str | None = None,
) -> list[dict]:
    order = _ORDER_MAP.get(sort, _ORDER_MAP["improvement_priority"])

    job_conditions = ["j.t3_score IS NOT NULL"]
    if listing_mode == "active":
        job_conditions.append("j.status = 'active'")
    else:
        job_conditions.append("j.status IN ('active', 'applied', 'closed', 'expired')")
    if date_listed_from:
        job_conditions.append("j.date_listed >= %(date_listed_from)s")
    if date_listed_to:
        job_conditions.append("j.date_listed <= %(date_listed_to)s")

    conditions = list(job_conditions)
    if not show_candidates:
        conditions.append("f.is_candidate = 0")
    if name:
        conditions.append("f.name ILIKE %(name_pattern)s")

    global_where = "WHERE " + " AND ".join(job_conditions)
    where = "WHERE " + " AND ".join(conditions)

    sql = f"""
        WITH global_salary AS (
            SELECT AVG(salary_max) FILTER (WHERE salary_period = 'yearly') AS avg
            FROM jobs j
            {global_where}
        )
        SELECT
            f.framework_id,
            f.name,
            f.user_skill_level,
            COUNT(*)                                                                            AS raw_count,
            COUNT(*) FILTER (WHERE j.t3_qualification >= 50 AND j.t3_qualification < 80)       AS actionable_count,
            ROUND(AVG(j.t3_fit)::numeric,           1)                                         AS avg_fit,
            ROUND(AVG(j.t3_qualification)::numeric,  1)                                        AS avg_qual,
            ROUND(COALESCE(
                AVG(CASE WHEN j.salary_period = 'yearly' THEN j.salary_max END),
                (SELECT avg FROM global_salary)
            )::numeric, 0)                                                                      AS avg_salary,
            CASE
                WHEN COUNT(*) FILTER (WHERE j.t3_qualification >= 50 AND j.t3_qualification < 80) = 0 THEN NULL
                ELSE ROUND((LN(
                    COALESCE(
                        AVG(CASE WHEN j.salary_period = 'yearly' THEN j.salary_max END),
                        (SELECT avg FROM global_salary)
                    ) *
                    AVG(j.t3_fit) *
                    COUNT(*) FILTER (WHERE j.t3_qualification >= 50 AND j.t3_qualification < 80) *
                    ({_FRAMEWORK_LEVEL_ORDINAL})
                ) - 15)::numeric, 1)
            END AS improvement_priority
        FROM frameworks f
        INNER JOIN job_frameworks jf ON f.framework_id = jf.framework_id
        INNER JOIN jobs j            ON jf.job_id      = j.job_id
        {where}
        GROUP BY f.framework_id, f.name, f.user_skill_level
        HAVING COUNT(*) >= %(min_jobs)s
        ORDER BY {order}
    """
    params: dict = {"min_jobs": CANDIDATE_MIN_JOBS}
    if name:
        params["name_pattern"] = f"%{name}%"
    if date_listed_from:
        params["date_listed_from"] = date_listed_from
    if date_listed_to:
        params["date_listed_to"] = date_listed_to

    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [{**dict(row), "id": row["framework_id"]} for row in cur.fetchall()]


def set_skill_level(skill_id: int, level: str | None) -> None:
    if level and level not in _VALID_LEVELS:
        raise ValueError(f"Invalid skill level: {level!r}")
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE skills SET user_skill_level = %s WHERE skill_id = %s",
                (level or None, skill_id),
            )


def set_framework_level(framework_id: int, level: str | None) -> None:
    if level and level not in _VALID_LEVELS:
        raise ValueError(f"Invalid skill level: {level!r}")
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE frameworks SET user_skill_level = %s WHERE framework_id = %s",
                (level or None, framework_id),
            )
