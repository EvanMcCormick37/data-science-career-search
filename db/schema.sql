-- schema.sql
-- Run via: psql $DATABASE_URL -f db/schema.sql
-- Or via:  python -m db.seed.seed  (which runs this file then seeds taxonomy data)

-- ── Extensions ────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector: embedding storage + ANN search
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- trigram indexes for fuzzy company matching


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
    employment_type       TEXT,                -- 'full-time','part-time','contract','internship'
    attendance            TEXT,                -- 'remote','hybrid','onsite'
    seniority             TEXT,                -- 'junior','mid','senior','lead','staff','principal'
    experience_years_min  INTEGER,
    experience_years_max  INTEGER,
    salary_min            INTEGER,
    salary_max            INTEGER,
    salary_currency       TEXT DEFAULT 'USD',
    salary_period         TEXT,                -- 'yearly','hourly','monthly'
    qualifications        TEXT,                -- extracted from job_highlights
    responsibilities      TEXT,               -- extracted from job_highlights
    date_listed           DATE,
    date_ingested         TIMESTAMP DEFAULT NOW(),
    date_updated          TIMESTAMP DEFAULT NOW(),
    status                TEXT DEFAULT 'active', -- 'active','expired','duplicate','extraction_failed'
    serp_api_json         JSONB,               -- full raw SerpAPI response for reprocessing
    embedding             vector(768),         -- all-mpnet-base-v2 output
    dedup_hash            TEXT UNIQUE,         -- SHA-256 of normalised title+company+location

    -- Relevance scores (populated lazily per query run, resume-specific)
    tier2_score           REAL,
    tier2_explanation     TEXT,
    tier3_score           REAL,
    tier3_explanation     TEXT
);


-- ============================================================
-- SKILLS TAXONOMY
-- ============================================================

CREATE TABLE IF NOT EXISTS skills (
    skill_id        SERIAL PRIMARY KEY,
    domain          TEXT NOT NULL,
    core_competency TEXT,
    competency      TEXT,
    name            TEXT UNIQUE NOT NULL,
    is_candidate    INTEGER DEFAULT 0          -- 1 = LLM-proposed, pending review
);

CREATE TABLE IF NOT EXISTS skill_aliases (
    alias    TEXT PRIMARY KEY,                 -- lowercase variant (e.g. 'etl')
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

CREATE INDEX IF NOT EXISTS idx_jobs_embedding
    ON jobs USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_jobs_status
    ON jobs (status);

CREATE INDEX IF NOT EXISTS idx_jobs_date_listed
    ON jobs (date_listed);

CREATE INDEX IF NOT EXISTS idx_jobs_dedup_hash
    ON jobs (dedup_hash);

CREATE INDEX IF NOT EXISTS idx_jobs_company_trgm
    ON jobs USING gin (company_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_jobs_title_trgm
    ON jobs USING gin (title gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_skill_aliases_skill
    ON skill_aliases (skill_id);

CREATE INDEX IF NOT EXISTS idx_framework_aliases_framework
    ON framework_aliases (framework_id);
