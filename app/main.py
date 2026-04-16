"""
FastAPI application entry point for the job-search dashboard.
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from db.operations import get_freshness_stats

app = FastAPI(title="Job Search Dashboard")

# Mount static files
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)

# ── Freshness cache (60-second TTL) ──────────────────────────────────────────
_freshness_cache: dict = {"data": None, "ts": 0.0}


def get_common_context(request: Request) -> dict:
    """Return a dict suitable for injecting into every template context."""
    now = time.monotonic()
    if _freshness_cache["data"] is None or now - _freshness_cache["ts"] > 60:
        _freshness_cache["data"] = get_freshness_stats()
        _freshness_cache["ts"] = now
    return {
        "request": request,
        "freshness": _freshness_cache["data"],
    }


# ── Routes ────────────────────────────────────────────────────────────────────
from app.routes import jobs, applications, actions, config_editor  # noqa: E402

app.include_router(jobs.router)
app.include_router(applications.router)
app.include_router(actions.router)
app.include_router(config_editor.router)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/jobs")
