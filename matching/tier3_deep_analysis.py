"""
Tier 3 — Deep career_profile-fit analysis via an expensive model (OpenRouter).

Takes the top candidates (pre-ranked by t2_score) and produces two independent
per-job scores via DEEP_ANALYSIS_MODEL:

  t3_qualification  How qualified the candidate is for the role (1–100).
  t3_fit            How well the job aligns with the candidate's preferences (1–100).

These are combined into a single t3_score (match score) using the formula:
  t3_score = ((1 - β) + β * (t3_fit / 100)) * (t3_qualification / 100) * 100
where β = FITNESS_WEIGHT (default 0.2).  A qualification of 0 makes the match 0;
a fit of 0 only discounts the match by β.

Results are persisted to jobs.t3_score, jobs.t3_explanation, jobs.t3_qualification,
and jobs.t3_fit.
"""
from __future__ import annotations

import logging
from typing import Sequence

from config.settings import DEEP_ANALYSIS_MODEL, FITNESS_WEIGHT
from db.operations import update_tier3_scores
from llm.client import complete_json

logger = logging.getLogger(__name__)

_SYSTEM = """You are an expert career advisor and resume/job evaluator.

You will analyze a candidate's career profile against a specific job listing and produce
two completely independent evaluations.  The separation of concerns is strict:

────────────────────────────────────────────────────────────────
EVALUATION 1 — QUALIFICATION SCORE (1–100)
────────────────────────────────────────────────────────────────
Measure only how qualified the candidate is for this specific role.
Base your score entirely on demonstrated skills, depth of experience, and background
versus the job's stated requirements.  Do NOT consider the candidate's preferences,
location, compensation needs, or whether they would enjoy the job.

Be harsh and maximally differentiated.  The purpose of this
score is to separate candidates at the top end — returning 70–85 for every job is
useless.  The job market is extremely competitive; assume the candidate is competing
against strong applicants.

Scoring guide:
  90–100  Reserve 90+ for roles where the candidate is genuinely overqualified.       
  75–89   Exceptional — Candidate meets or exceeds all requirements; likely a top pick.
  55–74   Strong — Candidate meets most requirements; gaps are minor or incidental.
  40–54   Adequate — Candidate meets the core requirements but has notable gaps in key areas.
  20–39   Weak — Meaningful gaps; the candidate would need to stretch significantly.
  1–19    Poor — Missing critical qualifications; unlikely to progress past screening.

────────────────────────────────────────────────────────────────
EVALUATION 2 — FIT SCORE (1–100)
────────────────────────────────────────────────────────────────
Measure only how well this job aligns with the candidate's stated preferences and situation.
Consider: location/remote preferences, compensation, role type, seniority level, industry,
company culture signals, and any other preference the candidate mentions.
Do NOT consider qualifications — a perfect-fit job the candidate is underqualified for
still scores high here.

Average fit is below 50.  Most jobs have at least some mismatches.  A score of 100 should
be very rare.  Missing salary data is a mild negative (uncertainty) but not a heavy penalty
unless compensation is an explicit hard requirement.

Scoring guide:
  80–100  Excellent — Job closely matches stated preferences across most dimensions.
  60–79   Good — Reasonable match with a few minor mismatches.
  40–59   Moderate — Some meaningful mismatches in preference dimensions.
  20–39   Poor — Significant mismatches (wrong location type, seniority, industry, etc.)
  1–19    Very poor — Largely incompatible with stated preferences.

────────────────────────────────────────────────────────────────
OUTPUT FORMAT
────────────────────────────────────────────────────────────────
Write your reasoning first, then state the score.  Be specific: cite exact skills,
experience items, or preference statements from both the career profile and the job listing.

Return ONLY valid JSON with this exact structure:
{
  "qual_explanation": "<detailed reasoning about qualification level>",
  "qual_score": <integer 1-100>,
  "fit_explanation": "<detailed reasoning about preference alignment>",
  "fit_score": <integer 1-100>
}
"""


def _format_user_message(career_profile_text: str, job: dict) -> str:
    salary_str = ""
    if job.get("salary_min"):
        period   = job.get("salary_period") or "yearly"
        currency = job.get("salary_currency") or "USD"
        hi       = f"–{job['salary_max']:,}" if job.get("salary_max") else ""
        salary_str = f"\nSalary: {currency} {job['salary_min']:,}{hi} ({period})"

    return (
        f"=== CAREER PROFILE ===\n{career_profile_text}\n\n"
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


def _compute_match_score(qual: float, fit: float) -> float:
    """
    match = ((1 - β) + β * (fit / 100)) * (qual / 100) * 100
    A qual of 0 drives match to 0; a fit of 0 discounts by β only.
    """
    β = FITNESS_WEIGHT
    return round(((1 - β) + β * (fit / 100)) * (qual / 100) * 100, 1)


def _analyse_one(job: dict, career_profile_text: str) -> dict:
    """Run deep analysis for a single job. Returns the job dict enriched with analysis."""
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user",   "content": _format_user_message(career_profile_text, job)},
    ]

    try:
        analysis = complete_json(
            DEEP_ANALYSIS_MODEL, messages, temperature=0.1, max_tokens=4096
        )
    except Exception as exc:
        logger.error(
            f"Tier 3 analysis failed for {job.get('title')!r} "
            f"@ {job.get('company_name')!r}: {exc}"
        )
        analysis = {
            "qual_score": 0,
            "fit_score": 0,
            "qual_explanation": f"Analysis failed: {exc}",
            "fit_explanation": "",
        }

    qual_score = int(analysis.get("qual_score", 0))
    fit_score  = int(analysis.get("fit_score",  0))
    t3_score   = _compute_match_score(qual_score, fit_score)

    qual_exp = analysis.get("qual_explanation", "")
    fit_exp  = analysis.get("fit_explanation",  "")
    t3_explanation = f"Qualification:\n{qual_exp}\n\nFit:\n{fit_exp}"

    return {
        **job,
        "qual_score":      qual_score,
        "fit_score":       fit_score,
        "t3_score":        t3_score,
        "t3_qualification": qual_score,
        "t3_fit":          fit_score,
        "t3_explanation":  t3_explanation,
    }


def analyse_batch(
    jobs: Sequence[dict],
    career_profile_text: str,
    *,
    persist: bool = True,
) -> list[dict]:
    """
    Run deep analysis on each job in sequence (expensive calls — expected ≤15 jobs).
    Results are sorted by t3_score descending.

    Args:
        jobs:                Tier 2 top candidates.
        career_profile_text: Full career_profile text.
        persist:             If True, write scores to the DB.

    Returns:
        List of job dicts enriched with fit analysis, sorted by t3_score desc.
    """
    model_label = DEEP_ANALYSIS_MODEL
    logger.info(f"Tier 3: analysing {len(jobs)} jobs with {model_label!r} …")

    results = []
    for i, job in enumerate(jobs, 1):
        title   = job.get("title", "?")
        company = job.get("company_name", "?")
        logger.info(f"  [{i}/{len(jobs)}] {title!r} @ {company!r}")

        enriched = _analyse_one(job, career_profile_text)
        results.append(enriched)

        if persist and job.get("job_id"):
            update_tier3_scores(
                job["job_id"],
                t3_score=enriched["t3_score"],
                t3_explanation=enriched["t3_explanation"],
                t3_qualification=enriched["t3_qualification"],
                t3_fit=enriched["t3_fit"],
            )

    results.sort(key=lambda j: j.get("t3_score", 0), reverse=True)
    logger.info(
        f"Tier 3 complete. Top: {results[0].get('title')!r} "
        f"(match={results[0].get('t3_score')}, "
        f"qual={results[0].get('t3_qualification')}, "
        f"fit={results[0].get('t3_fit')})"
        if results else "Tier 3: no results"
    )
    return results
