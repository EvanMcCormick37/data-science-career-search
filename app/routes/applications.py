"""
Application routes:
  GET   /applications               — list page (full or HTMX partial)
  GET   /applications/{id}/detail   — detail panel partial
  PATCH /applications/{id}          — update application, return row OOB partial
  GET   /applications/new           — new application form partial (requires job_id)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.services.applications import get_application_detail, list_applications, update_application
from app.services.jobs import get_job_detail, list_available_resumes
from app.templating import templates

router = APIRouter()


@router.get("/applications/new", response_class=HTMLResponse)
async def new_application_form(request: Request, job_id: int):
    from app.main import get_common_context

    job = get_job_detail(job_id)
    if job is None:
        return HTMLResponse("<p class='text-red-600 p-4'>Job not found.</p>", status_code=404)
    ctx = get_common_context(request)
    ctx["job"] = job
    ctx["resumes"] = list_available_resumes()
    return templates.TemplateResponse(request, "applications/_new_form.html", ctx)


@router.get("/applications", response_class=HTMLResponse)
async def applications_index(
    request: Request,
    states: Optional[list[str]] = Query(default=None),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    company: Optional[str] = None,
    assistance_level: Optional[list[str]] = Query(default=None),
    has_offer: Optional[str] = None,
    sort: str = "date_applied",
    page: int = 1,
    page_size: int = 50,
):
    from app.main import get_common_context

    states_list = states or []
    assistance_list = assistance_level or []
    has_offer_bool: Optional[bool] = None
    if has_offer == "yes":
        has_offer_bool = True
    elif has_offer == "no":
        has_offer_bool = False

    rows, total = list_applications(
        states=states_list,
        date_from=date_from,
        date_to=date_to,
        company=company,
        assistance_level=assistance_list,
        has_offer=has_offer_bool,
        sort=sort,
        page=page,
        page_size=page_size,
    )

    ctx = get_common_context(request)
    ctx.update(
        {
            "applications": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "sort": sort,
            "states": states_list,
            "assistance_level": assistance_list,
            "date_from": date_from or "",
            "date_to": date_to or "",
            "company": company or "",
            "has_offer": has_offer or "",
        }
    )

    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        return templates.TemplateResponse(request, "applications/_table.html", ctx)
    return templates.TemplateResponse(request, "applications/index.html", ctx)


@router.get("/applications/{application_id}/detail", response_class=HTMLResponse)
async def application_detail(request: Request, application_id: int):
    from app.main import get_common_context

    app_data = get_application_detail(application_id)
    if app_data is None:
        return HTMLResponse(
            "<p class='text-red-600 p-4'>Application not found.</p>", status_code=404
        )
    ctx = get_common_context(request)
    ctx["app"] = app_data
    ctx["resumes"] = list_available_resumes()
    return templates.TemplateResponse(request, "applications/_detail.html", ctx)


@router.patch("/applications/{application_id}", response_class=HTMLResponse)
async def patch_application(request: Request, application_id: int):
    from app.main import get_common_context

    form = await request.form()
    fields: dict = {}
    for key, value in form.items():
        if key in {
            "date_applied", "state", "assistance_level", "cover_letter", "resume",
            "cold_calls", "reached_human", "interviews", "offer",
        }:
            # Coerce numeric / boolean fields
            if key in {"cold_calls", "interviews"}:
                try:
                    fields[key] = int(value)
                except (ValueError, TypeError):
                    fields[key] = 0
            elif key in {"reached_human", "offer"}:
                fields[key] = 1 if value in ("1", "true", "on", "yes") else 0
            else:
                fields[key] = value

    update_application(application_id, **fields)

    app_data = get_application_detail(application_id)
    if app_data is None:
        return HTMLResponse(
            "<p class='text-red-600 p-4'>Application not found.</p>", status_code=404
        )
    ctx = get_common_context(request)
    ctx["app"] = app_data
    # Return OOB row (server sets hx-swap-oob on the tr element in the template)
    return templates.TemplateResponse(request, "applications/_row.html", ctx)
