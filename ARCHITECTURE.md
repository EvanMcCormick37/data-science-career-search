# Job Search Pipeline — Architecture Specification

## Overview

A Python-based pipeline that ingests job listings from SerpAPI (Google Jobs), extracts structured metadata via a cheap LLM, stores everything in PostgreSQL (with pgvector), and supports two use cases:

1. **Personal job filtering** — match listings against a resume using a three-tier relevance scoring funnel.
2. **Public dataviz** (future) — surface job market trends (in-demand skills, frameworks, and other metadata) on a web frontend. Do not build this.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        INGESTION PIPELINE                           │
│                     (Python, scheduled daily)                       │
│                                                                     │
│  ┌──────────┐    ┌──────────┐    ┌───────────┐    ┌─────────────┐   │
│  │ SerpAPI  │── ▶│  Dedup   │──▶│ LLM       │──▶│ Embed +     │    |
│  │ Fetcher  │    │ (Fuzzy)  │    │ Extractor │    │ Store (PG)  │   │
│  └──────────┘    └──────────┘    └───────────┘    └─────────────┘   │
│       │                                                             │
│       ▼                                                             │
│  Backfill mode: paginate historical listings (100-300 queries)      │
│  Steady-state: ~30-50 queries/day for new listings only             │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                       QUERY PIPELINE                                │
│                                                                     │
│  ┌──────────┐   ┌──────────────┐   ┌───────────┐   ┌───────────┐    │
│  │ Embed    │─▶│ pgvector     │──▶│ Cheap LLM │─▶│ Claude    │    │
│  │ Resume   │   │ Top 50-100   │   │ Score all │   │ Deep      │    │
│  │          │   │ (cosine sim) │   │ candidates│   │ Analysis  │    │
│  └──────────┘   └──────────────┘   └───────────┘   └───────────┘    │
│                                                                     │
│  Output: ranked shortlist with fit explanations                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Technology Stack

| Component         | Choice                       | Rationale                                           |
| ----------------- | ---------------------------- | --------------------------------------------------- | -------------- |
| Language          | Python 3.11+                 | Ecosystem for ML/NLP, rapid prototyping             |
| Database          | PostgreSQL 16 + pgvector     | Single DB for relational data AND vector search     |
| Embedding model   | all-mpnet-base-v2 (local)    | 768-dim, 384-token window.                          |
| Extraction LLM    | Cheap Model via OpenRouter   | Structured extraction at ~$0.001/job or less        |
| Scoring LLM       | Same model (e.g. Kimi K2)    | Tier 2 relevance scoring                            |
| Deep analysis LLM | Claude Code Subagent         | Tier 3 detailed resume fit analysis, top 10-15 only |
| Task scheduling   | cron (local) or APScheduler  | Daily trigger, no need for Celery at this scale     |
| Future frontend   | JS (React or Svelte) + D3.js | Dataviz flexibility                                 | (DO NOT BUILD) |

---

## Database Schema

```sql
-- Extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- for fuzzy text matching

-- Core jobs table
CREATE TABLE jobs (
    job_id          SERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    url             TEXT NOT NULL,
    company_name    TEXT NOT NULL,
    location        TEXT,
    description     TEXT,
    employment_type TEXT,              -- 'full-time', 'part-time', 'contract', 'internship'
    attendance      TEXT,              -- 'remote', 'hybrid', 'onsite'
    seniority       TEXT,              -- 'junior', 'mid', 'senior', 'lead', 'staff', 'principal'
    experience_years_min INTEGER,
    experience_years_max INTEGER,
    salary_min      INTEGER,
    salary_max      INTEGER,
    salary_currency TEXT DEFAULT 'USD',
    salary_period   TEXT,              -- 'yearly', 'hourly', 'monthly'
    qualifications  TEXT,              -- raw extracted text from SerpAPI
    responsibilities TEXT,             -- raw extracted text from SerpAPI
    date_listed     DATE,
    date_ingested   TIMESTAMP DEFAULT NOW(),
    date_updated    TIMESTAMP DEFAULT NOW(),
    status          TEXT DEFAULT 'active',  -- 'active', 'expired', 'duplicate'
    serp_api_json   JSONB,             -- full raw response for auditability
    embedding       vector(768),       -- all-mpnet-base-v2 output
    dedup_hash      TEXT UNIQUE,       -- fuzzy-normalized hash for dedup

    -- Relevance scoring (populated lazily, on query)
    tier2_score     REAL,
    tier2_explanation TEXT,
    tier1_score     REAL,
    tier1_explanation TEXT
);

-- Normalized skill/framework tables for efficient aggregation (Goal 2)
CREATE TABLE skills (
    skill_id   SERIAL PRIMARY KEY,
    domain     TEXT NOT NULL,
    core_competency TEXT,
    competency TEXT,
    name       TEXT UNIQUE NOT NULL,    -- normalized: lowercase, canonical form
    is_candidate INTEGER,    -- Boolean indicator. 1 means it's a 'proposed' skill but not yet accepted into the skill tree (I allow the LLMs to add new skills which they think I missed in the original skill tree, but we only fully add those skills once they have enough support. And we may also 'fold' them into existing skills.)
);

CREATE TABLE job_skills (
    job_id   INTEGER REFERENCES jobs(job_id) ON DELETE CASCADE,
    skill_id INTEGER REFERENCES skills(skill_id) ON DELETE CASCADE,
    PRIMARY KEY (job_id, skill_id)
);

CREATE TABLE frameworks (
    framework_id SERIAL PRIMARY KEY,
    domain TEXT NOT NULL,
    subdomain TEXT,
    service TEXT,
    name         TEXT UNIQUE NOT NULL
    is_candidate INTEGER,
);

CREATE TABLE job_frameworks (
    job_id       INTEGER REFERENCES jobs(job_id) ON DELETE CASCADE,
    framework_id INTEGER REFERENCES frameworks(framework_id) ON DELETE CASCADE,
    PRIMARY KEY (job_id, framework_id)
);

-- Indexes
CREATE INDEX idx_jobs_embedding ON jobs USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_jobs_status ON jobs (status);
CREATE INDEX idx_jobs_date_listed ON jobs (date_listed);
CREATE INDEX idx_jobs_dedup_hash ON jobs (dedup_hash);
CREATE INDEX idx_jobs_company_trgm ON jobs USING gin (company_name gin_trgm_ops);
CREATE INDEX idx_jobs_title_trgm ON jobs USING gin (title gin_trgm_ops);
```

### Notes on schema decisions

**Why JSONB for serp_api_json:** You keep the full raw response so you can re-extract if your LLM prompt improves, without re-hitting the API. This is your audit trail and reprocessing safety net.

**Why a separate skill_aliases table:** The LLM will extract "React.js" from one listing and "ReactJS" from another. Rather than hoping the LLM normalizes perfectly (it won't), you maintain a canonical mapping. When inserting a skill, you check aliases first. This is essential for accurate dataviz aggregation.

**Why tier2/tier3 scores live on the jobs table:** These are specific to YOUR resume and are populated lazily (only when you run a query). If you later build the public version, user-specific scores would go in a separate `user_job_scores` table. For now, keep it simple.

---

## Ingestion Pipeline Detail

### Step 1: SerpAPI Fetch

```
serpapi_fetcher.py
├── Accepts: search queries (role + location combinations)
├── Handles: pagination, rate limiting, backfill vs. daily mode
├── Outputs: list of raw job dicts
└── Writes: raw JSON to staging (or directly to processing queue)
```

**Backfill mode:** Iterate through your target query set (role titles × locations), paginating each. Track which queries have been completed so you can resume if interrupted.

**Daily mode:** Same query set, but filter by `date_posted:today` or use SerpAPI's `chips` parameter to restrict to recent listings. Only fetch first 1-2 pages per query.

**Rate limiting:** SerpAPI's 1,000 searches/month = ~33/day. Your daily steady-state budget should stay under 30 queries to leave headroom. Design your query set accordingly — prioritize breadth of role titles over locations, since Google Jobs already has geographic reach.

### Step 2: Fuzzy Dedup

```
dedup.py
├── Input: raw job dict
├── Normalize: lowercase, strip punctuation, expand abbreviations
│   ("Sr." → "senior", "Eng." → "engineer", etc.)
├── Generate dedup_hash: hash(normalized_title + normalized_company + normalized_location)
├── Check: does this hash exist in the DB?
│   ├── If exact hash match → skip, mark as duplicate
│   └── If no exact match → secondary fuzzy check:
│       Query jobs with same normalized company_name,
│       run thefuzz.token_sort_ratio on title, threshold ≥ 85
│       If match found → skip, mark as duplicate
│       Else → pass through to extraction
└── Output: deduplicated job dicts
```

**Performance note:** The fuzzy check is only triggered against same-company listings, avoiding O(n²) across the full corpus. The `pg_trgm` index on `company_name` makes this lookup fast.

### Step 3: LLM Metadata Extraction

```
extractor.py
├── Input: raw job description + qualifications + responsibilities
├── LLM call: Gemini Flash / Kimi K2 with structured output prompt, skills.md and frameworks.md within the system prompt.
├── Extracts:
│   ├── employment_type (enum)
│   ├── attendance (enum)
│   ├── seniority (enum)
│   ├── experience_years_min / max (integers)
│   ├── salary_min / max / currency / period (if present)
│   ├── skills (list of strings)
│   └── frameworks (list of strings)
├── Normalize: run skills/frameworks through alias tables
└── Output: structured job record ready for insertion
```

**Prompt design guidance:** Force JSON output with a strict schema. Include 2-3 few-shot examples in the prompt. Explicitly tell the model to return `null` for fields not present (not to guess). Keep the system prompt short — this is a classification/extraction task, not a creative one.

**Error handling:** If the LLM returns malformed JSON, retry once. If it fails again, store the job with `status = 'extraction_failed'` and move on. You can reprocess these in batch later.

### Step 4: Embed + Store

```
embedder.py
├── Compose embedding string:
│   "{title} | {qualifications} | {responsibilities} | {skills} | {frameworks}"
├── Truncation strategy (if > 384 tokens):
│   Priority order: title > qualifications > responsibilities > skills > frameworks
│   Truncate from the end of the lowest-priority field first
├── Generate: sentence-transformers all-mpnet-base-v2
├── Insert: full job record + embedding into PostgreSQL
└── Insert: normalized skills/frameworks into junction tables
```

---

## Query Pipeline Detail (Resume Matching)

### Tier 1 — Vector Similarity (free, instant)

```sql
SELECT job_id, title, company_name, location,
       1 - (embedding <=> :resume_embedding) AS cosine_similarity
FROM jobs
WHERE status = 'active'
ORDER BY embedding <=> :resume_embedding
LIMIT 100;
```

The resume is embedded using the same model and the same string template — adapted for a resume context: `"{target_role} | {qualifications_summary} | {experience_summary} | {skills} | {frameworks}"`.

### Tier 2 — Cheap LLM Scoring

For each of the top 100 candidates, send to Gemini Flash / Kimi K2:

```
System: You are a job matching evaluator. Given a resume and a job listing,
        score the match from 0-100 and provide a one-sentence explanation.
        Respond in JSON: {"score": int, "explanation": string}

User: RESUME: {resume_text}
      JOB: {title} at {company_name}
      Description: {description}
      Qualifications: {qualifications}
```

**Batching:** Run these concurrently (asyncio + httpx) with appropriate rate limiting. 100 calls to Gemini Flash takes ~15-30 seconds and costs a fraction of a cent.

Store `tier2_score` and `tier2_explanation` on the jobs table.

### Tier 3 — Claude Deep Analysis

Take the top 10-15 by tier2_score. Send to Claude with a detailed prompt that includes your full resume and asks for:

- Fit score (0-100)
- Strengths: where you match well
- Gaps: where you fall short
- Recommendation: apply / apply with caveats / skip
- Suggested resume adjustments for this specific role

Store `tier3_score` and `tier3_explanation`.

---

## Project Structure

```
job-pipeline/
├── config/
│   ├── settings.py          # API keys, DB connection, model config
│   └── queries.yaml         # Search query definitions (roles × locations)
├── pipeline/
│   ├── fetcher.py           # SerpAPI ingestion
│   ├── dedup.py             # Fuzzy deduplication
│   ├── extractor.py         # LLM metadata extraction
│   ├── embedder.py          # Embedding generation
│   ├── normalizer.py        # Skill/framework alias resolution
│   └── orchestrator.py      # Ties pipeline steps together
├── matching/
│   ├── tier1_vector.py      # pgvector similarity search
│   ├── tier2_cheap_llm.py   # Batch cheap LLM scoring
│   └── tier3_deep_analysis.py  # Claude detailed analysis
├── db/
│   ├── schema.sql           # DDL (the schema above)
│   ├── migrations/          # Schema evolution
│   └── connection.py        # DB connection pool
├── scripts/
│   ├── backfill.py          # One-time historical ingestion
│   ├── daily_run.py         # Daily pipeline entry point
│   └── match_resume.py      # Run the 3-tier matching flow
├── data/
│   ├── resume.md            # Your resume in structured format
│   ├── skills.md            # The skill tree in a semi-structured format
│   └── frameworks.md        # The frameworks tree in a semi-structured format
├── tests/
├── requirements.txt
└── README.md
```

---

## Cost Estimates (Monthly, Steady-State)

| Item                          | Volume         | Est. Cost       |
| ----------------------------- | -------------- | --------------- |
| SerpAPI                       | ~900 searches  | $25 (plan)      |
| Gemini Flash — extraction     | ~9,000 jobs    | ~$0.50-1.00     |
| Gemini Flash — tier 2 scoring | ~100/query run | ~$0.01/run      |
| Claude — tier 3 analysis      | ~15/query run  | ~$0.50-1.00/run |
| all-mpnet-base-v2             | local          | $0              |
| PostgreSQL                    | local          | $0              |
| **Total**                     |                | **~$27/mo**     |

---

## Open Decisions / Future Work

1. **Alias seed data:** You'll need to curate an initial `skill_aliases.csv` mapping common variants. Start with 50-100 entries covering your target field. Expand as you see fragmentation in the data.

2. **Job expiry:** Listings go stale. Consider marking jobs as `expired` after 30 days unless refreshed by a subsequent SerpAPI fetch. A simple `UPDATE jobs SET status = 'expired' WHERE date_listed < NOW() - INTERVAL '30 days' AND status = 'active'` in the daily cron.

3. **Reprocessing pipeline:** When you improve your extraction prompt (and you will), you'll want to re-run extraction against stored `serp_api_json` without re-fetching. Design the orchestrator to support a `reprocess` mode that reads from the DB instead of SerpAPI.

4. **Public website (Goal 2):**
   - API layer (FastAPI) serving aggregated skill/framework counts, trend-over-time, salary distributions
   - Visualization queries are trivial with the normalized schema:
     ```sql
     SELECT s.name, COUNT(*) as demand
     FROM job_skills js
     JOIN skills s ON s.skill_id = js.skill_id
     JOIN jobs j ON j.job_id = js.job_id
     WHERE j.status = 'active' AND j.date_listed > NOW() - INTERVAL '30 days'
     GROUP BY s.name
     ORDER BY demand DESC
     LIMIT 25;
     ```
   - User resume matching would expose Tiers 1-2 only (free tier) with Tier 3 behind auth/payment.

5. **Embedding model upgrade path:** If retrieval quality disappoints, you can swap to a larger model (e.g., `bge-large-en-v1.5`, 1024-dim) by re-embedding the corpus from stored text. The schema supports this — just alter the vector dimension and rebuild the HNSW index.
