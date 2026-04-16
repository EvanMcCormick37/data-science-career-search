"""
Config editor routes:
  GET  /config          — landing page
  GET  /config/queries  — queries editor
  POST /config/queries  — save queries
  GET  /config/profile  — career profile editor
  POST /config/profile  — save career profile
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services.config_files import (
    read_career_profile,
    read_queries,
    write_career_profile,
    write_queries,
)
from app.templating import templates

router = APIRouter()


@router.get("/config", response_class=HTMLResponse)
async def config_index(request: Request):
    from app.main import get_common_context
    ctx = get_common_context(request)
    return templates.TemplateResponse("config/index.html", ctx)


@router.get("/config/queries", response_class=HTMLResponse)
async def config_queries_get(request: Request):
    from app.main import get_common_context
    ctx = get_common_context(request)
    ctx["queries"] = read_queries()
    return templates.TemplateResponse("config/queries.html", ctx)


@router.post("/config/queries", response_class=HTMLResponse)
async def config_queries_post(request: Request):
    form = await request.form()
    raw = form.get("queries_json", "[]")
    try:
        queries = json.loads(raw)
    except (ValueError, TypeError):
        queries = []
    write_queries(queries)
    return RedirectResponse(url="/config/queries", status_code=303)


@router.get("/config/profile", response_class=HTMLResponse)
async def config_profile_get(request: Request):
    from app.main import get_common_context
    ctx = get_common_context(request)
    try:
        ctx["profile_text"] = read_career_profile()
    except FileNotFoundError:
        ctx["profile_text"] = ""
    return templates.TemplateResponse("config/career_profile.html", ctx)


@router.post("/config/profile", response_class=HTMLResponse)
async def config_profile_post(request: Request):
    form = await request.form()
    text = form.get("profile_text", "")
    write_career_profile(text)
    return RedirectResponse(url="/config/profile", status_code=303)
