#!/usr/bin/env python3
"""
Pipeline smoke test — all queries in queries.yaml, one page each.

Workflow:
  1. Load all queries from queries.yaml
  2. Fetch one page per query from SerpAPI and save all raw responses to
     data/test_response.json as a list (one entry per query)
  3. Run the full ingestion pipeline (dedup → extract → normalise → embed → store)
     on all fetched jobs and print a summary

Flags let you decouple the two phases so you can re-run ingestion on the same
saved responses without burning additional API credits:

  --fetch-only     Fetch and save JSON, then stop (skip ingestion)
  --ingest-only    Skip the fetch; run ingestion on an existing JSON file
  --file PATH      JSON file to read from / write to  [default: data/test_response.json]
  --dry-run        Extract metadata but do not write anything to the DB;
                   saves per-job extraction results to data/extracted_fields.json

Usage:
    # Full test (fetch all queries + ingest):
    python scripts/test_pipeline.py

    # Just hit the API and save (inspect JSON before ingesting):
    python scripts/test_pipeline.py --fetch-only

    # Re-run ingestion on the saved file without another API call:
    python scripts/test_pipeline.py --ingest-only

    # Dry-run extraction on saved responses (no DB writes):
    python scripts/test_pipeline.py --ingest-only --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from config.settings import DATA_DIR
from pipeline.fetcher import load_queries, _make_params
from serpapi import GoogleSearch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_pipeline")

DEFAULT_OUTPUT = DATA_DIR / "test_response.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Smoke-test the pipeline against all queries.yaml entries.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--fetch-only",  action="store_true", help="Fetch and save JSON, then stop")
    mode.add_argument("--ingest-only", action="store_true", help="Skip fetch; run ingestion on saved file")
    p.add_argument("--file",    metavar="PATH", default=str(DEFAULT_OUTPUT),
                   help=f"JSON file to write to / read from (default: {DEFAULT_OUTPUT})")
    p.add_argument("--dry-run", action="store_true",
                   help="Extract metadata but do not write to the DB")
    return p.parse_args()


# ── Phase 1: Fetch ────────────────────────────────────────────────────────

def fetch_and_save(output_path: Path) -> list[dict]:
    """
    Fetch one page per query from queries.yaml and save all raw responses.

    Saved format: a JSON list, one entry per query:
      [{"_query_name": "...", "jobs_results": [...], ...full SerpAPI response}, ...]
    """
    queries = load_queries()
    if not queries:
        logger.error("queries.yaml has no entries. Add at least one query first.")
        sys.exit(1)

    logger.info(f"Fetching {len(queries)} quer{'y' if len(queries) == 1 else 'ies'} (1 page each) …")

    responses = []
    total_jobs = 0
    for i, query in enumerate(queries, 1):
        name = query.get("name", query.get("q", f"query_{i}"))
        logger.info(f"  [{i}/{len(queries)}] {name!r}  q={query.get('q')!r}  location={query.get('location')!r}")

        try:
            response = GoogleSearch(_make_params(query)).get_dict()
        except Exception as exc:
            logger.error(f"  SerpAPI call failed: {exc}")
            continue

        if "error" in response:
            logger.error(f"  SerpAPI error: {response['error']}")
            continue

        job_count = len(response.get("jobs_results", []))
        logger.info(f"  → {job_count} jobs")
        total_jobs += job_count

        # Tag each response with the query name so ingest can log it
        response["_query_name"] = name
        responses.append(response)

    logger.info(f"Total: {total_jobs} jobs across {len(responses)} queries")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(responses, indent=2), encoding="utf-8")
    logger.info(f"Raw responses saved → {output_path}")

    return responses


# ── Phase 2: Ingest ───────────────────────────────────────────────────────

def _load_job_dicts(input_path: Path) -> list[dict]:
    """
    Load job dicts from a saved test_response.json.

    Handles both formats:
      - List of responses (current multi-query format)
      - Single response dict (legacy single-query format)
    """
    raw = json.loads(input_path.read_text(encoding="utf-8"))

    # Normalise to a list of responses
    if isinstance(raw, dict):
        responses = [raw]  # legacy single-query file
    else:
        responses = raw

    job_dicts = []
    for response in responses:
        query_name = response.get("_query_name", "unknown query")
        jobs = response.get("jobs_results", [])
        for job in jobs:
            job_dicts.append({**job, "serp_api_json": response, "_query_name": query_name})

    return job_dicts


def ingest_from_file(input_path: Path, dry_run: bool) -> None:
    """Load saved SerpAPI responses and run the full ingestion pipeline."""
    if not input_path.exists():
        logger.error(f"File not found: {input_path}")
        logger.error("Run without --ingest-only first to fetch and save the responses.")
        sys.exit(1)

    job_dicts = _load_job_dicts(input_path)
    if not job_dicts:
        logger.warning("No jobs found in the JSON file.")
        return

    logger.info(f"Loaded {len(job_dicts)} jobs from {input_path}")

    if dry_run:
        logger.info("[dry-run] Skipping DB writes — extracting metadata only …")
        from pipeline.extractor import Extractor
        from pipeline.dedup import make_dedup_hash
        extractor = Extractor()
        records = []

        for job in job_dicts:
            job["dedup_hash"] = make_dedup_hash(job)
            extraction = extractor.extract(job)
            status = "OK" if extraction else "FAILED"
            print(
                f"  [{status}] {job.get('title', '?'):<45} "
                f"@ {job.get('company_name', '?')}"
                f"  [{job.get('_query_name', '')}]"
            )
            if extraction:
                print(f"         attendance={extraction.get('attendance')}  "
                      f"seniority={extraction.get('seniority')}  "
                      f"skills={len(extraction.get('skills', []))}  "
                      f"frameworks={len(extraction.get('frameworks', []))}")
            records.append({
                "query":        job.get("_query_name"),
                "title":        job.get("title"),
                "company_name": job.get("company_name"),
                "location":     job.get("location"),
                "url":          next((o.get("link") for o in job.get("apply_options") or []), job.get("share_link")),
                "status":       status,
                "extraction":   extraction,
            })

        extracted_path = input_path.parent / "extracted_fields.json"
        extracted_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        logger.info(f"Extracted fields saved → {extracted_path}")
        return

    from pipeline.orchestrator import Orchestrator
    orchestrator = Orchestrator()
    stats = orchestrator.process_batch(job_dicts)

    print(f"\n{'='*50}")
    print(f"  Ingestion complete")
    print(f"  Inserted:   {stats['inserted']}")
    print(f"  Duplicates: {stats['duplicates']}")
    print(f"  Failed:     {stats['failed']}")
    print(f"{'='*50}")


# ── Entry point ───────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    output_path = Path(args.file)

    if not args.ingest_only:
        fetch_and_save(output_path)

    if not args.fetch_only:
        ingest_from_file(output_path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
