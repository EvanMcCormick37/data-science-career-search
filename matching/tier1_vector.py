"""
Tier 1 — Vector similarity search via pgvector.

Embeds the resume using the same model as job ingestion, then runs an
approximate nearest-neighbour query against the HNSW index on jobs.embedding.

Returns the top-N active jobs ordered by cosine similarity (highest first).
The cosine similarity score is computed as 1 - cosine_distance, so 1.0 is
a perfect match and 0.0 is orthogonal.

This stage is free (local compute only) and completes in <100ms at ≤10K jobs.
"""
from __future__ import annotations

import logging

import psycopg2.extras

from config.settings import TIER1_CANDIDATES
from db.connection import connection
from pipeline.embedder import Embedder

logger = logging.getLogger(__name__)


def search(
    resume_embedding: list[float],
    limit: int = TIER1_CANDIDATES,
    status_filter: str = "active",
) -> list[dict]:
    """
    Run a cosine similarity search against all jobs with the given status.

    Args:
        resume_embedding: 768-dim normalised embedding vector for the resume.
        limit:            Maximum number of jobs to return.
        status_filter:    Only consider jobs with this status (default 'active').

    Returns:
        List of job dicts with an added 'cosine_similarity' key (float, 0–1).
        Ordered highest similarity first.
    """
    embedding_str = "[" + ",".join(str(x) for x in resume_embedding) + "]"

    with connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    job_id,
                    title,
                    company_name,
                    location,
                    attendance,
                    seniority,
                    employment_type,
                    salary_min,
                    salary_max,
                    salary_currency,
                    salary_period,
                    description,
                    qualifications,
                    responsibilities,
                    date_listed,
                    url,
                    1 - (embedding <=> %(embedding)s::vector) AS cosine_similarity
                FROM jobs
                WHERE status = %(status)s
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> %(embedding)s::vector
                LIMIT %(limit)s
                """,
                {
                    "embedding": embedding_str,
                    "status":    status_filter,
                    "limit":     limit,
                },
            )
            rows = cur.fetchall()

    results = [dict(row) for row in rows]
    logger.info(
        f"Tier 1: {len(results)} candidates retrieved "
        f"(top similarity: {results[0]['cosine_similarity']:.3f})" if results else
        "Tier 1: no candidates found"
    )
    return results


def embed_and_search(
    resume: dict,
    limit: int = TIER1_CANDIDATES,
) -> list[dict]:
    """
    Convenience wrapper: embed the resume dict then run the similarity search.

    resume dict should contain at minimum:
      target_role, qualifications_summary, experience_summary, skills, frameworks
    (see pipeline/embedder.py for the full composition format)
    """
    embedder = Embedder()
    embedding = embedder.embed_resume(resume)
    return search(embedding, limit=limit)
