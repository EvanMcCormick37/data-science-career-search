"""Shared tier-2 scoring prompt and message builder.

Both IngestScorer (sync, one job at a time) and tier2_cheap_llm (async batch)
perform identical work — import from here so the rubric has a single home.
"""
from __future__ import annotations

SYSTEM_PROMPT = """You are an expert career advisor and resume/job evaluator.

Given a candidate's career profile and a specific job listing, determine a 'fit score' which captures how good of a match the applicant is for said job.
Your goal is to filter out jobs which are a poor fit, so be brutally honest and realistic in your evaluations. However, if you think a job is a strong fit, don't be afraid to say so emphatically.

Scoring guide:
  90-100  Exceptional fit — The applicant would be a top candidate for this job, and the job is a perfect fit for the candidate's preferences.
  75-89   Strong fit — The applicant is a strong candidate and the job is a reasonable fit for their preferences.
  60-74   Moderate fit — The applicant is an adequate fit for the position, or they are a strong fit but the position isn't a match for their preferences.
  40-59   Weak fit — There are moderate gaps in experience, making the applicant weak for the job.
  0-39    Poor fit — There are serious gaps in experience which make it unlikely for the candidate to be seriously considered for the position, or the position is far outside of the candidate's preferences.

Additionally, provide a brief explanation (1-5 sentences) for why you chose the score.
Cite exact skills, experiences, or preferences from both the career profile and the job description.

Return ONLY valid JSON:
{
  "score": <integer 0-100>,
  "explanation": <string>
}
"""


def format_user_message(career_profile_text: str, job: dict) -> str:
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
