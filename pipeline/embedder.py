"""
Local embedding generation using sentence-transformers all-mpnet-base-v2.

Model: all-mpnet-base-v2
  - 768 dimensions
  - 384 token context window
  - Downloaded automatically (~420 MB) on first use

Composition strings:
  Job:    "{title} | {qualifications} | {responsibilities} | {skills} | {frameworks}"
  Resume: "{target_role} | {qualifications_summary} | {experience_summary} | {skills} | {frameworks}"

Truncation strategy when input exceeds 384 tokens:
  Fields are truncated in reverse priority order (lowest priority first):
    frameworks → skills → responsibilities → qualifications → title (never truncated)
"""
from __future__ import annotations

import logging
from functools import lru_cache

from sentence_transformers import SentenceTransformer

from config.settings import EMBEDDING_MODEL, EMBEDDING_MAX_TOKENS

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_model() -> SentenceTransformer:
    logger.info(f"Loading embedding model {EMBEDDING_MODEL!r} (first call only) …")
    return SentenceTransformer(EMBEDDING_MODEL)


def _token_count(text: str, model: SentenceTransformer) -> int:
    return len(model.tokenizer.encode(text, add_special_tokens=False))


def _truncate_field(text: str, budget: int, model: SentenceTransformer) -> str:
    """Truncate `text` to at most `budget` tokens."""
    if budget <= 0:
        return ""
    tokens = model.tokenizer.encode(text, add_special_tokens=False)
    if len(tokens) <= budget:
        return text
    return model.tokenizer.decode(tokens[:budget], skip_special_tokens=True)


def _build_job_text(job: dict, model: SentenceTransformer) -> str:
    """
    Compose the embedding string for a job, truncating lower-priority fields
    if the total would exceed EMBEDDING_MAX_TOKENS.
    """
    title           = (job.get("title") or "").strip()
    qualifications  = (job.get("qualifications") or "").strip()
    responsibilities = (job.get("responsibilities") or "").strip()
    skills          = ", ".join(job.get("skills_canonical") or [])
    frameworks      = ", ".join(job.get("frameworks_canonical") or [])

    separator_tokens = 4 * 3  # " | " between each of 5 fields ≈ 3 tokens each
    title_tokens = _token_count(title, model)
    budget = EMBEDDING_MAX_TOKENS - title_tokens - separator_tokens

    # Truncate lowest priority first
    for field_ref in [("frameworks", frameworks), ("skills", skills),
                      ("responsibilities", responsibilities), ("qualifications", qualifications)]:
        pass  # calculate below

    fields = [
        ("qualifications",   qualifications),
        ("responsibilities", responsibilities),
        ("skills",           skills),
        ("frameworks",       frameworks),
    ]
    # Measure each field
    token_counts = {name: _token_count(text, model) for name, text in fields}
    total = sum(token_counts.values())

    if total > budget:
        # Truncate in reverse priority order (frameworks first, qualifications last)
        for name in ["frameworks", "skills", "responsibilities", "qualifications"]:
            excess = total - budget
            if excess <= 0:
                break
            available = token_counts[name]
            cut = min(excess, available)
            token_counts[name] -= cut
            total -= cut

        # Re-truncate each field to its new budget
        truncated = {}
        for name, text in fields:
            idx = dict(fields)
            truncated[name] = _truncate_field(text, token_counts[name], model)
        qualifications   = truncated["qualifications"]
        responsibilities = truncated["responsibilities"]
        skills           = truncated["skills"]
        frameworks       = truncated["frameworks"]

    return f"{title} | {qualifications} | {responsibilities} | {skills} | {frameworks}"


def _build_resume_text(resume: dict, model: SentenceTransformer) -> str:
    target_role    = (resume.get("target_role") or "").strip()
    qualifications = (resume.get("qualifications_summary") or "").strip()
    experience     = (resume.get("experience_summary") or "").strip()
    skills         = ", ".join(resume.get("skills") or [])
    frameworks     = ", ".join(resume.get("frameworks") or [])
    text = f"{target_role} | {qualifications} | {experience} | {skills} | {frameworks}"
    # Simple truncation for resume (single document, not high-volume)
    tokens = model.tokenizer.encode(text, add_special_tokens=False)
    if len(tokens) > EMBEDDING_MAX_TOKENS:
        text = model.tokenizer.decode(tokens[:EMBEDDING_MAX_TOKENS], skip_special_tokens=True)
    return text


class Embedder:
    def __init__(self) -> None:
        self._model = _load_model()

    def embed_job(self, job: dict) -> list[float]:
        """Return a normalised 768-dim vector for a job dict."""
        text = _build_job_text(job, self._model)
        return self._model.encode(text, normalize_embeddings=True).tolist()

    def embed_resume(self, resume: dict) -> list[float]:
        """Return a normalised 768-dim vector for a resume dict."""
        text = _build_resume_text(resume, self._model)
        return self._model.encode(text, normalize_embeddings=True).tolist()

    def embed_text(self, text: str) -> list[float]:
        """Embed arbitrary text (used for candidate skill similarity detection)."""
        tokens = self._model.tokenizer.encode(text, add_special_tokens=False)
        if len(tokens) > EMBEDDING_MAX_TOKENS:
            text = self._model.tokenizer.decode(
                tokens[:EMBEDDING_MAX_TOKENS], skip_special_tokens=True
            )
        return self._model.encode(text, normalize_embeddings=True).tolist()
