"""
Ingestion-time fit scorer — cheap LLM scoring of a single job at ingestion.

Reads the career profile via matching.career_profile (cached after first load)
and uses SCORING_MODEL to evaluate how well each incoming job fits.

The score (0–100) and a one-sentence explanation are added to the job record
before DB insertion, so every job in the database already has a fit score by
the time ingestion completes.

If the career profile is missing or still a placeholder, scoring is silently
skipped and the job is inserted with t2_score = NULL.
"""
from __future__ import annotations

import logging

from config.settings import SCORING_MODEL
from llm.client import complete_json
from matching.career_profile import load as _load_career_profile
from matching.scoring import SYSTEM_PROMPT, format_user_message

logger = logging.getLogger(__name__)


class IngestScorer:
    """
    Scores a single job against the career profile using the cheap LLM.

    Instantiate once on the Orchestrator and call score() per job.
    The career profile is loaded and cached on first call.
    """

    def score(self, job: dict) -> tuple[int | None, str | None]:
        """
        Returns (score 0-100, explanation) on success, or (None, None) when the
        career profile is unavailable or the LLM call fails.
        """
        career_profile_text = _load_career_profile()
        if career_profile_text is None:
            return None, None

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": format_user_message(career_profile_text, job)},
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
