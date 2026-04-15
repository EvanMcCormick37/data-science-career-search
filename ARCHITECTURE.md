# Job Search Pipeline — Architecture Specification

## Overview

A Python-based pipeline that ingests job listings from SerpAPI (Google Jobs), extracts structured metadata via a cheap LLM, stores everything in PostgreSQL (with pgvector), and supports two use cases:

1. **Personal job filtering** — match listings against a resume using a three-tier relevance scoring funnel.
2. **Public dataviz** (future, do not build) — surface job market trends (in-demand skills, frameworks, and other metadata) on a web frontend.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        INGESTION PIPELINE                           │
│                     (Python, scheduled daily)                       │
│                                                                     │
│  ┌──────────┐    ┌──────────┐    ┌───────────┐    ┌─────────────┐   │
│  │ SerpAPI  │───▶│  Dedup   │──▶│ LLM       │──▶│ Embed +     │   │
│  │ Fetcher  │    │ (Fuzzy)  │    │ Extractor │    │ Store (PG)  │   │
│  └──────────┘    └──────────┘    └───────────┘    └─────────────┘   │
│       │                                │                            │
│       ▼                                ▼                            │
│  Backfill mode: paginate         skills.md + frameworks.md          │
│  historical listings             in system prompt guide             │
│  (100-300 queries)               extraction against canonical       │
│                                  taxonomy                           │
│  Steady-state: ~30-50                                               │
│  queries/day for new                                                │
│  listings only                                                      │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                     NORMALIZATION LAYER                              │
│                                                                     │
│  LLM returns skill/framework name                                   │
│       │                                                             │
│       ▼                                                             │
│  Check alias table → canonical match found? → insert canonical ID   │
│       │ no match                                                    │
│       ▼                                                             │
│  Check canonical names directly → exact match? → insert skill ID    │
│       │ no match                                                    │
│       ▼                                                             │
│  Insert as candidate (is_candidate = 1)                             │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                       QUERY PIPELINE                                │
│                                                                     │
│  ┌──────────┐   ┌──────────────┐   ┌───────────┐   ┌───────────┐  │
│  │ Embed    │──▶│ pgvector     │──▶│ Cheap LLM │──▶│ Claude    │  │
│  │ Resume   │   │ Top 50-100   │   │ Score all  │   │ Deep      │  │
│  │          │   │ (cosine sim) │   │ candidates │   │ Analysis  │  │
│  └──────────┘   └──────────────┘   └───────────┘   └───────────┘  │
│                                                                     │
│  Tier 1: Vector       Tier 2: Cheap LLM     Tier 3: Claude         │
│  similarity search    relevance scoring      detailed fit analysis  │
│  (free, instant)      (top 100 → scored)     (top 10-15 only)      │
│                                                                     │
│  Output: ranked shortlist with fit explanations                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Technology Stack

| Component         | Choice                       | Rationale                                                          |
| ----------------- | ---------------------------- | ------------------------------------------------------------------ |
| Language          | Python 3.11+                 | Ecosystem for ML/NLP, rapid prototyping                            |
| Database          | PostgreSQL 16 + pgvector     | Single DB for relational data AND vector search                    |
| Embedding model   | all-mpnet-base-v2 (local)    | 768-dim, 384-token window                                          |
| Extraction LLM    | Cheap model via OpenRouter   | Structured extraction at ~$0.001/job. Modular — swap models freely |
| Scoring LLM       | Same cheap model             | Tier 2 relevance scoring                                           |
| Deep analysis LLM | Claude Code subagent         | Tier 3 detailed resume fit analysis, top 10-15 only                |
| Task scheduling   | cron (local) or APScheduler  | Daily trigger, no need for Celery at this scale                    |
| Future frontend   | JS (React or Svelte) + D3.js | Dataviz flexibility (DO NOT BUILD YET)                             |

### Why PostgreSQL + pgvector (not a separate VectorDB)

At ≤10K jobs, pgvector with HNSW indexing handles similarity search in <100ms. A dedicated vector store (Pinecone, Qdrant, etc.) would add: a second data store to keep in sync, a second backup strategy, cross-system joins for filtering, and additional cost. One database, one connection, one backup.

### Why OpenRouter

OpenRouter provides a unified API across dozens of model providers. This gives you:

- A single integration point — swap models by changing a config string, not rewriting API calls.
- Access to the cheapest available models (Gemini Flash, Kimi K2, DeepSeek, etc.) without separate API keys for each.
- A natural A/B testing setup: run the same extraction prompt against two models, compare output quality.

Build the LLM client as a thin wrapper that accepts a model identifier and routes through OpenRouter. Keep per-model API keys in config for fallback direct access if needed.

---

## Database Schema

```sql
-- Extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- for fuzzy text matching


-- ============================================================
-- CORE JOBS TABLE
-- ============================================================

CREATE TABLE jobs (
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
    responsibilities      TEXT,                -- raw extracted text from SerpAPI
    date_listed           DATE,
    date_ingested         TIMESTAMP DEFAULT NOW(),
    date_updated          TIMESTAMP DEFAULT NOW(),
    status                TEXT DEFAULT 'active',  -- 'active', 'expired', 'duplicate', 'extraction_failed'
    serp_api_json         JSONB,               -- full raw response for auditability and reprocessing
    embedding             vector(768),         -- all-mpnet-base-v2 output
    dedup_hash            TEXT UNIQUE,         -- normalized hash for dedup

    -- Relevance scoring (populated lazily, per query run)
    tier2_score           REAL,
    tier2_explanation     TEXT,
    tier3_score           REAL,
    tier3_explanation     TEXT
);


-- ============================================================
-- SKILLS TAXONOMY
-- ============================================================

CREATE TABLE skills (
    skill_id        SERIAL PRIMARY KEY,
    domain          TEXT NOT NULL,          -- top-level category (e.g. 'Backend', 'Data Engineering')
    core_competency TEXT,                   -- grouping within domain (e.g. 'Databases', 'APIs')
    competency      TEXT,                   -- sub-grouping (e.g. 'Relational Databases')
    name            TEXT UNIQUE NOT NULL,   -- canonical name, normalized lowercase
    is_candidate    INTEGER DEFAULT 0       -- 1 = proposed by LLM, not yet accepted into taxonomy
);

CREATE TABLE skill_aliases (
    alias    TEXT PRIMARY KEY,              -- variant form (e.g. 'postgres', 'postgresql')
    skill_id INTEGER NOT NULL REFERENCES skills(skill_id) ON DELETE CASCADE
);

CREATE TABLE job_skills (
    job_id   INTEGER REFERENCES jobs(job_id) ON DELETE CASCADE,
    skill_id INTEGER REFERENCES skills(skill_id) ON DELETE CASCADE,
    PRIMARY KEY (job_id, skill_id)
);


-- ============================================================
-- FRAMEWORKS TAXONOMY
-- ============================================================

CREATE TABLE frameworks (
    framework_id    SERIAL PRIMARY KEY,
    domain          TEXT NOT NULL,          -- top-level category (e.g. 'Cloud', 'Frontend')
    subdomain       TEXT,                   -- grouping (e.g. 'AWS', 'React Ecosystem')
    service         TEXT,                   -- specific service area (e.g. 'Compute', 'State Management')
    name            TEXT UNIQUE NOT NULL,   -- canonical name
    is_candidate    INTEGER DEFAULT 0       -- 1 = proposed by LLM, not yet accepted
);

CREATE TABLE framework_aliases (
    alias        TEXT PRIMARY KEY,          -- variant form (e.g. 'k8s', 'kube')
    framework_id INTEGER NOT NULL REFERENCES frameworks(framework_id) ON DELETE CASCADE
);

CREATE TABLE job_frameworks (
    job_id       INTEGER REFERENCES jobs(job_id) ON DELETE CASCADE,
    framework_id INTEGER REFERENCES frameworks(framework_id) ON DELETE CASCADE,
    PRIMARY KEY (job_id, framework_id)
);


-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX idx_jobs_embedding ON jobs USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_jobs_status ON jobs (status);
CREATE INDEX idx_jobs_date_listed ON jobs (date_listed);
CREATE INDEX idx_jobs_dedup_hash ON jobs (dedup_hash);
CREATE INDEX idx_jobs_company_trgm ON jobs USING gin (company_name gin_trgm_ops);
CREATE INDEX idx_jobs_title_trgm ON jobs USING gin (title gin_trgm_ops);
CREATE INDEX idx_skill_aliases_skill ON skill_aliases (skill_id);
CREATE INDEX idx_framework_aliases_framework ON framework_aliases (framework_id);
```

### Schema Design Notes

**JSONB for serp_api_json:** The full raw SerpAPI response is preserved so you can re-extract metadata when your extraction prompt improves, without re-hitting the API. This is your audit trail and reprocessing safety net.

**Tier scores on the jobs table:** These are specific to YOUR resume and are populated lazily (only during a query run). Tier 1 (vector cosine similarity) is computed at query time and not stored. If you later build the public-facing version, user-specific scores would move to a separate `user_job_scores` table.

**is_candidate flag:** Skills and frameworks extracted by the LLM that don't match any canonical name or known alias are inserted with `is_candidate = 1`. They participate in job-skill linkage immediately (so no data is lost), but are excluded from dataviz aggregation until manually reviewed and either promoted (set `is_candidate = 0`) or merged into an existing canonical entry.

**Alias tables vs. in-memory normalization:** Alias tables live in Postgres for durability and easy manual curation (adding a new alias is a single INSERT). At startup, the normalizer loads the full alias mapping into a Python dict for fast in-memory lookup during ingestion. The DB is the source of truth; the dict is a cache.

---

## Ingestion Pipeline Detail

### Step 1: SerpAPI Fetch

```
pipeline/fetcher.py
├── Accepts: search queries (role + location combinations) from config/queries.yaml
├── Handles: pagination, rate limiting, backfill vs. daily mode
├── Outputs: list of raw job dicts
└── Writes: raw JSON to staging (or directly to processing queue)
```

**Backfill mode:** Iterate through the target query set (role titles × locations), paginating each. Track completed queries in a local state file so the process can resume if interrupted.

**Daily mode:** Same query set, filtered by `date_posted:today` or SerpAPI's `chips` parameter for recent listings. Fetch first 1-2 pages per query only.

**Budget management:** SerpAPI's 1,000 searches/month ≈ 33/day. Daily steady-state should stay under 30 queries to preserve headroom. Prioritize breadth of role titles over geographic variation — Google Jobs already has geographic reach.

### Step 2: Fuzzy Dedup

```
pipeline/dedup.py
├── Input: raw job dict from fetcher
├── Normalize: lowercase, strip punctuation, expand abbreviations
│   ("Sr." → "senior", "Eng." → "engineer", etc.)
├── Generate dedup_hash: hash(normalized_title + normalized_company + normalized_location)
├── Check: does this hash exist in the DB?
│   ├── Exact hash match → skip, mark as duplicate
│   └── No exact match → secondary fuzzy check:
│       Query jobs with same normalized company_name (pg_trgm index),
│       run thefuzz.token_sort_ratio on title, threshold ≥ 85
│       ├── Match found → skip, mark as duplicate
│       └── No match → pass through to extraction
└── Output: deduplicated job dicts
```

**Performance:** The fuzzy check scopes to same-company listings only, avoiding O(n²) comparison. The `pg_trgm` GIN index on `company_name` makes the company lookup fast.

### Step 3: LLM Metadata Extraction

```
pipeline/extractor.py
├── Input: raw job description + qualifications + responsibilities
├── System prompt includes: skills.md and frameworks.md as reference taxonomy
├── LLM call: cheap model via OpenRouter, structured JSON output
├── Extracts:
│   ├── employment_type (enum)
│   ├── attendance (enum)
│   ├── seniority (enum)
│   ├── experience_years_min / max (integers)
│   ├── salary_min / max / currency / period (if present)
│   ├── skills (list of strings — model instructed to use canonical names from skills.md)
│   └── frameworks (list of strings — same, from frameworks.md)
├── Pass extracted skills/frameworks through normalizer (Step 3a)
└── Output: structured job record ready for insertion
```

**Prompt design:**

- Force JSON output with a strict schema definition.
- Include 2-3 few-shot examples demonstrating correct extraction.
- Instruct the model to use canonical names from the provided taxonomy. If the model encounters a skill/framework not in the taxonomy, it should return the name as-is (the normalizer handles it downstream).
- Instruct the model to return `null` for any field not clearly present in the listing — never guess.
- Keep the prompt minimal beyond these requirements. This is a classification/extraction task.

**System prompt size consideration:** `skills.md` and `frameworks.md` are included in every extraction call. Monitor their token count — if they grow beyond ~1.5K tokens combined, consider trimming the system prompt to a flat list of canonical names only and keeping hierarchy metadata (domain, core_competency, etc.) in the database and CSV files only.

**Error handling:** If the LLM returns malformed JSON, retry once with the same input. If it fails again, store the job with `status = 'extraction_failed'` and the raw `serp_api_json` intact. Reprocess failed jobs in batch later.

### Step 3a: Skill & Framework Normalization

```
pipeline/normalizer.py
├── On startup: load alias tables from DB into in-memory dicts
│   skill_alias_map:     {"postgres": 42, "postgresql": 42, "pg": 42, ...}
│   framework_alias_map: {"k8s": 17, "kube": 17, "kubernetes": 17, ...}
├── For each skill string returned by the LLM:
│   1. Lowercase and strip whitespace
│   2. Check skill_alias_map → match? → return canonical skill_id
│   3. Check skills table by name (exact match) → match? → return skill_id
│   4. No match → INSERT into skills with is_candidate = 1, return new skill_id
├── Same logic for frameworks
└── Output: list of resolved skill_ids and framework_ids for junction table insertion
```

**Alias table seeding:** Populate initial aliases from a `skill_aliases.csv` and `framework_aliases.csv` alongside the canonical taxonomy CSVs. Start with 50-100 common variants for your target field. Expand as you observe candidate entries that are obvious variants.

**Cache invalidation:** When you manually add new aliases or promote candidates, either restart the pipeline or expose a `reload_aliases()` method that refreshes the in-memory dicts from the DB.

### Step 4: Embed + Store

```
pipeline/embedder.py
├── Compose embedding string:
│   "{title} | {qualifications} | {responsibilities} | {skills} | {frameworks}"
├── Truncation strategy (if input exceeds 384 tokens):
│   Priority order: title > qualifications > responsibilities > skills > frameworks
│   Truncate from the end of the lowest-priority field first
├── Generate embedding: sentence-transformers all-mpnet-base-v2 (local)
├── INSERT job record + embedding into jobs table
└── INSERT resolved skill_ids / framework_ids into junction tables
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

The resume is embedded using the same model and a parallel string template adapted for resume context:
`"{target_role} | {qualifications_summary} | {experience_summary} | {skills} | {frameworks}"`

### Tier 2 — Cheap LLM Scoring (via OpenRouter)

For each of the top 100 candidates from Tier 1, send to the cheap model:

```
System: You are a job matching evaluator. Given a resume and a job listing,
        score the match from 0-100 and provide a one-sentence explanation.
        Respond in JSON: {"score": int, "explanation": string}

User: RESUME: {resume_text}
      JOB: {title} at {company_name}
      Description: {description}
      Qualifications: {qualifications}
```

**Execution:** Run concurrently (asyncio + httpx) with appropriate rate limiting for the chosen model. 100 calls to a cheap model completes in ~15-30 seconds at negligible cost.

Store `tier2_score` and `tier2_explanation` on the jobs table.

### Tier 3 — Claude Deep Analysis

Take the top 10-15 by `tier2_score`. Send to Claude with a detailed prompt including the full resume:

**Outputs per job:**

- Fit score (0-100)
- Strengths: where the resume matches well
- Gaps: where the resume falls short
- Recommendation: apply / apply with caveats / skip
- Suggested resume adjustments for this specific role

Store `tier3_score` and `tier3_explanation`.

---

## Candidate Review Workflow

Periodically review skills/frameworks where `is_candidate = 1`:

```sql
-- See all candidate skills, ordered by how many jobs reference them
SELECT s.skill_id, s.name, COUNT(js.job_id) AS job_count
FROM skills s
JOIN job_skills js ON s.skill_id = js.skill_id
WHERE s.is_candidate = 1
GROUP BY s.skill_id, s.name
ORDER BY job_count DESC;
```

**Actions per candidate:**

1. **Promote:** Set `is_candidate = 0`, assign proper `domain` / `core_competency` / `competency` values. The skill is now part of the canonical taxonomy.
2. **Merge:** The candidate is a variant of an existing skill. Add an entry to `skill_aliases` mapping the candidate name to the existing `skill_id`. Update all `job_skills` rows to point to the canonical `skill_id`. Delete the candidate row.
3. **Discard:** The candidate is noise (e.g. the LLM extracted a sentence fragment as a skill). Delete the candidate and its `job_skills` entries.

**Semi-automated duplicate detection:** Embed each candidate name using the same embedding model, compute cosine similarity against all canonical skill name embeddings, and flag any pair with similarity > 0.85 as a probable duplicate for human review.

---

## Project Structure

```
job-pipeline/
├── config/
│   ├── settings.py              # API keys, DB connection, model identifiers
│   └── queries.yaml             # Search query definitions (roles × locations)
├── pipeline/
│   ├── fetcher.py               # SerpAPI ingestion (backfill + daily)
│   ├── dedup.py                 # Fuzzy deduplication
│   ├── extractor.py             # LLM metadata extraction via OpenRouter
│   ├── embedder.py              # Embedding generation (all-mpnet-base-v2)
│   ├── normalizer.py            # Skill/framework alias resolution + candidate insertion
│   └── orchestrator.py          # Ties pipeline steps together, supports reprocess mode
├── matching/
│   ├── tier1_vector.py          # pgvector similarity search
│   ├── tier2_cheap_llm.py       # Batch cheap LLM scoring via OpenRouter
│   └── tier3_deep_analysis.py   # Claude detailed analysis
├── llm/
│   └── client.py                # Thin OpenRouter wrapper — accepts model ID, returns structured output
├── db/
│   ├── schema.sql               # DDL (the schema defined above)
│   ├── seed/
│   │   ├── skills.csv           # Initial canonical skills from skills.md
│   │   ├── frameworks.csv       # Initial canonical frameworks from frameworks.md
│   │   ├── skill_aliases.csv    # Known variant mappings
│   │   └── framework_aliases.csv
│   ├── migrations/              # Schema evolution
│   └── connection.py            # DB connection pool (psycopg or asyncpg)
├── scripts/
│   ├── backfill.py              # One-time historical ingestion
│   ├── daily_run.py             # Daily pipeline entry point (cron target)
│   ├── match_resume.py          # Run the 3-tier matching flow
│   ├── review_candidates.py     # Semi-automated candidate skill review
│   └── reprocess.py             # Re-run extraction on stored serp_api_json
├── data/
│   ├── resume.md                # Your resume in structured format
│   ├── skills.md                # Skill taxonomy (included in LLM system prompt)
│   └── frameworks.md            # Framework taxonomy (included in LLM system prompt)
├── tests/
├── requirements.txt
└── README.md
```

---

## Cost Estimates (Monthly, Steady-State)

| Item                         | Volume         | Est. Cost       |
| ---------------------------- | -------------- | --------------- |
| SerpAPI                      | ~900 searches  | $25.00 (plan)   |
| Cheap model (extraction)     | ~9,000 jobs    | ~$0.50–1.00     |
| Cheap model (tier 2 scoring) | ~100/query run | ~$0.01/run      |
| Claude (tier 3 analysis)     | ~15/query run  | ~$0.50–1.00/run |
| all-mpnet-base-v2            | local          | $0              |
| PostgreSQL                   | local          | $0              |
| **Total**                    |                | **~$27/mo**     |

---

## Open Decisions / Future Work

1. **System prompt size monitoring:** Track the token count of `skills.md` + `frameworks.md` as the taxonomy grows. If combined size exceeds ~1.5K tokens, trim the system prompt to a flat name list and keep hierarchy metadata in the database only.

2. **Job expiry:** Mark listings as expired after 30 days unless refreshed by a subsequent SerpAPI fetch:

   ```sql
   UPDATE jobs SET status = 'expired'
   WHERE date_listed < NOW() - INTERVAL '30 days'
   AND status = 'active';
   ```

   Run this as part of the daily cron.

3. **Reprocessing pipeline:** When the extraction prompt improves, re-run extraction against stored `serp_api_json` without re-fetching from SerpAPI. The orchestrator should support a `reprocess` mode that reads from the DB.

4. **Embedding model upgrade path:** If retrieval quality disappoints, swap to a larger model (e.g. `bge-large-en-v1.5`, 1024-dim) by re-embedding the corpus from stored text. Alter the vector column dimension and rebuild the HNSW index.

5. **Public website (Goal 2, do not build yet):**
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
     GROUP BY s.name
     ORDER BY demand DESC
     LIMIT 25;
     ```
   - User resume matching would expose Tiers 1–2 only (free tier) with Tier 3 behind auth/payment.
