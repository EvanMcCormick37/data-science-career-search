"""
Tier 3 — Deep resume-fit analysis via Claude (or any capable model).

Takes the top Tier 2 candidates and produces a detailed per-job report:
  - fit_score      (0–100)
  - strengths      (where the resume aligns well)
  - gaps           (where the resume falls short)
  - recommendation (apply / apply_with_caveats / skip)
  - resume_tips    (targeted tweaks for this specific role)

Model routing:
  If ANTHROPIC_API_KEY is set, calls the Anthropic API directly.
  Otherwise routes through OpenRouter using DEEP_ANALYSIS_MODEL.

Results are persisted to jobs.tier3_score and jobs.tier3_explanation.
"""
from __future__ import annotations

import json
import logging
from typing import Sequence

from config.settings import (
    ANTHROPIC_API_KEY,
    DEEP_ANALYSIS_MODEL,
)
from db.operations import update_tier3_scores

logger = logging.getLogger(__name__)

_SYSTEM = """You are an expert career advisor and resume evaluator.

Given a candidate's resume and a specific job listing, produce a detailed fit analysis.

Return ONLY valid JSON with this exact structure:
{
  "fit_score": <integer 0-100>,
  "strengths": [<string>, ...],
  "gaps": [<string>, ...],
  "recommendation": "apply" | "apply_with_caveats" | "skip",
  "resume_tips": [<string>, ...]
}

Scoring guide:
  90-100  Exceptional fit — apply immediately
  75-89   Strong fit — apply with minor tailoring
  60-74   Moderate fit — apply if interested, address gaps in cover letter
  40-59   Weak fit — significant gaps, apply only if very interested
  0-39    Poor fit — skip

Be specific and actionable.  Cite exact skills, experiences, or wording from both
the resume and the job description.  Resume tips should be concrete changes to the
resume text, not general advice."""


def _format_user_message(resume_text: str, job: dict) -> str:
    salary_str = ""
    if job.get("salary_min"):
        period   = job.get("salary_period") or "yearly"
        currency = job.get("salary_currency") or "USD"
        hi       = f"–{job['salary_max']:,}" if job.get("salary_max") else ""
        salary_str = f"\nSalary: {currency} {job['salary_min']:,}{hi} ({period})"

    return (
        f"=== RESUME ===\n{resume_text}\n\n"
        f"=== JOB LISTING ===\n"
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company_name', '')}\n"
        f"Location: {job.get('location', '')} | "
        f"Attendance: {job.get('attendance') or 'unknown'} | "
        f"Seniority: {job.get('seniority') or 'unknown'} | "
        f"Employment: {job.get('employment_type') or 'unknown'}"
        f"{salary_str}\n\n"
        f"Description:\n{job.get('description') or ''}\n\n"
        f"Qualifications:\n{job.get('qualifications') or ''}\n\n"
        f"Responsibilities:\n{job.get('responsibilities') or ''}"
    )


def _call_anthropic(messages: list[dict]) -> str:
    """Call the Anthropic API directly (avoids OpenRouter markup for Claude models)."""
    import anthropic  # optional dependency; only imported if ANTHROPIC_API_KEY is set

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    user_messages = [m for m in messages if m["role"] != "system"]

    response = client.messages.create(
        model=DEEP_ANALYSIS_MODEL.split("/")[-1],  # strip "anthropic/" prefix if present
        max_tokens=2048,
        system=system,
        messages=user_messages,
    )
    return response.content[0].text


def _call_openrouter(messages: list[dict]) -> str:
    from llm.client import complete
    return complete(DEEP_ANALYSIS_MODEL, messages, temperature=0.1, max_tokens=2048)


def _analyse_one(job: dict, resume_text: str) -> dict:
    """Run deep analysis for a single job. Returns the job dict with analysis added."""
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user",   "content": _format_user_message(resume_text, job)},
    ]

    try:
        if ANTHROPIC_API_KEY:
            raw = _call_anthropic(messages)
        else:
            raw = _call_openrouter(messages)

        analysis = json.loads(raw)
    except json.JSONDecodeError:
        logger.error(
            f"Tier 3 JSON parse failure for {job.get('title')!r} "
            f"@ {job.get('company_name')!r}"
        )
        analysis = {
            "fit_score":      0,
            "strengths":      [],
            "gaps":           ["Analysis failed — JSON parse error"],
            "recommendation": "skip",
            "resume_tips":    [],
        }
    except Exception as exc:
        logger.error(f"Tier 3 API error for job {job.get('job_id')}: {exc}")
        analysis = {
            "fit_score":      0,
            "strengths":      [],
            "gaps":           [f"Analysis failed: {exc}"],
            "recommendation": "skip",
            "resume_tips":    [],
        }

    return {**job, **analysis}


def analyse_batch(
    jobs: Sequence[dict],
    resume_text: str,
    *,
    persist: bool = True,
) -> list[dict]:
    """
    Run deep analysis on each job in sequence (these are expensive calls —
    expected to be ≤15 jobs).  Results are sorted by fit_score descending.

    Args:
        jobs:        Tier 2 top candidates.
        resume_text: Full resume text.
        persist:     If True, write scores to the DB.

    Returns:
        List of job dicts enriched with fit analysis, sorted by fit_score desc.
    """
    model_label = DEEP_ANALYSIS_MODEL
    logger.info(f"Tier 3: analysing {len(jobs)} jobs with {model_label!r} …")

    results = []
    for i, job in enumerate(jobs, 1):
        title   = job.get("title", "?")
        company = job.get("company_name", "?")
        logger.info(f"  [{i}/{len(jobs)}] {title!r} @ {company!r}")

        enriched = _analyse_one(job, resume_text)
        results.append(enriched)

        if persist and job.get("job_id"):
            explanation = json.dumps({
                "strengths":      enriched.get("strengths", []),
                "gaps":           enriched.get("gaps", []),
                "recommendation": enriched.get("recommendation", ""),
                "resume_tips":    enriched.get("resume_tips", []),
            })
            update_tier3_scores(
                job["job_id"],
                enriched.get("fit_score", 0),
                explanation,
            )

    results.sort(key=lambda j: j.get("fit_score", 0), reverse=True)
    logger.info(
        f"Tier 3 complete. Top: {results[0].get('title')!r} "
        f"(score={results[0].get('fit_score')})"
        if results else "Tier 3: no results"
    )
    return results
