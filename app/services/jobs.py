"""
Jobs service layer — thin pass-through over db.operations.
Exists as the extension point for future business logic.
"""
from __future__ import annotations

import os

from config.settings import RESUMES_DIR
from db.operations import get_job_detail, list_jobs

__all__ = ["list_jobs", "get_job_detail", "list_available_resumes"]


def list_available_resumes() -> list[str]:
    """Return sorted list of resume filenames in RESUMES_DIR. Empty list if dir missing."""
    try:
        entries = os.listdir(RESUMES_DIR)
    except FileNotFoundError:
        return []
    files = [e for e in entries if os.path.isfile(os.path.join(RESUMES_DIR, e))]
    return sorted(files)
