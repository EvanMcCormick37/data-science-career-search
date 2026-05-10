"""Career profile — single source of truth for loading data/career_profile.md.

Call load() anywhere scoring needs the profile text.  Returns None when the
file is missing or still a placeholder so callers can skip scoring gracefully.
The result is cached after the first successful read.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from config.settings import RESUME_PATH

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def load() -> str | None:
    """Return the career profile text, or None if missing/placeholder."""
    if not RESUME_PATH.exists():
        logger.warning(
            f"Career profile not found at {RESUME_PATH}. "
            "Scoring will be skipped until it is created."
        )
        return None
    text = RESUME_PATH.read_text(encoding="utf-8").strip()
    if not text or "<!-- Fill in your" in text:
        logger.warning(
            "data/career_profile.md is still a placeholder. "
            "Fill it in to enable fit scoring."
        )
        return None
    return text
