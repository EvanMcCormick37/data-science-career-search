# Job Search Pipeline — Architecture

## Overview

A Python-based pipeline that ingests job listings from SerpAPI (Google Jobs), extracts structured metadata via a cheap LLM, scores every job for career fit at ingestion time, stores everything in PostgreSQL (with pgvector), and supports two use cases:

1. **Personal job filtering** — surface the best-fit jobs using a two-stage scoring approach: cheap LLM fit scoring at ingestion, expensive LLM deep analysis on demand for the top candidates.
2. **Public dataviz** (future, do not build) — surface job market trends (in-demand skills, frameworks, salary distributions) on a web frontend.

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           INGESTION PIPELINE                             │
│                        (Python, scheduled daily)                         │
│                                                                          │
│  ┌──────────┐  ┌────────┐  ┌───────────┐  ┌────────────┐  ┌──────────┐  │
│  │ SerpAPI  │─▶│ Dedup  │─▶│  Extract  │─▶│  Embed +   │─▶│  Score   │  │
│  │ Fetcher  │  │ (Fuzzy)│  │ (cheap    │  │  Normalize │  │  (cheap  │  │
│  │          │  │        │  │  LLM)     │  │            │  │   LLM)   │  │
│  └──────────┘  └────────┘  └───────────┘  └────────────┘  └────┬─────┘  │
│                                                                  │       │
│                                                                  ▼       │
│                                                           Store to PG    │
│                                                      (tier2_score saved  │
│                                                        with each job)    │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│                        NORMALIZATION LAYER                               │
│                                                                          │
│  LLM returns skill/framework name                                        │
│       │                                                                  │
│       ▼                                                                  │
│  Check alias table → canonical match found? → insert canonical ID        │
│       │ no match                                                         │
│       ▼                                                                  │
│  Check canonical names directly → exact match? → insert skill ID         │
│       │ no match                                                         │
│       ▼                                                                  │
│  Insert as candidate (is_candidate = 1)                                  │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│                         MATCHING — PRIMARY PATH                          │
│                     (scripts/score_top_jobs.py)                          │
│                                                                          │
│  Query DB: ORDER BY tier2_score DESC                                     │
│       │    (scores pre-computed at ingestion)                            │
│       ▼                                                                  │
│  Top K jobs ──▶ Expensive LLM deep analysis ──▶ Ranked shortlist         │
│                 (DEEP_ANALYSIS_MODEL)             with strengths, gaps,  │
│                                                   recommendation,        │
│                                                   resume tips            │
│                                                                          │
│  Results persisted to tier3_score / tier3_explanation                    │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│                       MATCHING — AD-HOC PATH                             │
│                   (scripts/match_career_profile.py)                      │
│                                                                          │
│  ┌──────────┐  ┌──────────────┐  ┌────────────┐  ┌───────────────────┐  │
│  │  Embed   │─▶│  pgvector    │─▶│ Cheap LLM  │─▶│  Expensive LLM   │  │
│  │  Career  │  │  Top 100     │  │ re-score   │  │  Deep Analysis   │  │
│  │  Profile │  │ (cosine sim) │  │ (optional) │  │  (optional)      │  │
│  └──────────┘  └──────────────┘  └────────────┘  └───────────────────┘  │
│                                                                          │
│  --tier 1: vector search only (instant)                                  │
│  --tier 2: vector → cheap LLM re-score                                   │
│  --tier 3: full pipeline (vector → cheap LLM → expensive LLM)           │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Technology Stack

| Component              | Choice                          | Rationale                                                          |
| ---------------------- | ------------------------------- | ------------------------------------------------------------------ |
| Language               | Python 3.11+                    | Ecosystem for ML/NLP, rapid prototyping                            |
| Database               | PostgreSQL 16 + pgvector        | Single DB for relational data AND vector search                    |
| Embedding model (large)| all-mpnet-base-v2 (local)       | 768-dim, 384-token window. Used for job + career profile vectors stored in DB |
| Embedding model (small)| all-MiniLM-L6-v2 (local)        | 384-dim. Used for short-string similarity (skill/framework name dedup). Never stored in DB |
| Model cache            | `models/` directory             | sentence-transformers downloads on first use, loads from disk thereafter |
| Extraction LLM         | Cheap model via OpenRouter      | Structured metadata extraction at ~$0.001/job. Swap by changing config |
| Fit scoring LLM        | Same cheap model (SCORING_MODEL)| Scores every job at ingestion time. Pre-populates tier2_score in DB |
| Deep analysis LLM      | Expensive model via OpenRouter  | On-demand deep analysis for top-K jobs. Defaults to claude-sonnet  |
| Task scheduling        | cron (local) or APScheduler     | Daily trigger, no need for Celery at this scale                    |
| Future frontend        | JS (React or Svelte) + D3.js    | Dataviz flexibility (DO NOT BUILD YET)                             |

### Why PostgreSQL + pgvector (not a separate VectorDB)

At ≤10K jobs, pgvector with HNSW indexing handles similarity search in <100ms. A dedicated vector store (Pinecone, Qdrant, etc.) would add: a second data store to keep in sync, a second backup strategy, cross-system joins for filtering, and additional cost. One database, one connection, one backup.

### Why OpenRouter

OpenRouter provides a unified API across dozens of model providers. This gives you:

- A single integration point — swap models by changing a config string, not rewriting API calls.
- Access to the cheapest available models (Gemini Flash, Kimi K2, DeepSeek, etc.) without separate API keys for each.
- A natural A/B testing setup: run the same prompt against two models and compare output quality.

### Why two embedding models

Job records and career profile embeddings are stored in the pgvector column and compared against each other — they must use the same model (all-mpnet-base-v2, 768-dim). Skill and framework name similarity (used in `review_candidates.py`) only requires short-string comparison that is never stored in the DB. all-MiniLM-L6-v2 handles this at half the size. Loading the large model solely for 1–5 word strings would be wasteful. `embed_text()` auto-routes to the small model for strings ≤ 10 words.

---

## Database Schema

```sql
-- Extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- for fuzzy text matching


-- ============================================================
-- CORE JOBS TABLE
-- ============================================================

CREATE TABLE IF NOT EXISTS jobs (
    job_id                SERIAL PRIMARY KEY,
    title                 TEXT NOT NULL,
    url                   TEXT NOT NULL,
    company_name          TEXT NOT NULL,
    location              TEXT,
    description           TEXT,
    employment_type       TEXT,                -- 'full-time', 'part-time', 'contract', 'internship'
    attendance            TEXT,                -- 'remote', 'hybrid', 'onsite'
    seniority             TEXT,                -- 'junior', 'mid', 'senior', 'lead', 'staff', 'principal'
    experience_years_min  INTEGER,
    experience_years_max  INTEGER,
    salary_min            INTEGER,
    salary_max            INTEGER,
    salary_currency       TEXT DEFAULT 'USD',
    salary_period         TEXT,                -- 'yearly', 'hourly', 'monthly'
    qualifications        TEXT,                -- raw extracted text from SerpAPI
    responsibilities      TEXT,               -- raw extracted text from SerpAPI
    date_listed           DATE,
    date_ingested         TIMESTAMP DEFAULT NOW(),
    date_updated          TIMESTAMP DEFAULT NOW(),
    status                TEXT DEFAULT 'active',  -- 'active', 'expired', 'duplicate', 'extraction_failed'
    serp_api_json         JSONB,               -- full raw response for auditability and reprocessing
    embedding             vector(768),         -- all-mpnet-base-v2 output
    dedup_hash            TEXT UNIQUE,         -- SHA-256 of normalised title+company+location

    -- Fit scores
    tier2_score           REAL,                -- cheap LLM score, populated at ingestion
    tier2_explanation     TEXT,
    tier3_score           REAL,                -- expensive LLM score, populated on demand
    tier3_explanation     TEXT,

    -- Application tracking (NULL until an application is submitted)
    application_id        INTEGER REFERENCES applications(application_id) ON DELETE SET NULL
);


-- ============================================================
-- APPLICATIONS TABLE
-- ============================================================

CREATE TABLE IF NOT EXISTS applications (
    application_id   SERIAL PRIMARY KEY,
    job_id           INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    date_applied     DATE,
    assistance_level TEXT CHECK (assistance_level IN ('ai', 'assisted', 'human')),
    cover_letter     TEXT,
    resume           TEXT,
    cold_calls       INTEGER DEFAULT 0,   -- number of cold outreach attempts
    reached_human    INTEGER DEFAULT 0,   -- boolean: 1 = spoke to a real person
    interviews       INTEGER DEFAULT 0,   -- number of interview rounds completed
    offer            INTEGER DEFAULT 0    -- boolean: 1 = offer received
);

CREATE INDEX IF NOT EXISTS idx_applications_job ON applications (job_id);


-- ============================================================
-- SKILLS TAXONOMY
-- ============================================================

CREATE TABLE IF NOT EXISTS skills (
    skill_id        SERIAL PRIMARY KEY,
    domain          TEXT NOT NULL,          -- top-level category
    core_competency TEXT,                   -- grouping within domain
    competency      TEXT,                   -- sub-grouping
    name            TEXT UNIQUE NOT NULL,   -- canonical name
    is_candidate    INTEGER DEFAULT 0       -- 1 = proposed by LLM, not yet accepted
);

CREATE TABLE IF NOT EXISTS skill_aliases (
    alias    TEXT PRIMARY KEY,
    skill_id INTEGER NOT NULL REFERENCES skills(skill_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS job_skills (
    job_id   INTEGER REFERENCES jobs(job_id) ON DELETE CASCADE,
    skill_id INTEGER REFERENCES skills(skill_id) ON DELETE CASCADE,
    PRIMARY KEY (job_id, skill_id)
);


-- ============================================================
-- FRAMEWORKS TAXONOMY
-- ============================================================

CREATE TABLE IF NOT EXISTS frameworks (
    framework_id    SERIAL PRIMARY KEY,
    domain          TEXT NOT NULL,
    subdomain       TEXT,
    service         TEXT,
    name            TEXT UNIQUE NOT NULL,
    is_candidate    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS framework_aliases (
    alias        TEXT PRIMARY KEY,
    framework_id INTEGER NOT NULL REFERENCES frameworks(framework_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS job_frameworks (
    job_id       INTEGER REFERENCES jobs(job_id) ON DELETE CASCADE,
    framework_id INTEGER REFERENCES frameworks(framework_id) ON DELETE CASCADE,
    PRIMARY KEY (job_id, framework_id)
);


-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_jobs_embedding    ON jobs USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_jobs_status       ON jobs (status);
CREATE INDEX IF NOT EXISTS idx_jobs_tier2_score  ON jobs (tier2_score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_date_listed  ON jobs (date_listed);
CREATE INDEX IF NOT EXISTS idx_jobs_dedup_hash   ON jobs (dedup_hash);
CREATE INDEX IF NOT EXISTS idx_jobs_company_trgm ON jobs USING gin (company_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_jobs_title_trgm   ON jobs USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_skill_aliases_skill          ON skill_aliases (skill_id);
CREATE INDEX IF NOT EXISTS idx_framework_aliases_framework  ON framework_aliases (framework_id);
```

### Schema Design Notes

**`serp_api_json`:** The full raw SerpAPI response is preserved so you can re-extract metadata when your extraction prompt improves, without re-hitting the API. This is your audit trail and reprocessing safety net.

**`tier2_score` / `tier2_explanation`:** Populated eagerly at ingestion by the cheap LLM scorer (`pipeline/scorer.py`). Every active job in the DB has a fit score as soon as it is inserted. If the career profile (`data/career_profile.md`) is missing or still a placeholder, scoring is silently skipped and the columns remain NULL.

**`tier3_score` / `tier3_explanation`:** Populated on demand by `scripts/score_top_jobs.py` when you run the expensive LLM over the top-K candidates.

**`application_id` back-pointer:** `jobs.application_id` references `applications` and is NULL until you submit an application for that role. The circular FK (`applications.job_id → jobs`, `jobs.application_id → applications`) is resolved by adding `application_id` via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, which runs after both tables are created.

**`is_candidate` flag:** Skills and frameworks extracted by the LLM that don't match any canonical name or known alias are inserted with `is_candidate = 1`. They participate in job-skill linkage immediately (no data is lost), but are excluded from dataviz aggregation until reviewed via `review_candidates.py`.

**Alias tables vs. in-memory normalization:** Alias tables live in Postgres for durability and easy manual curation. At startup, the normalizer loads the full alias mapping into Python dicts for fast in-process lookup. The DB is the source of truth; the dict is a cache.

---

## Ingestion Pipeline Detail

### Step 1: SerpAPI Fetch

```
pipeline/fetcher.py
├── Accepts: queries from config/queries.yaml (role × location combinations)
│           OR a one-off query dict from scripts/single_query.py
├── Modes:
│   ├── daily    — DAILY_MAX_PAGES pages per query (new listings only)
│   ├── backfill — up to BACKFILL_MAX_PAGES pages; resumes from state file on restart
│   └── ad-hoc   — single query, max_pages set by caller (single_query.py)
├── Rate limiting: 0.5s sleep between pages; stays under ~30 queries/day in daily mode
└── Outputs: generator of raw job dicts, each with serp_api_json attached
```

### Step 2: Fuzzy Dedup

```
pipeline/dedup.py
├── Normalize: lowercase, strip punctuation ("Sr." → "senior", "Eng." → "engineer")
├── Generate dedup_hash: SHA-256(normalized_title | normalized_company | normalized_location)
├── Check exact hash match in DB → duplicate: skip
└── Secondary fuzzy check (same company via pg_trgm, thefuzz title ratio ≥ 85) → duplicate: skip
```

### Step 3: LLM Metadata Extraction

```
pipeline/extractor.py
├── Model: EXTRACTION_MODEL (cheap, via OpenRouter)
├── System prompt: skills.md + frameworks.md as reference taxonomy
├── Extracts: employment_type, attendance, seniority, experience_years_min/max,
│            salary_min/max/currency/period, skills[], frameworks[]
├── Validation: enum fields coerced to known values or null; lists stripped of non-strings
└── Error handling: retry once on failure; mark as 'extraction_failed' after two failures
```

### Step 3a: Skill & Framework Normalization

```
pipeline/normalizer.py
├── On startup: load full alias tables from DB into in-memory dicts (one read, cached)
├── For each name: alias lookup → exact name lookup → insert as candidate (is_candidate=1)
└── Output: resolved skill_ids and framework_ids for junction table insertion
```

### Step 4: Embed

```
pipeline/embedder.py — embed_job()
├── Model: EMBEDDING_MODEL_LARGE (all-mpnet-base-v2), loaded from models/ cache
├── Composition: "{title} | {qualifications} | {responsibilities} | {skills} | {frameworks}"
└── Truncation: lowest-priority fields (frameworks → skills → responsibilities → qualifications)
               trimmed first when total exceeds EMBEDDING_MAX_TOKENS (384)
```

### Step 5: Fit Scoring

```
pipeline/scorer.py — IngestScorer.score()
├── Model: SCORING_MODEL (cheap, same as EXTRACTION_MODEL by default)
├── Career profile: loaded once from data/career_profile.md and cached
├── Scores 0–100 with a 1–5 sentence explanation
├── Returns (None, None) if career profile is missing/placeholder → job stored with NULL score
└── Output: tier2_score + tier2_explanation, stored with the job record
```

### Step 6: Store

```
db/operations.py — insert_job()
├── INSERT into jobs: all extracted fields + embedding + tier2_score + tier2_explanation
└── INSERT into job_skills / job_frameworks junction tables
```

---

## Matching Detail

### Primary path — `scripts/score_top_jobs.py`

Because every job is scored at ingestion, the most common workflow requires no embedding at query time:

```
db/operations.py — get_top_scored_jobs(top_k, min_score)
├── SELECT top_k active jobs WHERE tier2_score >= min_score ORDER BY tier2_score DESC
└── Pass to matching/tier3_deep_analysis.py — analyse_batch()
    ├── Model: DEEP_ANALYSIS_MODEL (expensive; defaults to claude-sonnet via OpenRouter)
    ├── Per-job output: fit_score, explanation, strengths, gaps, recommendation, resume tips
    └── Persists tier3_score + tier3_explanation to DB
```

Usage:
```
python scripts/score_top_jobs.py               # top 15 by tier2_score
python scripts/score_top_jobs.py --top-k 20
python scripts/score_top_jobs.py --min-score 60
python scripts/score_top_jobs.py --no-persist
python scripts/score_top_jobs.py --output results.json
```

### Ad-hoc path — `scripts/match_career_profile.py`

For exploration or re-scoring against an updated career profile without re-running ingestion:

**Tier 1 — Vector similarity (free, instant)**
```sql
SELECT job_id, ..., 1 - (embedding <=> :career_embedding) AS cosine_similarity
FROM jobs WHERE status = 'active' AND embedding IS NOT NULL
ORDER BY embedding <=> :career_embedding LIMIT 100;
```
Career profile is embedded with the large model using the same composition template as jobs.

**Tier 2 — Cheap LLM re-score (optional, asyncio + httpx)**
Runs concurrently over the Tier 1 candidates. Updates `tier2_score` / `tier2_explanation` in the DB. ~15–30s for 100 jobs at negligible cost.

**Tier 3 — Expensive LLM deep analysis (optional)**
Same as the primary path. Operates on the Tier 2 top-K.

---

## Candidate Taxonomy Review

`scripts/review_candidates.py` handles the ongoing curation loop for LLM-proposed skill/framework candidates (`is_candidate = 1`).

**Interactive actions per candidate:** promote / merge / discard / skip / quit.

**Similarity detection:** Before the review loop, all canonical entries are embedded using the small embedding model (`all-MiniLM-L6-v2`). For each candidate, the top-K most similar canonical entries (default 5) are displayed — no threshold, always shows K results.

**Promote flow:** Cascading menu driven by the live taxonomy from the DB:
- Skills: domain → core_competency → competency (each level shows existing values + "add new")
- Frameworks: domain → subdomain → service (service is optional; "none" offered when applicable)

---

## Project Structure

```
data-science-career-search/
├── config/
│   ├── settings.py              # All config from env vars (.env)
│   ├── queries.yaml             # Search query definitions (roles × locations)
│   └── queries.example.yaml     # Template — copy to queries.yaml
├── pipeline/
│   ├── fetcher.py               # SerpAPI ingestion (daily / backfill / ad-hoc)
│   ├── dedup.py                 # Fuzzy deduplication
│   ├── extractor.py             # LLM metadata extraction via OpenRouter
│   ├── normalizer.py            # Skill/framework alias resolution + candidate insertion
│   ├── embedder.py              # Two-model embedding (large for DB, small for similarity)
│   ├── scorer.py                # Ingestion-time fit scoring (cheap LLM)
│   └── orchestrator.py          # Orchestrates steps 1–6; supports reprocess mode
├── matching/
│   ├── tier1_vector.py          # pgvector cosine similarity search
│   ├── tier2_cheap_llm.py       # Ad-hoc batch cheap LLM scoring (asyncio)
│   └── tier3_deep_analysis.py   # Expensive LLM deep fit analysis
├── llm/
│   └── client.py                # Thin OpenRouter wrapper (sync + async, JSON mode)
├── db/
│   ├── schema.sql               # Full DDL (idempotent; run via seed.py)
│   ├── connection.py            # Threaded psycopg2 connection pool
│   ├── operations.py            # All SQL reads/writes (no raw SQL elsewhere)
│   └── seed/
│       ├── seed.py              # Bootstrap: runs schema.sql + seeds taxonomy CSVs
│       ├── skills.csv           # Canonical skills taxonomy
│       ├── frameworks.csv       # Canonical frameworks taxonomy
│       ├── skill_aliases.csv    # Known variant → canonical mappings (~160 entries)
│       └── framework_aliases.csv# Known variant → canonical mappings (~140 entries)
├── scripts/
│   ├── backfill.py              # One-time historical ingestion (all queries.yaml)
│   ├── daily_run.py             # Daily cron entry point (expire + fetch + ingest)
│   ├── single_query.py          # Ad-hoc ingestion for a single search query
│   ├── reprocess.py             # Re-run extraction on stored serp_api_json
│   ├── score_top_jobs.py        # Expensive LLM deep analysis on top-K by tier2_score
│   ├── match_career_profile.py  # Ad-hoc 3-tier matching (vector → cheap LLM → expensive LLM)
│   └── review_candidates.py     # Interactive taxonomy curation for LLM-proposed candidates
├── data/
│   ├── career_profile.md        # Your resume/career profile (used for scoring)
│   ├── skills.md                # Skill taxonomy reference (included in extraction prompt)
│   └── frameworks.md            # Framework taxonomy reference (included in extraction prompt)
├── models/                      # sentence-transformers model cache (gitignored)
├── tests/
├── requirements.txt
└── README.md
```

---

## Environment Variables (`.env`)

| Variable               | Default                        | Purpose                                      |
| ---------------------- | ------------------------------ | -------------------------------------------- |
| `DATABASE_URL`         | *(required)*                   | PostgreSQL connection string                 |
| `SERPAPI_KEY`          | *(required)*                   | SerpAPI API key                              |
| `OPENROUTER_API_KEY`   | *(required)*                   | OpenRouter API key                           |
| `EXTRACTION_MODEL`     | `google/gemini-flash-1.5`      | LLM for metadata extraction                  |
| `SCORING_MODEL`        | `google/gemini-flash-1.5`      | LLM for ingestion-time fit scoring           |
| `DEEP_ANALYSIS_MODEL`  | `anthropic/claude-sonnet-4-5`  | LLM for expensive deep analysis              |
| `EMBEDDING_MODEL_LARGE`| `all-mpnet-base-v2`            | Large embedding model (jobs + career profile)|
| `EMBEDDING_MODEL_SMALL`| `all-MiniLM-L6-v2`             | Small embedding model (skill name similarity)|
| `EMBEDDING_DIM`        | `768`                          | Vector dimension (must match large model)    |
| `EMBEDDING_MAX_TOKENS` | `384`                          | Max tokens for large model truncation        |
| `TIER1_CANDIDATES`     | `100`                          | Vector search result limit                   |
| `TIER2_TOP_N`          | `15`                           | Top-N passed to Tier 3 in ad-hoc flow        |
| `TIER2_CONCURRENCY`    | `10`                           | Async concurrency for ad-hoc cheap LLM calls |
| `DEEP_ANALYSIS_TOP_K`  | `15`                           | Default K for score_top_jobs.py              |
| `DAILY_MAX_PAGES`      | `1`                            | SerpAPI pages per query in daily mode        |
| `BACKFILL_MAX_PAGES`   | `10`                           | SerpAPI pages per query in backfill mode     |
| `JOB_EXPIRY_DAYS`      | `30`                           | Days before active listings are marked expired|
| `DEDUP_FUZZY_THRESHOLD`| `85`                           | thefuzz token_sort_ratio threshold           |
| `ANTHROPIC_API_KEY`    | *(optional)*                   | Direct Anthropic key (bypasses OpenRouter)   |

---

## Cost Estimates (Monthly, Steady-State)

| Item                              | Volume             | Est. Cost          |
| --------------------------------- | ------------------ | ------------------ |
| SerpAPI                           | ~900 searches      | $25.00 (plan)      |
| Cheap model — extraction          | ~9,000 jobs        | ~$0.50–1.00        |
| Cheap model — ingestion scoring   | ~9,000 jobs        | ~$0.50–1.00        |
| Expensive LLM — deep analysis     | ~15 jobs/run       | ~$0.50–1.00/run    |
| Embedding models (local)          | —                  | $0                 |
| PostgreSQL (local)                | —                  | $0                 |
| **Total (excl. deep analysis)**   |                    | **~$27–28/mo**     |

The main change from the original cost model is that cheap LLM scoring now runs on every ingested job (~9,000/month) rather than on ~100 candidates per query run. At ~$0.001/call this adds roughly $9/month but eliminates per-query scoring latency.

---

## Setup Sequence

1. Copy `.env.example` → `.env` and fill in `DATABASE_URL`, `SERPAPI_KEY`, `OPENROUTER_API_KEY`
2. `python -m db.seed.seed` — creates schema and seeds taxonomy
3. Copy `config/queries.example.yaml` → `config/queries.yaml`, add your target searches
4. Fill in `data/career_profile.md` with your real resume (required for ingestion-time scoring)
5. `python scripts/backfill.py` — historical ingestion (downloads models on first run to `models/`)
6. `python scripts/daily_run.py` — daily cron target
7. `python scripts/score_top_jobs.py` — run expensive LLM over the top-K pre-scored jobs

---

## Open Decisions / Future Work

1. **System prompt size monitoring:** Track the token count of `skills.md` + `frameworks.md` as the taxonomy grows. If combined size exceeds ~1.5K tokens, trim the system prompt to a flat name list and keep hierarchy metadata in the database only.

2. **Embedding model upgrade path:** If retrieval quality disappoints, swap `EMBEDDING_MODEL_LARGE` to a larger model (e.g. `bge-large-en-v1.5`, 1024-dim). This requires re-embedding the full corpus from stored text, altering the `vector(768)` column dimension, and rebuilding the HNSW index. The stored `serp_api_json` makes re-embedding possible without re-fetching from SerpAPI.

3. **Application tracking UI:** The `applications` table exists but there is no script or interface for creating/updating application records. A simple CLI script (`scripts/log_application.py`) or a minimal web form would close this gap.

4. **Public website (Goal 2, do not build yet):**
   - API layer (FastAPI) serving aggregated skill/framework counts, trend-over-time, salary distributions.
   - Visualization queries are trivial with the normalized schema:
     ```sql
     SELECT s.name, COUNT(*) AS demand
     FROM job_skills js
     JOIN skills s ON s.skill_id = js.skill_id
     JOIN jobs j ON j.job_id = js.job_id
     WHERE j.status = 'active'
       AND j.date_listed > NOW() - INTERVAL '30 days'
       AND s.is_candidate = 0
     GROUP BY s.name ORDER BY demand DESC LIMIT 25;
     ```
   - User resume matching would expose the vector search + cheap LLM scoring only; expensive LLM behind auth/payment.
