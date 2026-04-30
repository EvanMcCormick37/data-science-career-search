"""
SerpAPI Google Jobs fetcher.

Two modes:
  daily    — fetches DAILY_MAX_PAGES pages per query, filtered to the past
             24 hours via chips=date_posted:today
  backfill — paginates up to BACKFILL_MAX_PAGES, filtered to the past ~month
             via chips=date_posted:month (the closest standard chip to 3 weeks;
             Google Jobs does not expose a 3-week filter)

The date chip is injected automatically from the mode.  A query dict entry
with a "chips" key overrides it, allowing per-query customisation from
queries.yaml.

Yields raw job dicts from SerpAPI's `jobs_results` array.  Each dict gets a
`serp_api_json` key added containing the full page response so downstream code
can always reprocess from the stored raw payload without re-hitting the API.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Generator, Literal

import yaml
from serpapi import GoogleSearch

from config.settings import (
    SERPAPI_KEY,
    QUERIES_PATH,
    DAILY_MAX_PAGES,
    BACKFILL_MAX_PAGES,
)

logger = logging.getLogger(__name__)

Mode = Literal["daily", "backfill"]

# Date-recency chips injected automatically per mode.
# Google Jobs only exposes fixed intervals; "month" is the closest to 3 weeks.
# Override per query in queries.yaml by setting a "chips" key.
_DATE_CHIPS: dict[str, str] = {
    "daily":    "date_posted:today",
    "backfill": "date_posted:month",
}


def load_queries() -> list[dict]:
    """Load and merge query definitions from queries.yaml."""
    with open(QUERIES_PATH) as f:
        data = yaml.safe_load(f)
    defaults = data.get("defaults", {})
    return [{**defaults, **q} for q in data.get("queries", [])]

def _make_params(query: dict) -> dict:
    """Build the SerpAPI params dict from a query entry."""
    params: dict = {"engine": "google_jobs", "api_key": SERPAPI_KEY}
    for key in ("q", "location", "gl", "hl", "lrad", "uds", "chips"):
        if query.get(key) is not None:
            params[key] = query[key]
    return params

# Lowercase substrings — any `via` field containing one of these is dropped.
_BLOCKED_SOURCES: frozenset[str] = frozenset({
    "ai salary map",
})


def _is_valid_job(job: dict) -> bool:
    """Return False for known non-job or spam sources."""
    
    via = job.get("via", "").lower()
    if any(blocked in via for blocked in _BLOCKED_SOURCES):
        return False
    
    desc = job.get("description","")
    if len(desc) < 1000:
        return False
    
    return True

def fetch_jobs(
    mode: Mode = "daily",
    queries: list[dict] | None = None,
    max_pages: int | None = None,
) -> Generator[dict, None, None]:
    """
    Yield raw SerpAPI job result dicts.

    Each yielded dict includes the original SerpAPI fields plus:
      serp_api_json — the full page response (for audit / reprocessing)

    Args:
        mode:      Controls the default page limit when max_pages is not given.
        queries:   Query dicts to run; loads from queries.yaml when None.
        max_pages: Override the mode default.  Useful for ad-hoc single queries.
    """
    if queries is None:
        queries = load_queries()

    if max_pages is None:
        max_pages = BACKFILL_MAX_PAGES if mode == "backfill" else DAILY_MAX_PAGES

    for query in queries:
        name = query.get("name", query.get("q", "unnamed"))
        logger.info(f"[{mode}] Fetching query: {name!r}")

        params = _make_params(query)
        # Inject the date chip when the query dict doesn't already supply one.
        if "chips" not in params and mode in _DATE_CHIPS:
            params["chips"] = _DATE_CHIPS[mode]
            logger.debug(f"  Date filter: chips={params['chips']!r}")

        for page in range(max_pages):
            try:
                response = GoogleSearch(params).get_dict()
            except Exception as exc:
                logger.error(f"  SerpAPI error on page {page + 1} of {name!r}: {exc}")
                break

            if "error" in response:
                logger.error(f"  SerpAPI error on page {page + 1} of {name!r}: {response['error']}")
                break

            jobs = response.get("jobs_results", [])
            logger.debug(f"  Page {page + 1}: {len(jobs)} jobs")

            for job in jobs:
                if _is_valid_job(job):
                    yield {**job, "serp_api_json": response}

            next_token = response.get("serpapi_pagination", {}).get("next_page_token")
            if not next_token:
                logger.debug(f" Last page.")
                break
            params["next_page_token"] = next_token
            time.sleep(0.5)  # gentle rate limiting

        logger.info(f"  Done: {name!r}")
