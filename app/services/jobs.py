"""
Jobs service layer — owns dashboard query composition and resume listing.
"""
from __future__ import annotations

import os

from config.settings import RESUMES_DIR
from db.jobs import fetch_jobs, get_job_detail

__all__ = ["list_jobs", "get_job_detail", "list_available_resumes"]

_VALID_JOB_SORTS = {
    "t2_score":    "j.t2_score DESC NULLS LAST",
    "t3_score":    "j.t3_score DESC NULLS LAST",
    "date_listed": "j.date_listed DESC NULLS LAST",
    "salary_max":  "j.salary_max DESC NULLS LAST",
}


def list_jobs(
    *,
    statuses: list[str] | None = None,
    t2_min: float | None = None,
    t3_min: float | None = None,
    seniority: list[str] | None = None,
    attendance: list[str] | None = None,
    location: str | None = None,
    title: str | None = None,
    company: str | None = None,
    description: str | None = None,
    date_listed_from: str | None = None,
    date_listed_to: str | None = None,
    has_application: bool | None = None,
    sort: str = "t2_score",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict], int]:
    """
    Build a filtered/sorted/paginated job query and execute it.
    Returns (rows, total_count).
    """
    conditions: list[str] = []
    params: list = []

    # Status filter — always exclude internal-only statuses
    if statuses:
        safe_statuses = [s for s in statuses if s not in ("extraction_failed", "duplicate")]
    else:
        safe_statuses = ["active"]
    placeholders = ", ".join(["%s"] * len(safe_statuses))
    conditions.append(f"j.status IN ({placeholders})")
    params.extend(safe_statuses)

    if t2_min is not None:
        conditions.append("j.t2_score >= %s")
        params.append(t2_min)
    if t3_min is not None:
        conditions.append("j.t3_score >= %s")
        params.append(t3_min)
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
        conditions.append(
            "(j.description ILIKE %s OR j.qualifications ILIKE %s OR j.responsibilities ILIKE %s)"
        )
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

    where_clause  = "WHERE " + " AND ".join(conditions) if conditions else ""
    order_clause  = _VALID_JOB_SORTS.get(sort, _VALID_JOB_SORTS["t2_score"])
    offset        = (page - 1) * page_size

    return fetch_jobs(where_clause, params, order_clause, page_size, offset)


def list_available_resumes() -> list[str]:
    """Return sorted list of resume filenames in RESUMES_DIR. Empty list if dir missing."""
    try:
        entries = os.listdir(RESUMES_DIR)
    except FileNotFoundError:
        return []
    files = [e for e in entries if os.path.isfile(os.path.join(RESUMES_DIR, e))]
    return sorted(files)
