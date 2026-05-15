"""
Read-only aggregation queries for the skills and frameworks dashboard tabs.
"""
from __future__ import annotations

import psycopg2.extras

from config.settings import CANDIDATE_MIN_JOBS
from db.connection import connection

_ORDER_MAP = {
    "relevance": "relevance DESC NULLS LAST",
    "count":     "count DESC",
    "avg_fit":   "avg_fit DESC NULLS LAST",
    "avg_qual":  "avg_qual DESC NULLS LAST",
}


def list_skills(
    *,
    name: str | None = None,
    sort: str = "relevance",
    show_candidates: bool = False,
) -> list[dict]:
    order = _ORDER_MAP.get(sort, _ORDER_MAP["relevance"])
    conditions = ["j.t3_score IS NOT NULL"]
    if not show_candidates:
        conditions.append("s.is_candidate = 0")
    if name:
        conditions.append("s.name ILIKE %(name_pattern)s")

    where = "WHERE " + " AND ".join(conditions)
    sql = f"""
        SELECT
            s.skill_id,
            s.name,
            COUNT(*)                                              AS count,
            ROUND(AVG(j.t3_fit)::numeric,           1)          AS avg_fit,
            ROUND(AVG(j.t3_qualification)::numeric,  1)          AS avg_qual,
            ROUND((AVG(j.t3_fit) * LN(COUNT(*)))::numeric, 1)   AS relevance
        FROM skills s
        INNER JOIN job_skills js ON s.skill_id = js.skill_id
        INNER JOIN jobs j        ON js.job_id  = j.job_id
        {where}
        GROUP BY s.skill_id, s.name
        HAVING COUNT(*) >= %(min_jobs)s
        ORDER BY {order}
    """
    params: dict = {"min_jobs": CANDIDATE_MIN_JOBS}
    if name:
        params["name_pattern"] = f"%{name}%"

    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


def list_frameworks(
    *,
    name: str | None = None,
    sort: str = "relevance",
    show_candidates: bool = False,
) -> list[dict]:
    order = _ORDER_MAP.get(sort, _ORDER_MAP["relevance"])
    conditions = ["j.t3_score IS NOT NULL"]
    if not show_candidates:
        conditions.append("f.is_candidate = 0")
    if name:
        conditions.append("f.name ILIKE %(name_pattern)s")

    where = "WHERE " + " AND ".join(conditions)
    sql = f"""
        SELECT
            f.framework_id,
            f.name,
            COUNT(*)                                              AS count,
            ROUND(AVG(j.t3_fit)::numeric,           1)          AS avg_fit,
            ROUND(AVG(j.t3_qualification)::numeric,  1)          AS avg_qual,
            ROUND((AVG(j.t3_fit) * LN(COUNT(*)))::numeric, 1)   AS relevance
        FROM frameworks f
        INNER JOIN job_frameworks jf ON f.framework_id = jf.framework_id
        INNER JOIN jobs j            ON jf.job_id      = j.job_id
        {where}
        GROUP BY f.framework_id, f.name
        HAVING COUNT(*) >= %(min_jobs)s
        ORDER BY {order}
    """
    params: dict = {"min_jobs": CANDIDATE_MIN_JOBS}
    if name:
        params["name_pattern"] = f"%{name}%"

    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
