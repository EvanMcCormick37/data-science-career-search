"""
Skill & framework normaliser.

Loads alias tables once from the DB into in-memory dicts for fast lookup
during high-volume ingestion.  The DB is the source of truth; the dicts
are a read-through cache.

Resolution order per item:
  1. alias_map lookup (lowercase)      → return canonical id
  2. exact name match in names table   → return id
  3. not found → INSERT as candidate   → return new id

Call reload() after manually adding aliases or promoting candidate entries
so the in-memory maps reflect the latest DB state without restarting.
"""
from __future__ import annotations

import logging

from db.connection import connection

logger = logging.getLogger(__name__)

class Normalizer:
    def __init__(self) -> None:
        # alias → id
        self._skill_alias:     dict[str, int] = {}
        self._framework_alias: dict[str, int] = {}
        # lowercase canonical name → id
        self._skill_name:     dict[str, int] = {}
        self._framework_name: dict[str, int] = {}
        self.reload()

    def reload(self) -> None:
        """Refresh all in-memory maps from the DB."""
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT alias, skill_id FROM skill_aliases")
                self._skill_alias = {r[0].lower(): r[1] for r in cur.fetchall()}

                cur.execute("SELECT alias, framework_id FROM framework_aliases")
                self._framework_alias = {r[0].lower(): r[1] for r in cur.fetchall()}

                cur.execute("SELECT skill_id, name FROM skills")
                self._skill_name = {r[1].lower(): r[0] for r in cur.fetchall()}

                cur.execute("SELECT framework_id, name FROM frameworks")
                self._framework_name = {r[1].lower(): r[0] for r in cur.fetchall()}

        logger.debug(
            f"Normaliser reloaded: {len(self._skill_alias)} skill aliases, "
            f"{len(self._framework_alias)} framework aliases, "
            f"{len(self._skill_name)} canonical skills, "
            f"{len(self._framework_name)} canonical frameworks"
        )

    def normalize_skills(self, names: list[str]) -> list[int]:
        """Return deduplicated skill_ids for the given name list."""
        seen: set[int] = set()
        result: list[int] = []
        for name in names:
            sid = self._resolve_skill(name)
            if sid is not None and sid not in seen:
                result.append(sid)
                seen.add(sid)
        return result

    def normalize_frameworks(self, names: list[str]) -> list[int]:
        """Return deduplicated framework_ids for the given name list."""
        seen: set[int] = set()
        result: list[int] = []
        for name in names:
            fid = self._resolve_framework(name)
            if fid is not None and fid not in seen:
                result.append(fid)
                seen.add(fid)
        return result

    # ── Internal ──────────────────────────────────────────────────────────

    def _resolve_skill(self, name: str) -> int | None:
        key = name.strip().lower()
        if not key:
            return None

        if key in self._skill_alias:
            return self._skill_alias[key]

        if key in self._skill_name:
            return self._skill_name[key]

        return self._insert_candidate_skill(name.strip())

    def _resolve_framework(self, name: str) -> int | None:
        key = name.strip().lower()
        if not key:
            return None

        if key in self._framework_alias:
            return self._framework_alias[key]

        if key in self._framework_name:
            return self._framework_name[key]

        return self._insert_candidate_framework(name.strip())

    def _insert_candidate_skill(self, name: str) -> int:
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO skills (domain, name, is_candidate)
                    VALUES ('(candidate)', %s, 1)
                    ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                    RETURNING skill_id
                    """,
                    (name,),
                )
                skill_id: int = cur.fetchone()[0]
        self._skill_name[name.lower()] = skill_id
        logger.debug(f"Candidate skill inserted: {name!r} → {skill_id}")
        return skill_id

    def _insert_candidate_framework(self, name: str) -> int:
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO frameworks (domain, name, is_candidate)
                    VALUES ('(candidate)', %s, 1)
                    ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                    RETURNING framework_id
                    """,
                    (name,),
                )
                framework_id: int = cur.fetchone()[0]
        self._framework_name[name.lower()] = framework_id
        logger.debug(f"Candidate framework inserted: {name!r} → {framework_id}")
        return framework_id
