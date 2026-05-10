# Data Science Career Search

A self-hosted job search system that ingests listings from SerpAPI, scores them against your career profile using LLMs, and surfaces results through a local dashboard.

## What it does

1. **Ingest** — fetches Google Jobs results via SerpAPI, deduplicates, and extracts structured metadata with a cheap LLM.
2. **Score** — every job is automatically scored 0–100 for career fit at ingestion time. High scorers get deep analysis from an expensive LLM.
3. **Browse** — a FastAPI dashboard lets you filter jobs, track applications, and edit config.

## Architecture

```
SerpAPI → Dedup → Extract → Normalize → Embed → Score → Store
                                                      ↓
                                               PostgreSQL + pgvector
                                                      ↓
                          FastAPI Dashboard (jobs / applications / config)
```

Both the pipeline and dashboard share a single database and a focused SQL layer (`db/jobs.py`, `db/taxonomy.py`, `db/applications.py`). The dashboard never triggers ingestion; the pipeline never imports from `app/`.

## Scoring tiers

| Tier | Model | When | Output |
|------|-------|------|--------|
| Tier 1 | pgvector | Ad-hoc | Cosine similarity against career profile embedding |
| Tier 2 | Cheap LLM (Gemini Flash) | Every ingestion | `t2_score` 0–100 + explanation |
| Tier 3 | Expensive LLM (Claude Sonnet) | Auto for `t2_score >= 70`; on-demand | `t3_qualification`, `t3_fit`, combined score |

**Tier 3 formula:** `t3_score = ((1 - β) + β × (t3_fit / 100)) × (t3_qualification / 100) × 100`  
Default `β = 0.2` — qualification dominates; fit only discounts.

## Project layout

```
pipeline/        # fetch → dedup → extract → normalize → embed → score → store
matching/        # tier1 (vector), tier2 (cheap LLM), tier3 (expensive LLM)
app/             # FastAPI dashboard (routes / services / templates)
db/              # schema, connection pool, SQL split by domain (jobs/taxonomy/applications)
scripts/         # CLI entry points (daily_run, backfill, score_top_jobs, …)
config/          # settings.py + queries.yaml (search definitions)
data/            # career_profile.md, skills/frameworks taxonomy, resumes/
```

## Quick start

```bash
# 1. Configure
cp .env.example .env          # fill in DATABASE_URL, SERPAPI_KEY, OPENROUTER_API_KEY
cp config/queries.example.yaml config/queries.yaml   # add your search queries
# edit data/career_profile.md with your resume

# 2. Start the database
docker-compose up -d

# 3. Bootstrap schema + taxonomy
python -m db.seed.seed

# 4. Ingest jobs
python scripts/backfill.py    # historical (10 pages/query)
python scripts/daily_run.py   # daily cron target (1 page/query)

# 5. Run the dashboard
pip install -e .[dashboard]
uvicorn app.main:app --reload  # → http://127.0.0.1:8000
```

## Key workflows

| Task | Command |
|------|---------|
| Daily ingestion (cron) | `python scripts/daily_run.py` |
| Historical backfill | `python scripts/backfill.py` |
| Ad-hoc single query | `python scripts/single_query.py` |
| Re-extract from stored JSON | `python scripts/reprocess.py` |
| Deep-score top jobs | `python scripts/score_top_jobs.py` |
| Ad-hoc 3-tier match | `python scripts/match_career_profile.py` |
| Curate taxonomy candidates | `python scripts/review_candidates.py` |

## Stack

- **Database:** PostgreSQL 17 + pgvector (Docker)
- **Embeddings:** `all-mpnet-base-v2` (768-dim, jobs/profile) · `all-MiniLM-L6-v2` (384-dim, skill similarity)
- **LLMs:** OpenRouter — cheap model for extraction/scoring, expensive model for deep analysis
- **Dashboard:** FastAPI + Jinja2 + HTMX + Alpine.js + Tailwind CSS (no build step)

## Environment variables

See `.env.example` for the full list. Required: `DATABASE_URL`, `SERPAPI_KEY`, `OPENROUTER_API_KEY`.  
See `ARCHITECTURE.md` for the complete variable reference and schema documentation.
