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

    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        # Fire applicationCreated event (caught by app.js to open /applications in new tab)
        # Return a success message for the detail panel
        content = (
            '<div class="p-4 flex flex-col gap-3 text-sm">'
            '<p class="text-green-700 font-semibold">✓ Application logged.</p>'
            f'<a href="/applications" target="_blank" '
            f'class="text-blue-600 hover:text-blue-800 underline">Open Applications →</a>'
            '</div>'
        )
        return HTMLResponse(content=content, status_code=200, headers={"HX-Trigger": "applicationCreated"})

    return RedirectResponse(url="/applications", status_code=303)
