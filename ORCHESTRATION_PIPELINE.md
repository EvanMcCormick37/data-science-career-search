# Orchestration Pipeline — Upgrade Plan

## Executive Summary

The current pipeline is a well-structured set of Python scripts with a clean 6-step ingestion flow. It runs locally via cron and has no cloud deployment, no fault-tolerant state management, no CI/CD, and no infrastructure-as-code. This document describes a phased upgrade to a cloud-native, fully orchestrated data pipeline following MLOps and DataOps best practices — staying within the $50/month total budget (existing spend is ~$28/month on SerpAPI + LLMs, leaving ~$22/month for infrastructure).

---

## Current State Assessment

### What works well (keep)

- Clean step separation: `fetcher → dedup → extractor → normalizer → embedder → scorer → store`
- `serp_api_json` audit trail enables reprocessing without re-fetching — already a checkpointing primitive
- Alias tables in Postgres for taxonomy normalization — durable and curate-able
- Two-tier scoring: cheap LLM at ingestion, expensive LLM on demand
- `status` field on jobs (`active`, `bad_fit`, `expired`, `extraction_failed`) — natural state machine in the DB

### What needs replacing

| Problem | Impact |
|---|---|
| Cron-scheduled local scripts | No cloud deployment, no fault tolerance |
| `try/except` + `stats` dict inside `Orchestrator.process_batch` | No retry logic per step; a failure mid-batch loses all progress |
| Backfill state tracked in a local file | Not portable, not observable |
| No tests (single `test_pipeline.py`) | Can't safely refactor or ship CI |
| No Docker/containerization | Environment drift between dev and any deployment |
| No secrets management | API keys in `.env` only |
| Manual `python scripts/daily_run.py` | No scheduling UI, no alerting on failure |
| No data lineage | Can't trace a job record back to the pipeline run that created it |

---

## Target Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  GitHub (source of truth)                                            │
│  ├── Code + IaC (Pulumi)                                             │
│  ├── GitHub Actions CI/CD                                            │
│  └── GitHub Container Registry (Docker images)                      │
└────────────────────────────┬────────────────────────────────────────┘
                             │ push to main → build → deploy
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Oracle Cloud Always Free (ARM VM, 4 OCPU / 24 GB RAM)              │
│  └── Prefect Worker (Docker container)                               │
│       ├── Runs flows on schedule set by Prefect Cloud                │
│       ├── Loads embedding models from persistent block volume        │
│       └── Connects to Neon (DATABASE_URL) + OpenRouter + SerpAPI     │
└────────────────────────────┬────────────────────────────────────────┘
                             │ reads/writes
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Neon.tech (managed serverless PostgreSQL + pgvector)                │
│  └── Same schema as today — zero migration required                  │
└─────────────────────────────────────────────────────────────────────┘
                             ▲ observes flows
┌─────────────────────────────────────────────────────────────────────┐
│  Prefect Cloud (free tier)                                           │
│  ├── Schedules daily_ingestion flow (cron: 0 7 * * *)               │
│  ├── Flow run history, state UI, log viewer                          │
│  └── Email/Slack alerting on flow failure                            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Orchestration Framework: Prefect

### Why Prefect

Three frameworks were evaluated:

| Framework | Hosting cost | Learning curve | Python-native | Free scheduling UI |
|---|---|---|---|---|
| **Prefect** | $0 (Cloud free tier) | Low | Yes — `@task`, `@flow` decorators | Yes |
| Dagster | $0 (Cloud free tier) | Medium-high | Yes — assets/ops model | Yes |
| Airflow | $50–200+/month managed, or significant self-host ops | Medium | No — DAG files, YAML config | Requires self-host |
| GitHub Actions | $0 | Low | No — YAML only | Limited |
| Temporal | $0 self-host, expensive cloud | High | Partially | No |

**Prefect wins** for this project because:

1. The existing `Orchestrator.process_batch()` method maps almost 1:1 to a Prefect `@flow`. Each step inside it becomes a `@task` with retry semantics — this is the smallest possible refactor surface.
2. Prefect Cloud free tier includes: unlimited flow runs, 3 users, 30-day run history, scheduling, alerting. No self-hosted server to operate.
3. Task-level retries with configurable backoff are built in — right now extraction failures just mark the job as `extraction_failed` with no retry attempt.
4. Prefect's `ConcurrentTaskRunner` can parallelize independent tasks (e.g. embed + score can run concurrently once extraction completes).

**Dagster** is a worthy alternative if asset lineage and data quality checks become priorities — its software-defined asset model would expose each job record as a traceable data asset. It can be adopted later without replacing Prefect (they serve different layers).

### Flow Structure (Target)

```
flows/
├── daily_ingestion.py       # fetch + ingest + expire old listings
├── backfill.py              # historical multi-page ingestion
├── score_top_jobs.py        # on-demand tier3 deep analysis
├── reprocess_failed.py      # re-extraction from stored serp_api_json
└── utils/
    └── notify.py            # shared alerting helpers
```

### How the Ingestion Flow Maps to Tasks

```python
# flows/daily_ingestion.py (conceptual — not yet implemented)

@flow(name="daily-ingestion", retries=1)
def daily_ingestion_flow(max_pages: int = 1):
    queries   = load_queries_task()                     # reads config/queries.yaml
    raw_jobs  = fetch_jobs_task(queries, max_pages)     # fetcher.py; retries=2
    new_jobs  = dedup_task(raw_jobs)                    # dedup.py; filters duplicates
    extracted = extract_task.map(new_jobs)              # extractor.py; retries=3, exponential backoff
    normed    = normalize_task.map(extracted)           # normalizer.py
    embedded  = embed_task.map(normed)                  # embedder.py
    scored    = score_task.map(embedded)                # scorer.py; retries=2
    store_task.map(scored)                              # db/operations.py
    expire_old_listings_task()                          # marks stale jobs expired
```

Key upgrade from today: each `@task` has independent retry logic. If `extract_task` fails on job 47 of 200, only job 47 retries — the other 199 are not re-run. Prefect persists the task state so a flow restart skips already-completed tasks (via result caching).

### State Persistence Strategy

Prefect task result caching is keyed on input hash. Combined with our existing DB design:

- `serp_api_json` — raw API response stored at fetch time. Already checkpoints against re-fetching.
- `dedup_hash` — already prevents re-insertion of duplicates across runs.
- Add `pipeline_run_id TEXT` column to `jobs` table — populated with the Prefect flow run ID at insert time. Links every job to the exact code version and run that created it.
- Extraction failures: Prefect retries the task; if all retries exhaust, the job is marked `extraction_failed` in DB (same as today) and the run continues.

---

## Infrastructure

### Database: Neon.tech (Free Tier)

Neon is a serverless PostgreSQL provider with native pgvector support. The free tier provides:
- 0.5 GB storage (sufficient: ~9,000 jobs/month × ~5 KB/job = ~45 MB/month including vectors)
- Auto-suspend when idle (no cost for idle time — the pipeline only writes daily)
- Connection pooler (PgBouncer) built in — critical since the Prefect worker spawns concurrent tasks
- Branching: create a `dev` branch of the database for testing schema changes without affecting production

**Migration from local PostgreSQL:** Export via `pg_dump`, import to Neon via `pg_restore`. The schema is already idempotent (`IF NOT EXISTS` everywhere). Zero application code changes needed — only `DATABASE_URL` changes in `.env`.

**Upgrade path:** Neon Pro is $19/month if storage or compute hours become a constraint. This remains within budget.

**Alternative considered:** Self-hosted PostgreSQL on the Oracle Always Free VM. Cheaper long-term but requires managing backups, updates, and pgvector installation. Neon's managed overhead is worth the simplicity here.

### Compute: Oracle Cloud Always Free (ARM)

Oracle's Always Free tier includes 4 ARM-based Ampere A1 CPUs and 24 GB RAM (allocatable across up to 4 VMs). This is not a trial — it does not expire.

Allocation for this project:
- **1 VM: 4 OCPU / 24 GB RAM** — runs the Prefect worker process in Docker
  - `all-mpnet-base-v2` requires ~600 MB RAM when loaded; `all-MiniLM-L6-v2` adds ~100 MB
  - The remaining RAM handles concurrent task execution during ingestion
- 200 GB block storage — model cache volume (`models/` directory, gitignored)

**Why not GitHub Actions for compute?** GitHub-hosted runners have 7 GB RAM and a 6-hour job limit. Loading two sentence-transformer models takes ~2 minutes and ~700 MB RAM — feasible but wasteful to repeat every daily run. A persistent worker with a warm model cache is more efficient and reliable for this pattern.

**Alternative: Fly.io free tier** — 3 shared-CPU VMs with 256 MB RAM each. Insufficient for the embedding models without the paid plan.

### CI/CD: GitHub Actions

```
.github/workflows/
├── ci.yml          # Runs on every PR: lint + type-check + tests
└── deploy.yml      # Runs on push to main: build Docker image → push to GHCR → deploy to Oracle VM
```

`ci.yml` pipeline:
1. `ruff check` + `black --check` — code style
2. `mypy` — type checking
3. `pytest tests/` — unit + integration tests (using a local SQLite/test DB)

`deploy.yml` pipeline:
1. Build Docker image tagged with `git SHA`
2. Push to `ghcr.io/<username>/job-pipeline:<sha>` (GitHub Container Registry — free)
3. SSH into Oracle VM, pull new image, restart Prefect worker container

**Environments:** GitHub Environments (`staging`, `production`) gate the deploy step behind a required approval for production if needed. For now, auto-deploy to production on merge to `main` is appropriate.

### Infrastructure as Code: Pulumi (Python)

Pulumi manages all cloud resources as Python code — the same language as the application. The Community plan is free for individuals and includes unlimited resources and state stored in Pulumi Cloud.

```
infra/
├── __main__.py          # Pulumi program
├── Pulumi.yaml          # Project definition
├── Pulumi.dev.yaml      # Stack config (dev)
└── Pulumi.prod.yaml     # Stack config (prod)
```

Resources managed by Pulumi:
- Neon project + branch + database + role (via Neon Pulumi provider)
- Oracle Cloud Compute instance + block volume + network config (via OCI provider)
- GitHub repository secrets (DATABASE_URL, API keys) — via GitHub Pulumi provider
- Prefect deployment + schedule — via Prefect Pulumi provider (or Prefect CLI in deploy.yml)

**Why Pulumi over Terraform:** Python-native fits the team's existing skill set — no HCL to learn. State management, secret handling, and drift detection are equivalent between the two. Terraform is the better-known tool and either would work; Pulumi is recommended here because the infra code lives in the same language as everything else.

### Secrets Management: GitHub Secrets (CI/CD) + Doppler (Runtime)

**CI/CD secrets:** GitHub repository secrets hold API keys used in GitHub Actions (for building and deploying). Free, already integrated.

**Runtime secrets on the VM:** Two options:
1. **Simple:** Bake secrets into the Prefect deployment as environment variables, pulled from GitHub Secrets during `deploy.yml` and injected via `docker run --env-file`.
2. **Better:** Doppler free tier (5,000 reads/month) — a secrets manager that syncs to your Docker container at runtime, rotatable without redeploying.

Start with option 1. Migrate to Doppler if secret rotation becomes a pain point.

---

## Cost Breakdown (Target State)

| Item | Provider | Cost |
|---|---|---|
| SerpAPI (900 searches/month) | SerpAPI | $25.00 |
| Cheap LLM (extraction + scoring, ~9K jobs) | OpenRouter | ~$1.50 |
| Expensive LLM (deep analysis, on-demand) | OpenRouter | ~$0.50/run |
| Compute (Prefect worker + models) | Oracle Cloud Always Free | **$0** |
| PostgreSQL + pgvector | Neon.tech Free | **$0** |
| Workflow orchestration + scheduling | Prefect Cloud Free | **$0** |
| CI/CD + container registry | GitHub Actions + GHCR | **$0** |
| Infrastructure as Code | Pulumi Community | **$0** |
| Secrets (basic) | GitHub Secrets | **$0** |
| **Total** | | **~$27–29/month** |

Well within the $50/month budget. The $20+ headroom allows upgrading to Neon Pro ($19/month) if data volume grows, or adding Doppler for secrets management, without exceeding budget.

---

## Phased Upgrade Plan

### Phase 0 — Foundations (Week 1–2)

**Goal:** Make the codebase deployable and testable before touching orchestration.

- [ ] Add `Dockerfile` — multi-stage build: `python:3.11-slim` base, installs dependencies, copies source, pre-downloads embedding models into image (bakes in the cache)
- [ ] Add `docker-compose.yml` for local development: app container + local PostgreSQL with pgvector
- [ ] Add `pyproject.toml` — consolidate tooling config (ruff, black, mypy, pytest settings)
- [ ] Add pre-commit hooks: `ruff`, `black`, `mypy` — enforced locally and in CI
- [ ] Replace `requirements.txt` with `uv`-managed lockfile (`uv lock`) for reproducible installs
- [ ] Add `.github/workflows/ci.yml` — runs lint + type check + tests on every PR
- [ ] Write initial test suite: unit tests for `dedup.py`, `normalizer.py`; integration test for `orchestrator.process_batch()` against a test fixture

**No changes to pipeline logic in this phase.** The goal is a green CI baseline before any refactor.

**Deliverable:** Every push to a PR runs tests and fails loudly before any broken code can merge.

---

### Phase 1 — Prefect Orchestration (Week 3–5)

**Goal:** Convert the pipeline to Prefect flows without changing behavior.

**Step 1.1 — Task-ify the pipeline steps**

Each step in `Orchestrator.process_batch()` becomes a standalone `@task`. The `Orchestrator` class stays intact as a library; a new `flows/daily_ingestion.py` wraps it in Prefect primitives.

Tasks to create:
```
flows/tasks/
├── fetch.py          # @task wrapping pipeline/fetcher.py
├── dedup.py          # @task wrapping pipeline/dedup.py
├── extract.py        # @task(retries=3, retry_delay_seconds=exponential_backoff) wrapping extractor.py
├── normalize.py      # @task wrapping normalizer.py
├── embed.py          # @task wrapping embedder.py
├── score.py          # @task(retries=2) wrapping scorer.py
├── store.py          # @task(retries=2) wrapping db/operations.py insert_job
└── expire.py         # @task wrapping the expiry logic in daily_run.py
```

**Step 1.2 — Create flows**

```
flows/
├── daily_ingestion.py     # @flow orchestrating all tasks above
├── backfill.py             # @flow wrapping scripts/backfill.py logic
├── score_top_jobs.py      # @flow wrapping scripts/score_top_jobs.py
└── reprocess_failed.py    # @flow wrapping scripts/reprocess.py
```

**Step 1.3 — Run locally with Prefect**

```bash
prefect server start                    # local UI at http://localhost:4200
prefect deploy flows/daily_ingestion.py  # register the flow
python flows/daily_ingestion.py         # test run
```

**Step 1.4 — Add `pipeline_run_id` to jobs table**

```sql
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS pipeline_run_id TEXT;
```

Populate this with the Prefect flow run ID (`prefect.context.get_run_context().flow_run.id`) in `store_task`. This creates a link from every job record to the exact orchestrated run — and by extension, to the git SHA of the code that ran it (stored in the Docker image tag, which Prefect logs).

**Deliverable:** The same daily ingestion logic runs as a Prefect flow locally, with task-level retries and a flow run dashboard.

---

### Phase 2 — Cloud Infrastructure (Week 6–8)

**Goal:** Provision cloud resources with Pulumi and deploy the worker.

**Step 2.1 — Provision Neon database**

```bash
pip install pulumi pulumi-neon
cd infra
pulumi stack init prod
pulumi up
```

The Pulumi program creates:
- Neon project `job-pipeline-prod`
- `main` branch (production) + `dev` branch (for testing schema changes)
- Database `jobs_db`, role `pipeline_user`
- Outputs `DATABASE_URL` as a stack export → saved to GitHub repository secret

**Step 2.2 — Migrate data to Neon**

```bash
pg_dump $LOCAL_DATABASE_URL | psql $NEON_DATABASE_URL
```

Validate: row counts match, pgvector queries work, HNSW index rebuilds on Neon.

**Step 2.3 — Provision Oracle Cloud ARM VM**

```bash
# infra/__main__.py provisions:
# - VCN + subnet + security list (port 22 SSH)
# - Compute instance: VM.Standard.A1.Flex, 4 OCPU, 24GB RAM
# - Block volume: 50GB attached at /opt/models (model cache)
# - Cloud-init: installs Docker, sets up systemd service for Prefect worker
```

**Step 2.4 — Deploy Prefect worker**

```bash
# On the Oracle VM:
docker pull ghcr.io/<username>/job-pipeline:latest
prefect worker start --pool "oracle-arm" --type process
```

Register the Prefect deployment pointing at Prefect Cloud:
```bash
prefect deploy --name daily-ingestion \
  --schedule "0 7 * * *" \               # 7am UTC daily
  --pool "oracle-arm"
```

**Deliverable:** The pipeline runs daily on Oracle Cloud, monitored by Prefect Cloud UI, writing to Neon. Local machine is no longer in the critical path.

---

### Phase 3 — Full CI/CD (Week 9–10)

**Goal:** Every merge to `main` automatically builds, tests, and deploys a new version.

```yaml
# .github/workflows/deploy.yml
on:
  push:
    branches: [main]

jobs:
  build-and-deploy:
    steps:
      - Build Docker image tagged with $GITHUB_SHA
      - Push to ghcr.io/<username>/job-pipeline:$GITHUB_SHA
      - Also tag as :latest
      - SSH into Oracle VM
      - docker pull ghcr.io/<username>/job-pipeline:latest
      - Restart Prefect worker container
      - Run smoke test: prefect deployment run daily-ingestion/daily-ingestion --param max_pages=0
```

**Version pinning:** Each job in the DB has `pipeline_run_id` which maps to a Prefect flow run, which was executed with a Docker image tagged with a git SHA. Full traceability: job record → pipeline run → code version.

**Rollback:** `docker pull ghcr.io/<username>/job-pipeline:<previous-sha>` and restart. No Helm, no Kubernetes required at this scale.

**Deliverable:** Push to `main` → tests pass → new image deployed → daily run uses new code automatically.

---

### Phase 4 — Observability (Week 11–12)

**Goal:** Know when something breaks before it silently loses a day of data.

**Prefect alerting (built-in, free):**
- Email alert if any flow run enters `Failed` state
- Email alert if daily ingestion hasn't completed by 10am UTC (automation: Prefect SLA feature)

**Structured logging:**

Replace `logging.getLogger(__name__)` calls with structured JSON logging using `structlog`:

```python
import structlog
log = structlog.get_logger()
log.info("job_inserted", job_id=job_id, title=job_record["title"], tier2_score=fit_score)
```

Structured logs are queryable in Prefect Cloud's log viewer and trivially parseable if forwarded to a log aggregator later.

**Data quality checks (Pandera):**

Add lightweight schema validation on the extraction output before normalization:

```python
import pandera as pa

ExtractionSchema = pa.DataFrameSchema({
    "employment_type": pa.Column(str, nullable=True, checks=pa.Check.isin(["full-time", "part-time", "contract", "internship", None])),
    "seniority": pa.Column(str, nullable=True),
    # ...
})
```

If extraction produces malformed output (e.g., model returns unexpected enum values), the task fails fast with a clear error rather than silently inserting junk data.

**Metrics (optional, free):**

Grafana Cloud free tier includes 10K series of Prometheus metrics and 50GB logs. A simple Prometheus push from the Prefect flow (jobs inserted, duplicates, failures per run) gives a trend chart without any paid tooling.

---

### Phase 5 — MLOps Polish (Ongoing)

**Goal:** Treat prompts and models as versioned artifacts, not configuration strings.

**Prompt versioning:**

The extraction and scoring prompts live in `pipeline/extractor.py` and `pipeline/scorer.py`. Move prompt templates to `prompts/` as versioned text files tracked in git. The `pipeline_run_id` on each job record already links it to the git SHA, so you know exactly which prompt version produced each extraction.

```
prompts/
├── extraction_v1.md       # Current extraction system prompt
├── extraction_v2.md       # Experimental — test against a sample
└── scoring_v1.md
```

**Model versioning (DVC):**

The `models/` directory is gitignored. As the embedding model cache grows (~500MB), use DVC (free, open-source) to version model artifacts against a free DVC remote (e.g., Google Drive or an S3-compatible provider with free tier). This means the exact model version used to produce each embedding is reproducible.

**A/B testing prompts/models:**

Prefect supports flow parameters. A `--model gemini-flash` vs `--model kimi-k2` comparison can be run as two separate flow runs with different `EXTRACTION_MODEL` parameters. The `pipeline_run_id` column makes it trivial to compare extraction quality between runs.

**Schema migration management (Alembic):**

As the DB schema evolves (e.g., adding `pipeline_run_id`, adding a `salary_confidence` column), migrations should be managed with Alembic rather than manual `ALTER TABLE` statements. Alembic integrates with the existing SQLAlchemy-free setup via raw SQL migration scripts. Each migration is a versioned file in `db/migrations/`.

---

## File Structure (Target State)

```
data-science-career-search/
├── .github/
│   └── workflows/
│       ├── ci.yml             # PR: lint + type check + tests
│       └── deploy.yml         # main: build → push → deploy
├── config/
│   ├── settings.py
│   ├── queries.yaml
│   └── queries.example.yaml
├── db/
│   ├── schema.sql
│   ├── connection.py
│   ├── operations.py
│   ├── migrations/            # NEW: Alembic migration scripts
│   │   ├── env.py
│   │   └── versions/
│   │       └── 001_add_pipeline_run_id.sql
│   └── seed/
│       ├── seed.py
│       ├── skills.csv
│       ├── frameworks.csv
│       ├── skill_aliases.csv
│       └── framework_aliases.csv
├── flows/                     # NEW: Prefect flows
│   ├── daily_ingestion.py
│   ├── backfill.py
│   ├── score_top_jobs.py
│   ├── reprocess_failed.py
│   └── tasks/
│       ├── fetch.py
│       ├── dedup.py
│       ├── extract.py
│       ├── normalize.py
│       ├── embed.py
│       ├── score.py
│       ├── store.py
│       └── expire.py
├── infra/                     # NEW: Pulumi IaC
│   ├── __main__.py
│   ├── Pulumi.yaml
│   ├── Pulumi.dev.yaml
│   └── Pulumi.prod.yaml
├── llm/
│   └── client.py
├── matching/
│   ├── tier1_vector.py
│   ├── tier2_cheap_llm.py
│   └── tier3_deep_analysis.py
├── pipeline/                  # Unchanged — business logic stays here
│   ├── fetcher.py
│   ├── dedup.py
│   ├── extractor.py
│   ├── normalizer.py
│   ├── embedder.py
│   ├── scorer.py
│   └── orchestrator.py
├── prompts/                   # NEW: versioned prompt templates
│   ├── extraction_v1.md
│   └── scoring_v1.md
├── scripts/                   # Kept for ad-hoc use; flows replace the scheduled ones
│   ├── backfill.py
│   ├── daily_run.py
│   ├── single_query.py
│   ├── reprocess.py
│   ├── score_top_jobs.py
│   ├── match_career_profile.py
│   └── review_candidates.py
├── tests/
│   ├── unit/
│   │   ├── test_dedup.py
│   │   └── test_normalizer.py
│   └── integration/
│       └── test_orchestrator.py
├── data/
│   ├── career_profile.md
│   ├── skills.md
│   └── frameworks.md
├── models/                    # gitignored; mounted as block volume in cloud
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── .pre-commit-config.yaml
└── README.md
```

---

## Key Design Principles

**Business logic in `pipeline/`, orchestration in `flows/`.**
The existing `pipeline/` modules contain all the domain logic and remain unchanged during the orchestration refactor. Prefect tasks in `flows/tasks/` are thin wrappers that add retry semantics, logging, and Prefect state tracking around calls into `pipeline/`. This means the pipeline remains testable without Prefect installed.

**The DB is the durable state store; Prefect is the execution state store.**
Prefect tracks whether a flow run succeeded or failed. The DB tracks what data was produced. The `pipeline_run_id` link between them is the audit trail. If Prefect state is lost, the DB state is intact and recoverable.

**Every config change is a code change.**
No "click in the UI to change the schedule" — the schedule lives in `flows/daily_ingestion.py` and is deployed via CI/CD. The Prefect UI is for observing runs, not configuring them.

**Infra changes go through `pulumi up` in CI, not the cloud console.**
The Oracle VM, Neon database, and secrets are declared in `infra/__main__.py`. Clicking in the OCI console is fine for debugging, but persistent changes require a Pulumi commit.

---

## Phased Timeline Summary

| Phase | Duration | Key Output |
|---|---|---|
| 0 — Foundations | 2 weeks | Docker, CI, pre-commit, initial tests |
| 1 — Prefect Orchestration | 3 weeks | Pipeline as Prefect flows, local Prefect server |
| 2 — Cloud Infrastructure | 3 weeks | Neon DB, Oracle VM, worker running in cloud |
| 3 — Full CI/CD | 2 weeks | Push to main → auto-deploy |
| 4 — Observability | 2 weeks | Alerting, structured logs, data quality checks |
| 5 — MLOps Polish | Ongoing | Prompt versioning, A/B testing, Alembic migrations |

Total to a production-grade cloud pipeline: **~10–12 weeks** of part-time work. Phases 0–3 are the critical path; Phases 4–5 are improvements on a working system.
