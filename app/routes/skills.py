"""
Skills and Frameworks tab routes:
  GET   /skills                  — skills aggregation table
  GET   /frameworks              — frameworks aggregation table
  PATCH /skills/{id}/level       — update user_skill_level
  PATCH /frameworks/{id}/level   — update user_skill_level
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.templating import templates
from db.skills import list_frameworks, list_skills, set_framework_level, set_skill_level

router = APIRouter()

_VALID_SORTS = {
    "improvement_priority",
    "avg_salary",
    "avg_fit",
    "avg_qual",
    "raw_count",
    "actionable_count",
}


def _skills_ctx(request, rows, *, name, sort, show_candidates, listing_mode,
                date_listed_from, date_listed_to, page_name, action, label):
    from app.main import get_common_context
    return {
        **get_common_context(request),
        "rows": rows,
        "name": name,
        "sort": sort,
        "show_candidates": show_candidates,
        "listing_mode": listing_mode,
        "date_listed_from": date_listed_from or "",
        "date_listed_to": date_listed_to or "",
        "page_name": page_name,
        "action": action,
        "label": label,
    }


@router.get("/skills", response_class=HTMLResponse)
async def skills_index(
    request: Request,
    name: str = "",
    sort: str = "improvement_priority",
    show_candidates: bool = False,
    listing_mode: str = "active",
    date_listed_from: Optional[str] = None,
    date_listed_to: Optional[str] = None,
):
    if sort not in _VALID_SORTS:
        sort = "improvement_priority"

    rows = list_skills(
        name=name or None,
        sort=sort,
        show_candidates=show_candidates,
        listing_mode=listing_mode,
        date_listed_from=date_listed_from or None,
        date_listed_to=date_listed_to or None,
    )
    ctx = _skills_ctx(
        request, rows,
        name=name, sort=sort, show_candidates=show_candidates,
        listing_mode=listing_mode,
        date_listed_from=date_listed_from, date_listed_to=date_listed_to,
        page_name="skills", action="/skills", label="Skills",
    )
    template = "keywords/_table.html" if request.headers.get("hx-request") else "keywords/index.html"
    return templates.TemplateResponse(request, template, ctx)


@router.get("/frameworks", response_class=HTMLResponse)
async def frameworks_index(
    request: Request,
    name: str = "",
    sort: str = "improvement_priority",
    show_candidates: bool = False,
    listing_mode: str = "active",
    date_listed_from: Optional[str] = None,
    date_listed_to: Optional[str] = None,
):
    if sort not in _VALID_SORTS:
        sort = "improvement_priority"

    rows = list_frameworks(
        name=name or None,
        sort=sort,
        show_candidates=show_candidates,
        listing_mode=listing_mode,
        date_listed_from=date_listed_from or None,
        date_listed_to=date_listed_to or None,
    )
    ctx = _skills_ctx(
        request, rows,
        name=name, sort=sort, show_candidates=show_candidates,
        listing_mode=listing_mode,
        date_listed_from=date_listed_from, date_listed_to=date_listed_to,
        page_name="frameworks", action="/frameworks", label="Frameworks",
    )
    template = "keywords/_table.html" if request.headers.get("hx-request") else "keywords/index.html"
    return templates.TemplateResponse(request, template, ctx)


@router.patch("/skills/{skill_id}/level", response_class=HTMLResponse)
async def patch_skill_level(request: Request, skill_id: int):
    form = await request.form()
    level = form.get("skill_level") or None
    try:
        set_skill_level(skill_id, level)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    return HTMLResponse("")


@router.patch("/frameworks/{framework_id}/level", response_class=HTMLResponse)
async def patch_framework_level(request: Request, framework_id: int):
    form = await request.form()
    level = form.get("skill_level") or None
    try:
        set_framework_level(framework_id, level)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    return HTMLResponse("")
