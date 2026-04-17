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
    status                TEXT DEFAULT 'active', -- 'active','expired','closed','bad_listing'
    serp_api_json         JSONB,               -- full raw SerpAPI response for reprocessing
    embedding             vector(768),         -- all-mpnet-base-v2 output
    dedup_hash            TEXT UNIQUE,         -- SHA-256 of normalised title+company+location

    -- Relevance scores (tier2 populated at ingestion; tier3 populated on demand)
    tier2_score           REAL,
    tier2_explanation     TEXT,
    tier3_score           REAL,
    tier3_explanation     TEXT
);


-- ============================================================
-- APPLICATIONS TABLE
-- ============================================================
-- Tracks every job application submitted from this pipeline.
-- applications.job_id  → jobs (which job was applied to)
-- jobs.application_id  → applications (back-pointer set when an application exists)
-- The back-pointer is added via ALTER TABLE below to break the circular FK dependency.

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

-- Back-pointer from jobs to their application (NULL until an application exists).
-- IF NOT EXISTS makes this idempotent on re-runs.
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS application_id INTEGER
        REFERENCES applications(application_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_applications_job
    ON applications (job_id);


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

-- ============================================================
-- DASHBOARD SCHEMA ADDITIONS
-- ============================================================

-- Application progress tracking (separate from jobs.status)
ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS state TEXT
    CHECK (state IN ('submitted', 'rejected', 'interviewing', 'offer', 'withdrawn'));

-- Backfill: existing applications with offer=1 → 'offer', else → 'submitted'
DO $$ BEGIN
    UPDATE applications SET state = 'offer'     WHERE offer = 1 AND state IS NULL;
    UPDATE applications SET state = 'submitted' WHERE state IS NULL;
END $$;

-- Dashboard-optimized indexes
CREATE INDEX IF NOT EXISTS idx_jobs_tier2_score
    ON jobs (tier2_score DESC NULLS LAST) WHERE tier2_score IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_applications_state
    ON applications (state);
