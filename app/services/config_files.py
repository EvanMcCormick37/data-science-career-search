"""
Config file service — read/write queries.yaml and career_profile.md atomically.
"""
from __future__ import annotations

import os
import tempfile

import yaml

from config.settings import QUERIES_PATH, RESUME_PATH


def read_queries() -> list[dict]:
    """Load queries.yaml and return the list of query dicts."""
    with open(QUERIES_PATH, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if isinstance(data, list):
        return data
    return data.get("queries", []) if isinstance(data, dict) else []


def write_queries(queries: list[dict]) -> None:
    """
    Serialize queries to YAML, validate by round-tripping, then write atomically.
    """
    serialized = yaml.dump(queries, allow_unicode=True, sort_keys=False)
    # Validate by round-tripping
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
