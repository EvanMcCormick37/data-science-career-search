"""
Ingestion-time fit scorer — cheap LLM scoring of a single job at ingestion.

Reads the career profile from data/career_profile.md (cached after first load)
and uses SCORING_MODEL to evaluate how well each incoming job fits.

The score (0–100) and a one-sentence explanation are added to the job record
before DB insertion, so every job in the database already has a fit score by
the time ingestion completes.

If the career profile is missing or still a placeholder, scoring is silently
skipped and the job is inserted with tier2_score = NULL.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from config.settings import RESUME_PATH, SCORING_MODEL
from llm.client import complete_json

logger = logging.getLogger(__name__)

_SYSTEM = """You are an expert career advisor and resume/job evaluator.

Given a candidate's career profile and a specific job listing, determine a 'fit score' which captures how good of a match the applicant is for said job.
Your goal is to filter out jobs which are a poor fit, so be brutally honest and realistic in your evaluations. However, if you think a job is a strong fit, don't be afraid to say so emphatically.

Scoring guide:
  90-100  Exceptional fit — The applicant would be a top candidate for this job, and the job is a perfect fit for the candidate's preferences.
  75-89   Strong fit — The applicant is a strong candidate and the job is a reasonable fit for their preferences.
  60-74   Moderate fit — The applicant is an adequate fit for the position, or they are a strong fit but the position isn't a match for their preferences.
  40-59   Weak fit — There are moderate gaps in experience, making the applicant weak for the job.
  0-39    Poor fit — There are serious gaps in experience which make it unlikely for the candidate to be seriously considered for the position, or the position is far outside of the candidate's preferences.

Additionally, provide a brief explanation (1–5 sentences) for why you chose the score.
Cite exact skills, experiences, or preferences from both the career profile and the job description.

Return ONLY valid JSON:
{
  "score": <integer 0-100>,
  "explanation": <string>
}
"""


@lru_cache(maxsize=1)
def _load_career_profile() -> str | None:
    """
    Load and cache the career profile text.
    Returns None if the file is missing or still a placeholder — callers
    should treat None as 'skip scoring'.
    """
    if not RESUME_PATH.exists():
        logger.warning(
            f"Career profile not found at {RESUME_PATH}. "
            "Ingestion-time fit scoring will be skipped until it is created."
        )
        return None
    text = RESUME_PATH.read_text(encoding="utf-8").strip()
    if not text or "<!-- Fill in your" in text:
        logger.warning(
            "data/career_profile.md is still a placeholder. "
            "Fill it in to enable ingestion-time fit scoring."
        )
        return None
    return text


def _format_user_message(career_profile_text: str, job: dict) -> str:
    salary_str = ""
    if job.get("salary_min") and job.get("salary_max"):
        period   = job.get("salary_period") or "yearly"
        currency = job.get("salary_currency") or "USD"
        salary_str = f"\nSalary: {currency} {job['salary_min']:,}–{job['salary_max']:,} ({period})"

    return (
        f"CAREER PROFILE:\n{career_profile_text}\n\n"
        "---\n"
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


class IngestScorer:
    """
    Scores a single job against the career profile using the cheap LLM.

    Instantiate once on the Orchestrator and call score() per job.
    The career profile is loaded and cached on first call.
    """

    def score(self, job: dict) -> tuple[int | None, str | None]:
        """
        Score a job for fit.

        Returns:
            (score: int 0-100, explanation: str)  on success
            (None, None)                            if career profile is unavailable
                                                    or the LLM call fails
        """
        career_profile_text = _load_career_profile()
        if career_profile_text is None:
            return None, None

        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": _format_user_message(career_profile_text, job)},
        ]

        try:
            result = complete_json(
                SCORING_MODEL,
                messages,
                temperature=0.0,
                max_tokens=256,
            )
            score       = int(result.get("score", 0))
            explanation = str(result.get("explanation", ""))
            return score, explanation
        except Exception as exc:
            logger.warning(
                f"Ingestion fit scoring failed for {job.get('title')!r} "
                f"@ {job.get('company_name')!r}: {exc}"
            )
            return None, None
