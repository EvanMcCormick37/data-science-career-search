"""
SerpAPI Google Jobs fetcher.

Two modes:
  daily    — fetches DAILY_MAX_PAGES pages per query (new listings only)
  backfill — paginates up to BACKFILL_MAX_PAGES, persisting progress to a
             state file so interrupted runs resume where they left off

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
    BACKFILL_STATE_PATH,
    DAILY_MAX_PAGES,
    BACKFILL_MAX_PAGES,
)

logger = logging.getLogger(__name__)

Mode = Literal["daily", "backfill"]


def load_queries() -> list[dict]:
    """Load and merge query definitions from queries.yaml."""
    with open(QUERIES_PATH) as f:
        data = yaml.safe_load(f)
    defaults = data.get("defaults", {})
    return [{**defaults, **q} for q in data.get("queries", [])]


def _load_state() -> dict:
    if BACKFILL_STATE_PATH.exists():
        return json.loads(BACKFILL_STATE_PATH.read_text())
    return {}


def _save_state(state: dict) -> None:
    BACKFILL_STATE_PATH.write_text(json.dumps(state, indent=2))


def _make_params(query: dict) -> dict:
    """Build the SerpAPI params dict from a query entry."""
    params: dict = {"engine": "google_jobs", "api_key": SERPAPI_KEY}
    for key in ("q", "location", "gl", "hl", "lrad", "uds"):
        if query.get(key) is not None:
            params[key] = query[key]
    return params


def fetch_jobs(
    mode: Mode = "daily",
    queries: list[dict] | None = None,
) -> Generator[dict, None, None]:
    """
    Yield raw SerpAPI job result dicts.

    Each yielded dict includes the original SerpAPI fields plus:
      serp_api_json — the full page response (for audit / reprocessing)
    """
    if queries is None:
        queries = load_queries()

    state     = _load_state() if mode == "backfill" else {}
    max_pages = BACKFILL_MAX_PAGES if mode == "backfill" else DAILY_MAX_PAGES

    for query in queries:
        name = query.get("name", query.get("q", "unnamed"))
        logger.info(f"[{mode}] Fetching query: {name!r}")

        if mode == "backfill" and state.get(name) == "done":
            logger.debug(f"  Skipping completed query: {name!r}")
            continue

        params = _make_params(query)

        # Resume from saved pagination token if this query was interrupted
        if mode == "backfill":
            saved_token = state.get(f"{name}:next_page_token")
            if saved_token:
                params["next_page_token"] = saved_token

        for page in range(max_pages):
            try:
                response = GoogleSearch(params).get_dict()
            except Exception as exc:
                logger.error(f"  SerpAPI error on page {page + 1} of {name!r}: {exc}")
                break

            if "error" in response:
                logger.error(f"  SerpAPI error: {response['error']}")
                break

            jobs = response.get("jobs_results", [])
            logger.debug(f"  Page {page + 1}: {len(jobs)} jobs")

            for job in jobs:
                yield {**job, "serp_api_json": response}

            next_token = response.get("serpapi_pagination", {}).get("next_page_token")
            if not next_token:
                if mode == "backfill":
                    state[name] = "done"
                    _save_state(state)
                break

            params["next_page_token"] = next_token
            if mode == "backfill":
                state[f"{name}:next_page_token"] = next_token
                _save_state(state)

            time.sleep(0.5)  # gentle rate limiting

        logger.info(f"  Done: {name!r}")
