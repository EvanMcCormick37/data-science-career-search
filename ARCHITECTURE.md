# Architecture

## Overview

A Python-based job search system with two entry points:

1. **CLI pipeline** — ingests job listings from SerpAPI (Google Jobs), extracts structured metadata via a cheap LLM, scores every job for career fit, and stores results in PostgreSQL with pgvector.
2. **Local dashboard** — a FastAPI web app for browsing jobs, tracking applications, and editing config.

Both systems share one database and one SQL layer (`db/operations.py`). The pipeline is entirely CLI-driven; the dashboard reads and writes through the same operations layer without importing any pipeline code.

---

## System Map

```
┌──────────────────────────────────────────────────────┐
│                  INGESTION PIPELINE                  │
│               (scripts/daily_run.py etc.)            │
│                                                      │
│  SerpAPI → Dedup → Extract → Normalize → Embed       │
│         → Score (cheap LLM) → Store                  │
│         → Auto Tier3 (if score >= threshold)         │
└──────────────────────┬───────────────────────────────┘
                       │ reads/writes
                       ▼
              ┌─────────────────┐
              │   PostgreSQL    │
              │  + pgvector     │
              └────────┬────────┘
                       │ reads/writes
┌──────────────────────▼───────────────────────────────┐
│                    DASHBOARD (app/)                   │
│           FastAPI + Jinja2 + HTMX + Alpine           │
│                                                      │
│  /jobs          — filterable job browser             │
│  /applications  — application tracker                │
│  /config        — edit queries.yaml + career profile │
└──────────────────────────────────────────────────────┘
```

---

## Technology Stack

| Component              | Choice                       | Notes                                                            |
| ---------------------- | ---------------------------- | ---------------------------------------------------------------- |
| Language               | Python 3.11+                 |                                                                  |
| Database               | PostgreSQL 17 + pgvector     | Docker via `docker-compose.yml`                                  |
| Embedding (large)      | all-mpnet-base-v2 (local)    | 768-dim. Used for job + career profile vectors stored in DB      |
| Embedding (small)      | all-MiniLM-L6-v2 (local)     | 384-dim. Used only for skill/framework name similarity, never stored |
| Model cache            | `models/` directory          | HuggingFace downloads on first use, loads from disk thereafter  |
| Extraction LLM         | Cheap model via OpenRouter   | Default: `google/gemini-flash-1.5`                              |
| Scoring LLM            | Same cheap model             | Runs at ingestion time; default: `google/gemini-flash-1.5`      |
| Deep analysis LLM      | Expensive model via OpenRouter | Default: `anthropic/claude-sonnet-4-5`                        |
| Dashboard backend      | FastAPI + Jinja2             | Single `uvicorn` process                                         |
| Dashboard frontend     | HTMX + Alpine.js + Tailwind  | All loaded via CDN — no build step                               |

---

## Project Structure

```
data-science-career-search/
├── config/
│   ├── settings.py              # All config from env vars (.env)
│   ├── queries.yaml             # Search query definitions (roles × locations)
│   └── queries.example.yaml     # Template — copy to queries.yaml
├── pipeline/
│   ├── fetcher.py               # SerpAPI fetch (daily / backfill / ad-hoc modes)
│   ├── dedup.py                 # Fuzzy deduplication
│   ├── extractor.py             # LLM metadata extraction via OpenRouter
│   ├── normalizer.py            # Skill/framework alias resolution + candidate insertion
│   ├── embedder.py              # Two-model embedding
│   ├── scorer.py                # Ingestion-time fit scoring (cheap LLM)
│   └── orchestrator.py          # Orchestrates fetch → store; triggers auto tier3
├── matching/
│   ├── tier1_vector.py          # pgvector cosine similarity search
│   ├── tier2_cheap_llm.py       # Async batch cheap LLM scoring
│   └── tier3_deep_analysis.py   # Expensive LLM deep fit analysis
├── llm/
│   └── client.py                # Thin OpenRouter wrapper (sync + async, JSON mode)
├── db/
│   ├── schema.sql               # Full DDL (idempotent; run via seed.py)
│   ├── connection.py            # Threaded psycopg2 connection pool
│   ├── operations.py            # All SQL reads/writes (no raw SQL elsewhere)
│   ├── migrations/              # Incremental schema changes (001–004)
│   └── seed/
│       ├── seed.py              # Bootstrap: runs schema.sql + seeds taxonomy CSVs
│       ├── skills.csv           # Canonical skills taxonomy
│       ├── frameworks.csv       # Canonical frameworks taxonomy
│       ├── skill_aliases.csv    # Known variant → canonical mappings
│       └── framework_aliases.csv
├── app/                         # Dashboard (FastAPI)
│   ├── main.py                  # App factory, router registration, freshness cache
│   ├── templating.py            # Jinja2 environment setup
│   ├── routes/
│   │   ├── jobs.py              # GET /jobs, GET /jobs/{id}/detail, PATCH /jobs/{id}/status
│   │   ├── applications.py      # Applications browser + detail + edit
│   │   ├── actions.py           # Cross-cutting writes (log application)
│   │   └── config_editor.py     # queries.yaml + career_profile.md editors
│   ├── services/
│   │   ├── jobs.py              # Filter/sort/paginate composition
│   │   ├── applications.py      # Log/edit application logic, resume resolution
│   │   └── config_files.py      # YAML/Markdown read/validate/write
│   ├── templates/               # Jinja2 templates (full pages + HTMX partials)
│   │   ├── base.html
│   │   ├── jobs/                # index.html, _table.html, _row.html, _detail.html
│   │   ├── applications/        # index.html, _table.html, _row.html, _detail.html, _new_form.html
│   │   └── config/              # index.html, queries.html, career_profile.html
│   └── static/
│       ├── app.css
│       └── app.js
├── scripts/
│   ├── daily_run.py             # Cron entry point: expire + fetch + ingest
│   ├── backfill.py              # Historical ingestion across all queries
│   ├── single_query.py          # Ad-hoc ingestion for one search query
│   ├── reprocess.py             # Re-run extraction on stored serp_api_json
│   ├── score_top_jobs.py        # Tier3 deep analysis on top-K by tier2_score
│   ├── match_career_profile.py  # Ad-hoc 3-tier matching (vector → cheap LLM → expensive LLM)
│   ├── review_candidates.py     # Interactive taxonomy curation for LLM-proposed candidates
│   ├── test_pipeline.py         # Pipeline smoke test
│   ├── backup_db.sh             # Database backup
│   └── teardown.sh              # Teardown script
├── data/
│   ├── career_profile.md        # Resume/career profile (required for scoring)
│   ├── skills.md                # Skill taxonomy reference (included in extraction prompt)
│   ├── frameworks.md            # Framework taxonomy reference (included in extraction prompt)
│   └── resumes/                 # Resume files for application tracking (PDFs/Markdown)
├── models/                      # sentence-transformers cache (gitignored)
├── scratch/                     # Exploratory notebooks (gitignored)
├── docker-compose.yml           # PostgreSQL + pgvector container
├── pyproject.toml
└── requirements.txt
```

Templates prefixed with `_` (e.g. `_table.html`, `_row.html`) are HTMX partials — they return fragments, not full pages. Full pages extend `base.html`.

---

## Database Schema

### `jobs`

| Column               | Type          | Notes                                                              |
| -------------------- | ------------- | ------------------------------------------------------------------ |
| job_id               | SERIAL PK     |                                                                    |
| title, url, company_name, location | TEXT |                                                   |
| description, qualifications, responsibilities | TEXT | Raw extracted text                         |
| employment_type      | TEXT          | 'full-time', 'part-time', 'contract', 'internship'                |
| attendance           | TEXT          | 'remote', 'hybrid', 'onsite'                                       |
| seniority            | TEXT          | 'junior', 'mid', 'senior', 'lead', 'staff', 'principal'           |
| experience_years_min/max | INTEGER   |                                                                    |
| salary_min/max/currency/period | varies |                                                           |
| date_listed          | DATE          |                                                                    |
| date_ingested, date_updated | TIMESTAMP |                                                              |
| status               | TEXT          | See status values below                                            |
| serp_api_json        | JSONB         | Full raw SerpAPI response — audit trail and reprocessing safety net |
| embedding            | vector(768)   | all-mpnet-base-v2 output; HNSW indexed                            |
| dedup_hash           | TEXT UNIQUE   | SHA-256 of normalised title+company+location                      |
| tier2_score          | REAL          | Cheap LLM fit score (0–100); populated at ingestion               |
| tier2_explanation    | TEXT          |                                                                    |
| tier3_score          | REAL          | Expensive LLM fit score; populated on demand                      |
| tier3_explanation    | TEXT          |                                                                    |
| application_id       | INTEGER FK    | Back-pointer to applications; NULL until applied                  |

**`jobs.status` values:**

| Value              | Set by              | Meaning                                                  |
| ------------------ | ------------------- | -------------------------------------------------------- |
| `active`           | pipeline            | Normal live listing                                      |
| `bad_fit`          | pipeline (auto)     | tier2_score < 50 at ingestion                            |
| `bad_listing`      | user (dashboard)    | Manually flagged as irrelevant listing                   |
| `applied`          | dashboard           | Application logged; pipeline leaves this alone           |
| `expired`          | pipeline / user     | Older than JOB_EXPIRY_DAYS, or manually set              |
| `closed`           | user (dashboard)    | Listing no longer accepting applications                 |
| `duplicate`        | pipeline (dedup)    | Detected as a duplicate                                  |
| `extraction_failed`| pipeline            | LLM extraction failed after retries                      |

`duplicate` and `extraction_failed` are hidden from the dashboard's default view but remain in the DB for reprocessing.

### `applications`

| Column           | Type    | Notes                                                             |
| ---------------- | ------- | ----------------------------------------------------------------- |
| application_id   | SERIAL PK |                                                                 |
| job_id           | INTEGER FK | Cascades on job delete                                         |
| date_applied     | DATE    |                                                                   |
| state            | TEXT    | 'submitted', 'interviewing', 'offer', 'rejected', 'withdrawn', 'expired' |
| assistance_level | TEXT    | 'ai', 'assisted', 'human'                                         |
| cover_letter     | TEXT    | Stored inline (per-application, disposable)                       |
| resume           | TEXT    | Filename only (e.g. `resume_ds_2026.pdf`); file lives in `data/resumes/` |
| cold_calls       | INTEGER |                                                                   |
| reached_human    | INTEGER | Boolean stored as 0/1                                             |
| interviews       | INTEGER | Number of interview rounds completed                              |
| offer            | INTEGER | Boolean stored as 0/1; kept in sync with `state = 'offer'`       |
| effort           | FLOAT   | Subjective effort score 0.0–10.0; NULL until filled in           |

On dashboard startup, `expire_stale_applications()` marks any `submitted` application older than 30 days as `expired`.

**Separation of concerns:** `jobs.status` tracks listing state (is the posting still live?). `applications.state` tracks application progress (how far along is your candidacy?). They are updated independently.

### Taxonomy tables

Skills and frameworks each have three tables:

- `skills` / `frameworks` — canonical entries with hierarchical metadata (`domain`, `core_competency`, `competency` for skills; `domain`, `subdomain`, `service` for frameworks). `is_candidate = 1` marks LLM-proposed entries pending review.
- `skill_aliases` / `framework_aliases` — lowercase variant → canonical ID mappings loaded into memory at startup for fast lookup.
- `job_skills` / `job_frameworks` — junction tables linking jobs to their taxonomy entries.

### Indexes

- HNSW index on `jobs.embedding` (cosine ops) for pgvector ANN search
- GIN trigram indexes on `jobs.company_name` and `jobs.title`
- B-tree indexes on `jobs.status`, `jobs.date_listed`, `jobs.dedup_hash`, `jobs.tier2_score`
- `applications.state`

---

## Ingestion Pipeline

### Entry points

| Script                   | When to use                                              |
| ------------------------ | -------------------------------------------------------- |
| `scripts/daily_run.py`   | Cron — expire old jobs, fetch today's listings, ingest   |
| `scripts/backfill.py`    | One-time historical ingestion across all queries         |
| `scripts/single_query.py`| Ad-hoc ingestion for one search query                    |
| `scripts/reprocess.py`   | Re-run extraction on stored `serp_api_json` (no re-fetch)|

### Pipeline steps (`pipeline/orchestrator.py`)

```
1. Dedup     — SHA-256 hash exact match, then thefuzz fuzzy match (default threshold: 85)
2. Extract   — LLM extracts structured fields from raw SerpAPI JSON; retry once on failure
3. Normalize — alias table lookup → exact name match → insert as is_candidate=1
4. Embed     — all-mpnet-base-v2; composition: title | qualifications | responsibilities | skills | frameworks
5. Score     — cheap LLM scores 0–100 against career_profile.md; jobs < 50 → status='bad_fit'
6. Store     — INSERT into jobs + job_skills + job_frameworks
7. Auto tier3 — jobs with tier2_score >= TIER3_AUTO_SCORE_MIN (default 70) immediately queued
                for expensive LLM deep analysis
```

The full raw SerpAPI response is stored in `serp_api_json` on every job, making re-extraction possible without re-fetching from the API.

---

## Scoring (Tier System)

| Tier   | Model        | When                           | Output                              |
| ------ | ------------ | ------------------------------ | ----------------------------------- |
| Tier 1 | pgvector     | Ad-hoc (`match_career_profile.py`) | Cosine similarity, no LLM call |
| Tier 2 | Cheap LLM    | At ingestion (always) + ad-hoc re-score | Score 0–100 + explanation    |
| Tier 3 | Expensive LLM| Auto for high scorers; on-demand via `score_top_jobs.py` | Fit score, strengths, gaps, resume tips |

**Tier 3 paths:**
- **Primary path:** `scripts/score_top_jobs.py` — query top-K jobs by `tier2_score DESC`, run expensive LLM, persist `tier3_score` / `tier3_explanation`.
- **Ad-hoc path:** `scripts/match_career_profile.py` — embed career profile, vector search top 100, optionally re-score (tier 2), optionally deep-analyse (tier 3).
- **Auto path:** The orchestrator queues any newly ingested job with `tier2_score >= 70` for immediate tier 3 analysis within the same batch run.

---

## Dashboard

**Stack:** FastAPI + Jinja2 (server-rendered HTML) + HTMX (partial updates) + Alpine.js (client-side UI state) + Tailwind CSS CDN. Single `uvicorn` process, no build step.

**Layered architecture:**

```
Routes (app/routes/)          — parse input, call service, return HTML
    ↓
Services (app/services/)      — compose db/operations calls + file I/O
    ↓
db/operations.py              — all SQL (shared with pipeline)
```

The dashboard must be deletable without touching `db/operations.py`. If `app/` is deleted, the pipeline still works.

### Pages

| Route                        | Purpose                                                      |
| ---------------------------- | ------------------------------------------------------------ |
| `GET /jobs`                  | Filterable, sortable, paginated job table (50/page)          |
| `GET /jobs/{id}/detail`      | Side panel with full job detail, scores, skills, status edit |
| `PATCH /jobs/{id}/status`    | Update job status; returns updated row fragment              |
| `GET /applications`          | Filterable application tracker                               |
| `GET /applications/{id}/detail` | Editable application detail panel                         |
| `POST /applications`         | Log new application (atomic: insert + update job back-pointer)|
| `GET /config/queries`        | Edit `queries.yaml` via table form                          |
| `GET /config/profile`        | Edit `career_profile.md` via textarea                        |

HTMX filter changes swap only the table fragment (`hx-target`, `hx-push-url="true"`). Row clicks load the detail panel without a full page reload. All writes use `PATCH`/`POST` with form data; no JSON API.

### Freshness header

The nav bar shows pipeline freshness (last ingestion time, active job count, applied count) via four lightweight queries cached in-memory for 60 seconds per process. No polling, no websockets.

---

## Candidate Taxonomy Review

`scripts/review_candidates.py` handles curation of LLM-proposed skills/frameworks (`is_candidate = 1`).

**Per-candidate actions:** promote (assign to taxonomy hierarchy), merge (remap to an existing canonical entry + add alias), discard, skip, quit.

**Similarity detection:** Before the review loop, all canonical entries are embedded with the small model (`all-MiniLM-L6-v2`). For each candidate, the top-K most similar canonical entries are shown to guide the decision.

---

## Environment Variables

| Variable               | Default                       | Purpose                                       |
| ---------------------- | ----------------------------- | --------------------------------------------- |
| `DATABASE_URL`         | *(required)*                  | PostgreSQL connection string                  |
| `SERPAPI_KEY`          | *(required)*                  | SerpAPI API key                               |
| `OPENROUTER_API_KEY`   | *(required)*                  | OpenRouter API key                            |
| `EXTRACTION_MODEL`     | `google/gemini-flash-1.5`     | LLM for metadata extraction                   |
| `SCORING_MODEL`        | `google/gemini-flash-1.5`     | LLM for ingestion-time fit scoring            |
| `DEEP_ANALYSIS_MODEL`  | `anthropic/claude-sonnet-4-5` | LLM for tier 3 deep analysis                  |
| `EMBEDDING_MODEL_LARGE`| `all-mpnet-base-v2`           | Large embedding model (jobs + career profile) |
| `EMBEDDING_MODEL_SMALL`| `all-MiniLM-L6-v2`            | Small embedding model (skill name similarity) |
| `EMBEDDING_DIM`        | `768`                         | Vector dimension (must match large model)     |
| `EMBEDDING_MAX_TOKENS` | `384`                         | Max tokens for embedding truncation           |
| `TIER1_CANDIDATES`     | `100`                         | pgvector search result limit                  |
| `TIER2_TOP_N`          | `15`                          | Top-N passed to tier 3 in ad-hoc flow         |
| `TIER2_CONCURRENCY`    | `10`                          | Async concurrency for ad-hoc cheap LLM calls  |
| `DEEP_ANALYSIS_TOP_K`  | `15`                          | Default K for `score_top_jobs.py`             |
| `TIER3_AUTO_SCORE_MIN` | `70`                          | tier2_score threshold for auto tier3 at ingest|
| `DAILY_MAX_PAGES`      | `1`                           | SerpAPI pages per query in daily mode         |
| `BACKFILL_MAX_PAGES`   | `10`                          | SerpAPI pages per query in backfill mode      |
| `JOB_EXPIRY_DAYS`      | `30`                          | Days before active listings are marked expired|
| `DEDUP_FUZZY_THRESHOLD`| `85`                          | thefuzz ratio threshold for fuzzy dedup       |
| `ANTHROPIC_API_KEY`    | *(optional)*                  | Direct Anthropic key (bypasses OpenRouter)    |

---

## Setup

1. Copy `.env.example` → `.env`, fill in `DATABASE_URL`, `SERPAPI_KEY`, `OPENROUTER_API_KEY`
2. Start the database: `docker-compose up -d`
3. `python -m db.seed.seed` — creates schema and seeds taxonomy
4. Copy `config/queries.example.yaml` → `config/queries.yaml`, add your searches
5. Fill in `data/career_profile.md` with your resume (required for scoring)
6. `python scripts/backfill.py` — historical ingestion (downloads models on first run)
7. `python scripts/daily_run.py` — daily cron target
8. `pip install -e .[dashboard]` — installs FastAPI, uvicorn, jinja2, python-multipart
9. `mkdir -p data/resumes && cp <your resume files> data/resumes/`
10. `uvicorn app.main:app --reload` — dashboard at `http://127.0.0.1:8000`

---

## Design Invariants

- **`db/operations.py` is the only place SQL lives.** Pipeline modules and dashboard services both call into it; neither writes SQL directly.
- **The dashboard does not import from `pipeline/` or `matching/`.** Deleting `app/` leaves the pipeline fully intact.
- **`serp_api_json` is always stored.** Re-extraction is possible without re-fetching from SerpAPI.
- **`jobs.status` and `applications.state` are orthogonal.** `jobs.status` tracks whether the listing is still live; `applications.state` tracks your candidacy progress. They update independently.
- **Pipeline scripts are CLI-only.** The dashboard displays results and freshness indicators; it never triggers ingestion, scoring, or backfill.
