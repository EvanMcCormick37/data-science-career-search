"""
Job routes:
  GET  /jobs               — list page (full or HTMX partial)
  GET  /jobs/{id}/detail   — detail panel partial
  PATCH /jobs/{id}/status  — update status, return row partial for OOB swap
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.services.jobs import get_job_detail, list_jobs
from app.templating import templates
from db.operations import update_job_status

router = APIRouter()


def _parse_list(val) -> list[str]:
    """Coerce a Query value that may be a list or a single string to list[str]."""
    if val is None:
        return []
    if isinstance(val, list):
        return [v for v in val if v]
    return [val] if val else []


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_index(
    request: Request,
    statuses: Optional[list[str]] = None,
    tier2_min: Optional[float] = None,
    tier3_min: Optional[float] = None,
    seniority: Optional[list[str]] = None,
    attendance: Optional[list[str]] = None,
    location: Optional[str] = None,
    title: Optional[str] = None,
    company: Optional[str] = None,
    description: Optional[str] = None,
    sort: str = "tier2_score",
    page: int = 1,
    page_size: int = 50,
):
    from app.main import get_common_context

    # FastAPI handles multi-value query params as lists when annotated with list[str]
    statuses_list = statuses or ["active"]
    seniority_list = seniority or []
    attendance_list = attendance or []

    rows, total = list_jobs(
        statuses=statuses_list,
        tier2_min=tier2_min,
        tier3_min=tier3_min,
        seniority=seniority_list,
        attendance=attendance_list,
        location=location,
        title=title,
        company=company,
        description=description,
        sort=sort,
        page=page,
        page_size=page_size,
    )

    ctx = get_common_context(request)
    ctx.update(
        {
            "jobs": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "sort": sort,
            "statuses": statuses_list,
            "seniority": seniority_list,
            "attendance": attendance_list,
            "tier2_min": tier2_min,
            "tier3_min": tier3_min,
            "location": location or "",
            "title": title or "",
            "company": company or "",
            "description": description or "",
        }
    )

    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        return templates.TemplateResponse("jobs/_table.html", ctx)
    return templates.TemplateResponse("jobs/index.html", ctx)


@router.get("/jobs/{job_id}/detail", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: int):
    from app.main import get_common_context

    job = get_job_detail(job_id)
    if job is None:
        return HTMLResponse("<p class='text-red-600 p-4'>Job not found.</p>", status_code=404)
    ctx = get_common_context(request)
    ctx["job"] = job
    return templates.TemplateResponse("jobs/_detail.html", ctx)


@router.patch("/jobs/{job_id}/status", response_class=HTMLResponse)
async def patch_job_status(request: Request, job_id: int):
    from app.main import get_common_context

    form = await request.form()
    status = form.get("status", "")
    try:
        update_job_status(job_id, status)
    except ValueError as exc:
        return HTMLResponse(f"<p class='text-red-600 p-4'>{exc}</p>", status_code=422)

    job = get_job_detail(job_id)
    if job is None:
        return HTMLResponse("<p class='text-red-600 p-4'>Job not found.</p>", status_code=404)
    ctx = get_common_context(request)
    ctx["job"] = job
    return templates.TemplateResponse("jobs/_row.html", ctx)
