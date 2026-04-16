"""
Local embedding generation using sentence-transformers.

Two models are managed:

  EMBEDDING_MODEL_LARGE  (default: all-mpnet-base-v2)
    768 dimensions, 384-token context window.
    Used for job records and career-profile embeddings that are stored in the
    pgvector column.  Both sides of a similarity search must use this model.

  EMBEDDING_MODEL_SMALL  (default: all-MiniLM-L6-v2)
    384 dimensions, 256-token context window.
    Used for short-string similarity comparisons (e.g. skill/framework name
    deduplication in review_candidates.py).  Results are never stored in the DB.

Public API
──────────
  embed_job(job)              → 768-dim vector  (large model)
  embed_career_profile(cp)    → 768-dim vector  (large model)
  embed_text(text)            → vector           (auto-routed by word count:
                                                   ≤ SHORT_TEXT_WORD_LIMIT words
                                                   → small model, else large)

Composition strings:
  Job:    "{title} | {qualifications} | {responsibilities} | {skills} | {frameworks}"
  Resume: "{target_role} | {qualifications_summary} | {experience_summary} | {skills} | {frameworks}"

Truncation strategy when input exceeds the model's token limit:
  Fields are truncated in reverse priority order (lowest priority first):
    frameworks → skills → responsibilities → qualifications → title (never truncated)
"""
from __future__ import annotations

import logging
from functools import lru_cache

from sentence_transformers import SentenceTransformer

from config.settings import (
    EMBEDDING_MAX_TOKENS,
    EMBEDDING_MODEL_LARGE,
    EMBEDDING_MODEL_SMALL,
    MODELS_DIR,
)

logger = logging.getLogger(__name__)

# Word-count threshold below which embed_text routes to the small model.
# Skill and framework names are always ≤ 5 words; this gives generous headroom.
_SHORT_TEXT_WORD_LIMIT = 10


@lru_cache(maxsize=1)
def _load_large() -> SentenceTransformer:
    MODELS_DIR.mkdir(exist_ok=True)
    logger.info(f"Loading large embedding model {EMBEDDING_MODEL_LARGE!r} (cache: {MODELS_DIR}) …")
    return SentenceTransformer(EMBEDDING_MODEL_LARGE, cache_folder=str(MODELS_DIR))


@lru_cache(maxsize=1)
def _load_small() -> SentenceTransformer:
    MODELS_DIR.mkdir(exist_ok=True)
    logger.info(f"Loading small embedding model {EMBEDDING_MODEL_SMALL!r} (cache: {MODELS_DIR}) …")
    return SentenceTransformer(EMBEDDING_MODEL_SMALL, cache_folder=str(MODELS_DIR))


def _route(text: str, large: SentenceTransformer, small: SentenceTransformer) -> SentenceTransformer:
    """Return the small model for short strings, the large model for longer ones."""
    return small if len(text.split()) <= _SHORT_TEXT_WORD_LIMIT else large


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
    title            = (job.get("title") or "").strip()
    qualifications   = (job.get("qualifications") or "").strip()
    responsibilities = (job.get("responsibilities") or "").strip()
    skills           = ", ".join(job.get("skills_canonical") or [])
    frameworks       = ", ".join(job.get("frameworks_canonical") or [])

    separator_tokens = 4 * 3  # " | " between each of 5 fields ≈ 3 tokens each
    title_tokens = _token_count(title, model)
    budget = EMBEDDING_MAX_TOKENS - title_tokens - separator_tokens

    fields = [
        ("qualifications",   qualifications),
        ("responsibilities", responsibilities),
        ("skills",           skills),
        ("frameworks",       frameworks),
    ]
    token_counts = {name: _token_count(text, model) for name, text in fields}
    total = sum(token_counts.values())

    if total > budget:
        # Truncate in reverse priority order (frameworks first, qualifications last)
        for name in ["frameworks", "skills", "responsibilities", "qualifications"]:
            excess = total - budget
            if excess <= 0:
                break
            cut = min(excess, token_counts[name])
            token_counts[name] -= cut
            total -= cut

        truncated = {name: _truncate_field(text, token_counts[name], model)
                     for name, text in fields}
        qualifications   = truncated["qualifications"]
        responsibilities = truncated["responsibilities"]
        skills           = truncated["skills"]
        frameworks       = truncated["frameworks"]

    return f"{title} | {qualifications} | {responsibilities} | {skills} | {frameworks}"


def _build_career_profile_text(career_profile: dict, model: SentenceTransformer) -> str:
    target_role    = (career_profile.get("target_role") or "").strip()
    qualifications = (career_profile.get("qualifications_summary") or "").strip()
    experience     = (career_profile.get("experience_summary") or "").strip()
    skills         = ", ".join(career_profile.get("skills") or [])
    frameworks     = ", ".join(career_profile.get("frameworks") or [])
    text = f"{target_role} | {qualifications} | {experience} | {skills} | {frameworks}"
    tokens = model.tokenizer.encode(text, add_special_tokens=False)
    if len(tokens) > EMBEDDING_MAX_TOKENS:
        text = model.tokenizer.decode(tokens[:EMBEDDING_MAX_TOKENS], skip_special_tokens=True)
    return text


class Embedder:
    """
    Wraps both embedding models.

    The large model is loaded lazily on first call to embed_job or
    embed_career_profile.  The small model is loaded lazily on first call to
    embed_text with a short string.  Neither model is loaded until needed.
    """

    def __init__(self) -> None:
        # Models are loaded on first access via the module-level lru_cache loaders.
        pass

    # ── Large-model methods (DB-bound) ────────────────────────────────────

    def embed_job(self, job: dict) -> list[float]:
        """Return a normalised 768-dim vector for a job dict (large model)."""
        model = _load_large()
        text = _build_job_text(job, model)
        return model.encode(text, normalize_embeddings=True).tolist()

    def embed_career_profile(self, career_profile: dict) -> list[float]:
        """Return a normalised 768-dim vector for a career_profile dict (large model)."""
        model = _load_large()
        text = _build_career_profile_text(career_profile, model)
        return model.encode(text, normalize_embeddings=True).tolist()

    # ── Auto-routed method ────────────────────────────────────────────────

    def embed_text(self, text: str) -> list[float]:
        """
        Embed arbitrary text, routing to the most appropriate model.

        Strings with ≤ SHORT_TEXT_WORD_LIMIT words (skill/framework names,
        short labels) use the small model.  Longer strings use the large model.

        Note: never mix embed_text results with embed_job / embed_career_profile
        results in a cosine similarity comparison — the vectors are produced by
        different models and are not comparable.
        """
        large = _load_large()
        small = _load_small()
        model = _route(text, large, small)
        max_tokens = model.get_max_seq_length()
        tokens = model.tokenizer.encode(text, add_special_tokens=False)
        if len(tokens) > max_tokens:
            text = model.tokenizer.decode(tokens[:max_tokens], skip_special_tokens=True)
        return model.encode(text, normalize_embeddings=True).tolist()
