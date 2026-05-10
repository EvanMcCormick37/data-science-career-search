"""
Applications service layer — thin pass-through over db.applications.
Exists as the extension point for future business logic.
"""
from __future__ import annotations

from db.applications import (
    create_application,
    get_application_detail,
    list_applications,
    update_application,
)

__all__ = [
    "list_applications",
    "get_application_detail",
    "update_application",
    "log_application",
]


def log_application(**fields) -> int:
    """Create a new application record and return the new application_id."""
    return create_application(**fields)
