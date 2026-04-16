"""
Action routes:
  POST /applications — log a new application, redirect to its detail page.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services.applications import log_application

router = APIRouter()


@router.post("/applications", response_class=HTMLResponse)
async def create_application(request: Request):
    form = await request.form()

    def _int(key: str, default: int = 0) -> int:
        try:
            return int(form.get(key, default))
        except (ValueError, TypeError):
            return default

    def _bool_int(key: str) -> int:
        return 1 if form.get(key) in ("1", "true", "on", "yes") else 0

    application_id = log_application(
        job_id=int(form["job_id"]),
        date_applied=form.get("date_applied") or None,
        state=form.get("state", "submitted"),
        assistance_level=form.get("assistance_level") or None,
        cover_letter=form.get("cover_letter") or None,
        resume=form.get("resume") or None,
        cold_calls=_int("cold_calls"),
        reached_human=_bool_int("reached_human"),
        interviews=_int("interviews"),
        offer=_bool_int("offer"),
    )

    redirect_url = f"/applications/{application_id}/detail"

    # For HTMX callers, use HX-Redirect header so the browser navigates
    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        return HTMLResponse(
            content="",
            status_code=200,
            headers={"HX-Redirect": redirect_url},
        )

    return RedirectResponse(url=redirect_url, status_code=303)
