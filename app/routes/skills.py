"""
Skills and Frameworks tab routes:
  GET /skills      — skills aggregation table
  GET /frameworks  — frameworks aggregation table
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.templating import templates
from db.skills import list_frameworks, list_skills

router = APIRouter()

_VALID_SORTS = {"relevance", "count", "avg_fit", "avg_qual"}


@router.get("/skills", response_class=HTMLResponse)
async def skills_index(
    request: Request,
    name: str = "",
    sort: str = "relevance",
    show_candidates: bool = False,
):
    from app.main import get_common_context

    if sort not in _VALID_SORTS:
        sort = "relevance"

    rows = list_skills(name=name or None, sort=sort, show_candidates=show_candidates)

    ctx = {
        **get_common_context(request),
        "rows": rows,
        "name": name,
        "sort": sort,
        "show_candidates": show_candidates,
        "page_name": "skills",
        "action": "/skills",
        "label": "Skills",
    }
    template = "keywords/_table.html" if request.headers.get("hx-request") else "keywords/index.html"
    return templates.TemplateResponse(template, ctx)


@router.get("/frameworks", response_class=HTMLResponse)
async def frameworks_index(
    request: Request,
    name: str = "",
    sort: str = "relevance",
    show_candidates: bool = False,
):
    from app.main import get_common_context

    if sort not in _VALID_SORTS:
        sort = "relevance"

    rows = list_frameworks(name=name or None, sort=sort, show_candidates=show_candidates)

    ctx = {
        **get_common_context(request),
        "rows": rows,
        "name": name,
        "sort": sort,
        "show_candidates": show_candidates,
        "page_name": "frameworks",
        "action": "/frameworks",
        "label": "Frameworks",
    }
    template = "keywords/_table.html" if request.headers.get("hx-request") else "keywords/index.html"
    return templates.TemplateResponse(template, ctx)
