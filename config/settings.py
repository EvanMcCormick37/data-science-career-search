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

RESUME_PATH          = DATA_DIR / "resume.md"
SKILLS_MD_PATH       = DATA_DIR / "skills.md"
FRAMEWORKS_MD_PATH   = DATA_DIR / "frameworks.md"
QUERIES_PATH         = CONFIG_DIR / "queries.yaml"
BACKFILL_STATE_PATH  = DATA_DIR / "backfill_state.json"

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
EMBEDDING_MODEL:      str = os.getenv("EMBEDDING_MODEL",      "all-mpnet-base-v2")
EMBEDDING_DIM:        int = int(os.getenv("EMBEDDING_DIM",      "768"))
EMBEDDING_MAX_TOKENS: int = int(os.getenv("EMBEDDING_MAX_TOKENS", "384"))

# ── Pipeline tuning ───────────────────────────────────────────────────────
DEDUP_FUZZY_THRESHOLD: int = int(os.getenv("DEDUP_FUZZY_THRESHOLD", "85"))
TIER1_CANDIDATES:      int = int(os.getenv("TIER1_CANDIDATES",      "100"))
TIER2_TOP_N:           int = int(os.getenv("TIER2_TOP_N",           "15"))
TIER2_CONCURRENCY:     int = int(os.getenv("TIER2_CONCURRENCY",     "10"))
DAILY_MAX_PAGES:       int = int(os.getenv("DAILY_MAX_PAGES",       "2"))
BACKFILL_MAX_PAGES:    int = int(os.getenv("BACKFILL_MAX_PAGES",    "10"))
JOB_EXPIRY_DAYS:       int = int(os.getenv("JOB_EXPIRY_DAYS",       "30"))
