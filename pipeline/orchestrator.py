"""
Pipeline orchestrator — ties fetcher → dedup → extractor → normaliser → embedder → DB.

Two entry points:

  process_batch(job_dicts)
    Accepts raw SerpAPI job dicts (from the fetcher or a test fixture).
    Runs the full ingestion pipeline on each and persists to the DB.

  reprocess(job_ids=None)
    Re-runs LLM extraction on stored serp_api_json without re-fetching.
    Pass a list of job_ids to target specific records, or None to reprocess
    all jobs with status='extraction_failed'.
    Useful when the extraction prompt improves.
"""
from __future__ import annotations

import json
import logging
from typing import Sequence

from tqdm import tqdm

from db.operations import (
    insert_job,
    mark_job_failed,
    get_jobs_for_reprocessing,
)
from pipeline.dedup import Deduplicator
from pipeline.embedder import Embedder
from pipeline.extractor import Extractor
from pipeline.normalizer import Normalizer

logger = logging.getLogger(__name__)


def _extract_highlights(job: dict) -> dict:
    """
    Pull qualifications and responsibilities out of SerpAPI's job_highlights
    structure into flat text fields.
    """
    qualifications   = job.get("qualifications", "") or ""
    responsibilities = job.get("responsibilities", "") or ""

    for section in job.get("job_highlights") or []:
        title = (section.get("title") or "").lower()
        items = section.get("items") or []
        text  = "\n".join(items)
        if "qualif" in title:
            qualifications = qualifications or text
        elif "responsib" in title:
            responsibilities = responsibilities or text

    return {
        **job,
        "qualifications":   qualifications,
        "responsibilities": responsibilities,
    }


def _primary_apply_url(job: dict) -> str:
    """Return the best apply URL from apply_options or share_link."""
    for opt in job.get("apply_options") or []:
        if url := opt.get("link"):
            return url
    return job.get("share_link", "")


class Orchestrator:
    def __init__(self) -> None:
        self._dedup      = Deduplicator()
        self._extractor  = Extractor()
        self._normalizer = Normalizer()
        self._embedder   = Embedder()

    def process_batch(self, job_dicts: Sequence[dict]) -> dict:
        """
        Run the full ingestion pipeline on a list of raw SerpAPI job dicts.

        Returns a summary dict:
          inserted    — jobs successfully stored
          duplicates  — jobs skipped as duplicates
          failed      — jobs that failed extraction
        """
        stats = {"inserted": 0, "duplicates": 0, "failed": 0}

        for raw_job in tqdm(job_dicts, desc="Processing jobs", unit="job"):
            job = _extract_highlights(raw_job)

            # ── 1. Dedup ──────────────────────────────────────────────────
            is_dup, reason = self._dedup.is_duplicate(job)
            if is_dup:
                logger.debug(f"Skip [{reason}]: {job.get('title')!r}")
                stats["duplicates"] += 1
                continue

            # ── 2. Extract ────────────────────────────────────────────────
            extraction = self._extractor.extract(job)
            if extraction is None:
                mark_job_failed(
                    dedup_hash=job["dedup_hash"],
                    serp_api_json=job.get("serp_api_json", {}),
                )
                stats["failed"] += 1
                continue

            # ── 3. Normalize skills & frameworks ──────────────────────────
            skill_ids     = self._normalizer.normalize_skills(extraction.pop("skills", []))
            framework_ids = self._normalizer.normalize_frameworks(extraction.pop("frameworks", []))

            # ── 4. Compose full job record ────────────────────────────────
            job_record = {
                "title":                job.get("title", ""),
                "url":                  _primary_apply_url(job),
                "company_name":         job.get("company_name", ""),
                "location":             job.get("location", ""),
                "description":          job.get("description", ""),
                "qualifications":       job.get("qualifications", ""),
                "responsibilities":     job.get("responsibilities", ""),
                "date_listed":          _parse_date_listed(job),
                "serp_api_json":        job.get("serp_api_json"),
                "dedup_hash":           job["dedup_hash"],
                **extraction,
            }

            # Attach canonical names for embedding composition
            job_record["skills_canonical"]     = _ids_to_names(skill_ids, "skill", self._normalizer)
            job_record["frameworks_canonical"] = _ids_to_names(framework_ids, "framework", self._normalizer)

            # ── 5. Embed ──────────────────────────────────────────────────
            embedding = self._embedder.embed_job(job_record)

            # ── 6. Store ──────────────────────────────────────────────────
            try:
                insert_job(job_record, embedding, skill_ids, framework_ids)
                stats["inserted"] += 1
            except Exception as exc:
                logger.error(
                    f"DB insert failed for {job.get('title')!r}: {exc}"
                )
                stats["failed"] += 1

        logger.info(
            f"Batch complete — inserted={stats['inserted']}, "
            f"duplicates={stats['duplicates']}, failed={stats['failed']}"
        )
        return stats

    def reprocess(self, job_ids: list[int] | None = None) -> dict:
        """
        Re-run extraction on stored serp_api_json records.

        If job_ids is None, targets all jobs with status='extraction_failed'.
        Does not re-fetch from SerpAPI.
        """
        if job_ids is not None:
            from db.operations import get_jobs_by_ids
            records = [
                {
                    "job_id":       r["job_id"],
                    "dedup_hash":   r["dedup_hash"],
                    "serp_api_json": r["serp_api_json"],
                }
                for r in get_jobs_by_ids(job_ids)
            ]
        else:
            records = get_jobs_for_reprocessing()

        logger.info(f"Reprocessing {len(records)} jobs …")
        stats = {"reprocessed": 0, "failed": 0}

        for record in tqdm(records, desc="Reprocessing", unit="job"):
            raw_job = record.get("serp_api_json") or {}
            if isinstance(raw_job, str):
                raw_job = json.loads(raw_job)

            # serp_api_json stores the full page response; we need the single job.
            # Try to reconstruct from the stored job fields if full page isn't useful.
            job = _extract_highlights(raw_job)
            job["dedup_hash"] = record["dedup_hash"]

            extraction = self._extractor.extract(job)
            if extraction is None:
                stats["failed"] += 1
                continue

            skill_ids     = self._normalizer.normalize_skills(extraction.pop("skills", []))
            framework_ids = self._normalizer.normalize_frameworks(extraction.pop("frameworks", []))

            job_record = {
                "title":            job.get("title", ""),
                "url":              _primary_apply_url(job),
                "company_name":     job.get("company_name", ""),
                "location":         job.get("location", ""),
                "description":      job.get("description", ""),
                "qualifications":   job.get("qualifications", ""),
                "responsibilities": job.get("responsibilities", ""),
                "date_listed":      _parse_date_listed(job),
                "serp_api_json":    raw_job,
                "dedup_hash":       record["dedup_hash"],
                **extraction,
            }
            job_record["skills_canonical"]     = _ids_to_names(skill_ids, "skill", self._normalizer)
            job_record["frameworks_canonical"] = _ids_to_names(framework_ids, "framework", self._normalizer)

            embedding = self._embedder.embed_job(job_record)

            try:
                from db.connection import connection
                with connection() as conn:
                    with conn.cursor() as cur:
                        import json as _json
                        import psycopg2.extras
                        cur.execute(
                            """
                            UPDATE jobs SET
                                title = %(title)s,
                                url = %(url)s,
                                company_name = %(company_name)s,
                                location = %(location)s,
                                description = %(description)s,
                                employment_type = %(employment_type)s,
                                attendance = %(attendance)s,
                                seniority = %(seniority)s,
                                experience_years_min = %(experience_years_min)s,
                                experience_years_max = %(experience_years_max)s,
                                salary_min = %(salary_min)s,
                                salary_max = %(salary_max)s,
                                salary_currency = %(salary_currency)s,
                                salary_period = %(salary_period)s,
                                qualifications = %(qualifications)s,
                                responsibilities = %(responsibilities)s,
                                embedding = %(embedding)s::vector,
                                status = 'active',
                                date_updated = NOW()
                            WHERE dedup_hash = %(dedup_hash)s
                            """,
                            {**job_record, "embedding": embedding},
                        )
                        cur.execute(
                            "SELECT job_id FROM jobs WHERE dedup_hash = %s",
                            (record["dedup_hash"],),
                        )
                        row = cur.fetchone()
                        if row:
                            job_id = row[0]
                            cur.execute("DELETE FROM job_skills WHERE job_id = %s", (job_id,))
                            cur.execute("DELETE FROM job_frameworks WHERE job_id = %s", (job_id,))
                            if skill_ids:
                                psycopg2.extras.execute_values(
                                    cur,
                                    "INSERT INTO job_skills (job_id, skill_id) VALUES %s",
                                    [(job_id, sid) for sid in skill_ids],
                                )
                            if framework_ids:
                                psycopg2.extras.execute_values(
                                    cur,
                                    "INSERT INTO job_frameworks (job_id, framework_id) VALUES %s",
                                    [(job_id, fid) for fid in framework_ids],
                                )
                stats["reprocessed"] += 1
            except Exception as exc:
                logger.error(f"Reprocess DB update failed: {exc}")
                stats["failed"] += 1

        logger.info(
            f"Reprocess complete — reprocessed={stats['reprocessed']}, failed={stats['failed']}"
        )
        return stats


# ── Helpers ───────────────────────────────────────────────────────────────

def _parse_date_listed(job: dict):
    """
    Attempt to parse a date from SerpAPI's detected_extensions.posted_at.
    Returns a date string ('YYYY-MM-DD') or None.
    """
    import re
    from datetime import date, timedelta

    posted = (job.get("detected_extensions") or {}).get("posted_at", "")
    if not posted:
        return None

    posted = posted.lower()
    today = date.today()

    if "today" in posted or "just" in posted or "hour" in posted or "minute" in posted:
        return today.isoformat()
    if "yesterday" in posted:
        return (today - timedelta(days=1)).isoformat()

    match = re.search(r"(\d+)\s+day", posted)
    if match:
        return (today - timedelta(days=int(match.group(1)))).isoformat()
    match = re.search(r"(\d+)\s+week", posted)
    if match:
        return (today - timedelta(weeks=int(match.group(1)))).isoformat()
    match = re.search(r"(\d+)\s+month", posted)
    if match:
        return (today - timedelta(days=int(match.group(1)) * 30)).isoformat()

    return None


def _ids_to_names(ids: list[int], kind: str, normalizer: Normalizer) -> list[str]:
    """Reverse-lookup canonical names from IDs using normalizer's name map."""
    if kind == "skill":
        reverse = {v: k for k, v in normalizer._skill_name.items()}
    else:
        reverse = {v: k for k, v in normalizer._framework_name.items()}
    return [reverse.get(i, str(i)) for i in ids]
