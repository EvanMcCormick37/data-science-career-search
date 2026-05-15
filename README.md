# Data Science Career Search

A self-hosted job search system that ingests listings from SerpAPI, scores them against your career profile using LLMs, and surfaces results through a local dashboard.

## What it does

1. Ingest — The ingest function uses a series of search queries to extract listings from the Google Jobs API. Two ingestion scripts cover different sets of data: backfill.py scrapes all job results from the past two weeks, for a maximum of 10 pages per search query, while daily_run.py scrapes only results he past 24 hours. These job results get returned in the form of JSON metadata objects.
2. Extract — An LLM-powered extractor extracts key metadata about the job, such as salary, seniority level, and attendance policy (onsite, hybrid, remote), as well as any skills and frameworks that are listed as job requirements.
3. Score — Each job is automatically scored by an inexpensive LLM for ‘career fit’ with the career profile stored in data/career_profile.md, on a scale of 0-100. Jobs with a career fit above some threshold (default 70) are rescored by an expensive model using a two-fold prompt. Both models return arguments supporting their ‘fit scores’ which are stored alongside the scores in the jobs database.
4. Browse — a FastAPI dashboard lets you filter jobs, track applications, monitor the relevance of particular skills and frameworks, and edit your configuration (career profile, search queries).

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
