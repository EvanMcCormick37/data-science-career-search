"""
Tier 2 — Cheap LLM relevance scoring via OpenRouter.

Scores all Tier 1 candidates concurrently using asyncio + httpx.
Each call asks the LLM to score resume-job fit on 0–100 and provide a
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
from db.operations import update_tier2_scores
from llm.client import async_complete_json

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a job-fit evaluator. Given a resume and a job listing, "
    "score the match from 0 to 100 and provide a single concise sentence explaining "
    "the primary reason for the score. "
    "Respond ONLY with valid JSON: {\"score\": <integer 0-100>, \"explanation\": <string>}"
)


def _format_user_message(resume_text: str, job: dict) -> str:
    salary_str = ""
    if job.get("salary_min") and job.get("salary_max"):
        period = job.get("salary_period") or "yearly"
        currency = job.get("salary_currency") or "USD"
        salary_str = f"\nSalary: {currency} {job['salary_min']:,}–{job['salary_max']:,} ({period})"

    return (
        f"RESUME:\n{resume_text}\n\n"
        f"JOB:\n"
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company_name', '')}\n"
        f"Location: {job.get('location', '')} | "
        f"Attendance: {job.get('attendance') or 'unknown'} | "
        f"Seniority: {job.get('seniority') or 'unknown'}"
        f"{salary_str}\n"
        f"Description: {(job.get('description') or '')[:1500]}\n"
        f"Qualifications: {(job.get('qualifications') or '')[:800]}"
    )


async def _score_one(
    job: dict,
    resume_text: str,
    semaphore: asyncio.Semaphore,
    http_client: httpx.AsyncClient,
) -> dict:
    """Score a single job.  Returns the job dict with score/explanation added."""
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user",   "content": _format_user_message(resume_text, job)},
    ]
    async with semaphore:
        try:
            result = await async_complete_json(
                SCORING_MODEL,
                messages,
                temperature=0.0,
                max_tokens=128,
                client=http_client,
            )
            score       = int(result.get("score", 0))
            explanation = str(result.get("explanation", ""))
        except Exception as exc:
            logger.warning(f"Tier 2 scoring failed for job {job.get('job_id')}: {exc}")
            score       = 0
            explanation = f"Scoring error: {exc}"

    return {**job, "tier2_score": score, "tier2_explanation": explanation}


async def _score_all(
    jobs: list[dict],
    resume_text: str,
) -> list[dict]:
    semaphore   = asyncio.Semaphore(TIER2_CONCURRENCY)
    async with httpx.AsyncClient(timeout=60) as http_client:
        tasks = [
            _score_one(job, resume_text, semaphore, http_client)
            for job in jobs
        ]
        scored = await asyncio.gather(*tasks)
    return list(scored)


def score_batch(
    jobs: Sequence[dict],
    resume_text: str,
    *,
    persist: bool = True,
    top_n: int = TIER2_TOP_N,
) -> list[dict]:
    """
    Score all jobs in the batch concurrently, persist scores to the DB,
    and return the top_n by score (descending).

    Args:
        jobs:        Tier 1 candidate job dicts (must include 'job_id').
        resume_text: Full resume text used in the scoring prompt.
        persist:     If True, write tier2_score + tier2_explanation to the DB.
        top_n:       Number of top-scoring jobs to return for Tier 3.

    Returns:
        List of job dicts sorted by tier2_score descending, truncated to top_n.
    """
    logger.info(f"Tier 2: scoring {len(jobs)} candidates with {SCORING_MODEL!r} …")
    scored = asyncio.run(_score_all(list(jobs), resume_text))

    if persist:
        for job in scored:
            if job.get("job_id") and job.get("tier2_score") is not None:
                update_tier2_scores(
                    job["job_id"],
                    job["tier2_score"],
                    job.get("tier2_explanation", ""),
                )

    scored.sort(key=lambda j: j.get("tier2_score", 0), reverse=True)
    top = scored[:top_n]

    logger.info(
        f"Tier 2: top score={top[0]['tier2_score']} for "
        f"{top[0].get('title')!r} @ {top[0].get('company_name')!r}"
        if top else "Tier 2: no results"
    )
    return top
