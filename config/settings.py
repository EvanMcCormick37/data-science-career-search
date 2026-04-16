"""
Application settings — all values sourced from environment variables.
Copy .env.example → .env and fill in your credentials before running anything.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Project root is two levels up from this file (config/settings.py → root)
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

# ── Paths ──────────────────────────────────────────────────────────────────
DATA_DIR   = ROOT_DIR / "data"
DB_DIR     = ROOT_DIR / "db"
CONFIG_DIR = ROOT_DIR / "config"
MODELS_DIR = ROOT_DIR / "models"

RESUME_PATH          = DATA_DIR / "career_profile.md"
SKILLS_MD_PATH       = DATA_DIR / "skills.md"
FRAMEWORKS_MD_PATH   = DATA_DIR / "frameworks.md"
QUERIES_PATH         = CONFIG_DIR / "queries.yaml"

# ── Database ───────────────────────────────────────────────────────────────
DATABASE_URL: str = os.environ["DATABASE_URL"]

# ── SerpAPI ───────────────────────────────────────────────────────────────
SERPAPI_KEY: str = os.environ["SERPAPI_KEY"]

# ── OpenRouter ────────────────────────────────────────────────────────────
OPENROUTER_API_KEY: str  = os.environ["OPENROUTER_API_KEY"]
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

# Optional: Anthropic direct API key for Tier 3.  If unset, Tier 3 routes
# through OpenRouter using DEEP_ANALYSIS_MODEL.
ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY") or None

# ── Model identifiers (OpenRouter model IDs) ──────────────────────────────
EXTRACTION_MODEL:    str = os.getenv("EXTRACTION_MODEL",    "google/gemini-flash-1.5")
SCORING_MODEL:       str = os.getenv("SCORING_MODEL",       "google/gemini-flash-1.5")
DEEP_ANALYSIS_MODEL: str = os.getenv("DEEP_ANALYSIS_MODEL", "anthropic/claude-sonnet-4-5")

# ── Embedding ─────────────────────────────────────────────────────────────
# Large model — used for job and career-profile embeddings stored in the DB.
# EMBEDDING_MODEL is accepted as a legacy alias for EMBEDDING_MODEL_LARGE.
EMBEDDING_MODEL_LARGE: str = (
    os.getenv("EMBEDDING_MODEL_LARGE")
    or os.getenv("EMBEDDING_MODEL", "all-mpnet-base-v2")
)
# Small model — used for short-string similarity (skill/framework name comparison).
# Never stored in the DB; dimensions need not match EMBEDDING_DIM.
EMBEDDING_MODEL_SMALL: str = os.getenv("EMBEDDING_MODEL_SMALL", "all-MiniLM-L6-v2")

EMBEDDING_DIM:        int = int(os.getenv("EMBEDDING_DIM",        "768"))
EMBEDDING_MAX_TOKENS: int = int(os.getenv("EMBEDDING_MAX_TOKENS", "384"))

# ── Pipeline tuning ───────────────────────────────────────────────────────
DEDUP_FUZZY_THRESHOLD: int = int(os.getenv("DEDUP_FUZZY_THRESHOLD", "85"))
TIER1_CANDIDATES:      int = int(os.getenv("TIER1_CANDIDATES",      "100"))
TIER2_TOP_N:           int = int(os.getenv("TIER2_TOP_N",           "15"))
TIER2_CONCURRENCY:     int = int(os.getenv("TIER2_CONCURRENCY",     "10"))
DEEP_ANALYSIS_TOP_K:   int = int(os.getenv("DEEP_ANALYSIS_TOP_K",   "15"))
DAILY_MAX_PAGES:       int = int(os.getenv("DAILY_MAX_PAGES",       "1"))
BACKFILL_MAX_PAGES:    int = int(os.getenv("BACKFILL_MAX_PAGES",    "10"))
JOB_EXPIRY_DAYS:       int = int(os.getenv("JOB_EXPIRY_DAYS",       "30"))
