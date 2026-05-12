"""
Config file service — read/write queries.yaml and career_profile.md atomically.
"""
from __future__ import annotations

import os
import tempfile

import yaml

from config.settings import QUERIES_PATH, RESUME_PATH


def read_queries() -> tuple[dict, list[dict]]:
    """
    Load queries.yaml and return (defaults, queries).
    Handles both the legacy plain-list format and the current defaults+queries dict format.
    """
    try:
        with open(QUERIES_PATH, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError:
        return {}, []
    if isinstance(data, list):
        return {}, data
    if isinstance(data, dict):
        return data.get("defaults", {}), data.get("queries", [])
    return {}, []


def write_queries(defaults: dict, queries: list[dict]) -> None:
    """
    Serialize defaults + queries to YAML, validate by round-tripping, then write atomically.
    """
    data = {"defaults": defaults, "queries": queries}
    serialized = yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    yaml.safe_load(serialized)
    _atomic_write(QUERIES_PATH, serialized)


def read_career_profile() -> str:
    """Return the full text of the career profile markdown file."""
    return RESUME_PATH.read_text(encoding="utf-8")


def write_career_profile(text: str) -> None:
    """Write career profile text atomically."""
    _atomic_write(RESUME_PATH, text)


def _atomic_write(path, content: str) -> None:
    """Write content to a temp file then os.replace to the target path atomically."""
    dir_ = os.path.dirname(path)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=dir_,
        delete=False,
        suffix=".tmp",
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    os.replace(tmp_path, path)
