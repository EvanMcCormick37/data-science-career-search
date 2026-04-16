"""
Applications service layer — thin pass-through over db.operations.
Exists as the extension point for future business logic.
"""
from __future__ import annotations

import db.operations as _ops
from db.operations import get_application_detail, list_applications, update_application

__all__ = [
    "list_applications",
    "get_application_detail",
    "update_application",
    "log_application",
]


def log_application(**fields) -> int:
    """
    Create a new application record and return the new application_id.
    Delegates to db.operations.create_application (dashboard version).
    """
    return _ops.create_application(**fields)
