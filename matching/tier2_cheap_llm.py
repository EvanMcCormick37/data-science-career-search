"""
Tier 2 — Cheap LLM relevance scoring via OpenRouter.

Scores all Tier 1 candidates concurrently using asyncio + httpx.
Each call asks the LLM to score career_profile-job fit on 0–100 and provide a
one-sentence explanation.  Results are stored on the jobs table for later
inspection and to avoid re-scoring on repeated query runs.

Concurrency is bounded by TIER2_CONCURRENCY (default 10) to stay within
the rate limits of cheap models.  100 calls typically completes in ~15–30s
at negligible cost (<$0.01).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Sequence

import httpx

from config.settings import SCORING_MODEL, TIER2_CONCURRENCY, TIER2_TOP_N
from db.jobs import update_tier2_scores
from llm.client import async_complete_json
from matching.scoring import SYSTEM_PROMPT, format_user_message

logger = logging.getLogger(__name__)


async def _score_one(
    job: dict,
    career_profile_text: str,
    semaphore: asyncio.Semaphore,
    http_client: httpx.AsyncClient,
) -> dict:
    """Score a single job.  Returns the job dict with score/explanation added."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": format_user_message(career_profile_text, job)},
    ]
    async with semaphore:
        try:
            result = await async_complete_json(
                SCORING_MODEL,
                messages,
                temperature=0.0,
                max_tokens=256,
                client=http_client,
            )
            score       = int(result.get("score", 0))
            explanation = str(result.get("explanation", ""))
        except Exception as exc:
            logger.warning(f"Tier 2 scoring failed for job {job.get('job_id')}: {exc}")
            score       = 0
            explanation = f"Scoring error: {exc}"

    return {**job, "t2_score": score, "t2_explanation": explanation}


async def _score_all(
    jobs: list[dict],
    career_profile_text: str,
) -> list[dict]:
    semaphore   = asyncio.Semaphore(TIER2_CONCURRENCY)
    async with httpx.AsyncClient(timeout=60) as http_client:
        tasks = [
            _score_one(job, career_profile_text, semaphore, http_client)
            for job in jobs
        ]
        scored = await asyncio.gather(*tasks)
    return list(scored)


def score_batch(
    jobs: Sequence[dict],
    career_profile_text: str,
    *,
    persist: bool = True,
    top_k: int = TIER2_TOP_N,
) -> list[dict]:
    """
    Score all jobs in the batch concurrently, persist scores to the DB,
    and return the top_k by score (descending).

    Args:
        jobs:        Tier 1 candidate job dicts (must include 'job_id').
        career_profile_text: Full career_profile text used in the scoring prompt.
        persist:     If True, write t2_score + t2_explanation to the DB.
        top_k:       Number of top-scoring jobs to return for Tier 3.

    Returns:
        List of job dicts sorted by t2_score descending, truncated to top_k.
    """
    logger.info(f"Tier 2: scoring {len(jobs)} candidates with {SCORING_MODEL!r} …")
    scored = asyncio.run(_score_all(list(jobs), career_profile_text))

    if persist:
        for job in scored:
            if job.get("job_id") and job.get("t2_score") is not None:
                update_tier2_scores(
                    job["job_id"],
                    job["t2_score"],
                    job.get("t2_explanation", ""),
                )

    scored.sort(key=lambda j: j.get("t2_score", 0), reverse=True)
    top = scored[:top_k]

    logger.info(
        f"Tier 2: top score={top[0]['t2_score']} for "
        f"{top[0].get('title')!r} @ {top[0].get('company_name')!r}"
        if top else "Tier 2: no results"
    )
    return top
