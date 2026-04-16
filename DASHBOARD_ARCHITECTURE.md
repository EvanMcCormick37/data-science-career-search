# Dashboard — Architecture

## Overview

A local web dashboard for browsing the `jobs` and `applications` tables, logging applications, editing job/application state, and editing the two user-owned config files (`config/queries.yaml`, `data/career_profile.md`). Built as a new top-level `app/` module that reads and writes through the existing `db/operations.py` layer — the dashboard adds no SQL of its own and no business logic that belongs in the pipeline.

**Design goals, in priority order:**

1. **Clone-and-run simplicity.** A second user (e.g. a non-technical family member) should be able to clone the repo, set up `.env`, and have the dashboard running with one extra command beyond what the pipeline already requires. No Node, no build step, no separate frontend install.
2. **Preserve existing invariants.** `db/operations.py` remains the only place SQL lives. The pipeline is not imported by the dashboard. Config and schema continue to follow existing patterns.
3. **Read-heavy, write-cautious.** The dashboard is primarily for browsing. The four write actions (log application, edit application, edit job status, edit config files) are each a single atomic operation with no background work, no drafts, and no partial state.
4. **Leave a clean seam for a future public dataviz site.** The public site is a separate concern with different data access patterns (aggregate, read-only, zero-tenant); the dashboard should not force architectural choices onto it, but should be colocatable in the same process if desired later.

**Explicit non-goals:**

- Running pipeline scripts from the UI. Ingestion, scoring, and backfill remain CLI-only. The dashboard displays results and shows freshness indicators; it does not trigger pipeline work.
- Multi-user auth, accounts, or row-level scoping. The dashboard is single-tenant per install.
- Mobile-first design. Desktop-primary, mobile-usable if effort permits, not a design constraint.
- LLM-powered UI features (cover letter generation, job summarization). These are future scope.
- Any charting or aggregate visualization. These belong to the future public dataviz work.

---

## Stack

**Backend:** FastAPI (Python 3.11+), Jinja2 templates, psycopg2 via the existing connection pool.
**Frontend:** Server-rendered HTML with HTMX for partial updates, Alpine.js for small client-side behaviors (side panel open/close, tag editing), Tailwind CSS via the browser CDN build for styling.
**Runtime:** Single `uvicorn` process. No build step, no bundler, no Node.

**Why this stack over FastAPI + React:**

The dashboard is a read-heavy app with four write actions, over a dataset of at most ~10K rows. It needs filterable tables, side panels, and simple forms — no rich client state, no offline behavior, no real-time collaboration. Server-rendered HTML with HTMX covers every one of the stated use cases at a fraction of the code surface:

- **One process, one install.** `pip install -e .` and `uvicorn app.main:app` is the entire startup story. Critical for the open-source clone-and-run goal.
- **No API contract to maintain.** Routes return HTML fragments, not JSON. This means no Pydantic request/response models for the frontend to consume, no TypeScript types to keep in sync, no codegen, no versioning.
- **The code you write is the code that runs.** Debugging means reading the route handler and the template. No build step, no transpile step, no hydration mismatch.
- **HTMX is boring in the right way.** `hx-get`, `hx-post`, `hx-target`, `hx-swap` covers 95% of what a dashboard like this needs. There is no state management library, because state lives on the server and in the URL.

**Where this stack stops being the right choice** (documented so future contributors can identify when to break the pattern):

- Features that require client-side computation (e.g. live-filtering 100K rows with no network round-trip).
- Animated, stateful interactive visualizations. When the public dataviz site needs real dataviz, a small React/Svelte island or a dedicated dataviz route using D3 or Plotly (loaded via CDN, no build step) is acceptable. That is a future decision, not a v1 one.
- Drag-and-drop Kanban boards, collaborative editing, or anything with optimistic UI.

**Why Alpine.js in addition to HTMX:** HTMX handles server-round-trip interactions. Alpine handles purely client-side UI state — open/close a panel, toggle a dropdown, show a confirmation modal. Using HTMX for these would mean unnecessary round-trips; writing vanilla JS for them would be verbose and inconsistent. Alpine is ~10KB, loaded via CDN, and has a syntax that matches HTMX's declarative-attribute philosophy.

**Why Tailwind CDN (not compiled):** Same reason as the rest of the stack — no build step. The CDN version (`https://cdn.tailwindcss.com`) is only recommended for small projects, but the dashboard is small, self-hosted, and the tradeoff (larger CSS payload) is invisible on localhost. If the dashboard is ever deployed publicly, this becomes the one piece worth swapping for a compiled build.

---

## System Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                          DASHBOARD (app/)                              │
│                      Single uvicorn process                            │
│                                                                        │
│  ┌────────────────┐    ┌─────────────────┐    ┌─────────────────┐    │
│  │    Routes      │──▶ │    Services     │──▶ │  db/operations  │    │
│  │  (HTMX/HTML)   │    │  (composition)  │    │    (SQL)        │    │
│  └────────────────┘    └─────────────────┘    └────────┬────────┘    │
│          │                      │                       │             │
│          ▼                      ▼                       ▼             │
│  ┌────────────────┐    ┌─────────────────┐    ┌─────────────────┐    │
│  │   Templates    │    │   File I/O      │    │   PostgreSQL    │    │
│  │  (Jinja2)      │    │  (config/       │    │   (+ pgvector)  │    │
│  │                │    │  resumes/)      │    │                 │    │
│  └────────────────┘    └─────────────────┘    └─────────────────┘    │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
             ▲
             │ (no coupling — both read the same DB)
             │
┌────────────────────────────────────────────────────────────────────────┐
│                       PIPELINE (unchanged)                             │
│            scripts/daily_run.py, backfill.py, score_top_jobs.py        │
└────────────────────────────────────────────────────────────────────────┘
```

**The dashboard does not import from `pipeline/` or `matching/`.** It imports from `db/` for data access and from `config/settings.py` for paths and environment. This keeps the two systems decoupled: the pipeline can evolve (new scorer, new extractor) without breaking the dashboard as long as the schema is stable.

---

## Layered Responsibilities

The dashboard has three layers. Each has a single job and a single rule.

### 1. Routes (`app/routes/*.py`)

**Job:** Parse input, call a service, return a response (HTML fragment or full page).

**Rule:** No logic. A route longer than ~15 lines is a signal that logic has leaked in and should be moved to a service. Routes contain no SQL, no file I/O, no business decisions.

### 2. Services (`app/services/*.py`)

**Job:** Compose `db/operations` calls and file I/O into user-facing operations. This is where "log an application" becomes "insert into applications, update jobs.status to 'applied', update jobs.application_id — as one transaction."

**Rule:** No SQL. A service that needs a query not yet in `db/operations.py` adds a function to `db/operations.py` rather than writing SQL inline. This preserves the existing invariant and means the dashboard can never drift from the pipeline's view of the data.

### 3. Data access (`db/operations.py`, existing)

**Job:** All SQL. All rows in, all rows out.

**Rule:** Unchanged. This file already has the right discipline; the dashboard extends it with additional read functions (filter-capable job queries, application joins) and a handful of new writes (status update, application status update, atomic log-application transaction).

**The rule that makes this work:** the dashboard must be deletable without touching `db/operations.py`. If you delete `app/`, the pipeline still works. If someone forks the project to build their own UI, `db/operations.py` is the stable interface they target.

---

## Directory Layout

```
app/
├── __init__.py
├── main.py                      # FastAPI app factory, lifespan, router registration
├── config.py                    # App-specific settings (host, port, paths for resume dir)
├── routes/
│   ├── __init__.py
│   ├── jobs.py                  # Jobs browser + detail panel + status edit
│   ├── applications.py          # Applications browser + detail panel + edit form
│   ├── actions.py               # Cross-cutting write actions (log application)
│   └── config_editor.py         # queries.yaml + career_profile.md editors
├── services/
│   ├── __init__.py
│   ├── jobs.py                  # Filter/sort/paginate logic, status transitions
│   ├── applications.py          # Log/edit application logic, resume resolution
│   └── config_files.py          # YAML/Markdown read/validate/write
├── templates/
│   ├── base.html                # Shell: nav, Tailwind CDN, HTMX + Alpine script tags
│   ├── jobs/
│   │   ├── index.html           # Full jobs page (table + filter form)
│   │   ├── _table.html          # Just the table (for HTMX filter swaps)
│   │   ├── _row.html            # One table row (for HTMX row updates)
│   │   └── _detail.html         # Side panel content for one job
│   ├── applications/
│   │   ├── index.html           # Full applications page
│   │   ├── _table.html
│   │   ├── _row.html
│   │   ├── _detail.html         # Side panel content for one application (edit form)
│   │   └── _new_form.html       # "Log application" form
│   └── config/
│       ├── index.html           # Landing page with tabs/links to the two editors
│       ├── queries.html         # queries.yaml editor
│       └── career_profile.html  # career_profile.md editor
├── static/
│   ├── app.css                  # Small amount of custom CSS beyond Tailwind
│   └── app.js                   # Small amount of custom JS beyond Alpine/HTMX
└── README.md                    # How to run, stack rationale, contribution rules
```

**Template naming convention:** templates whose name starts with `_` (e.g. `_table.html`, `_row.html`) are partials — they return a fragment and are intended to be swapped into a parent page via HTMX. Full pages extend `base.html`. This convention is explicit so contributors can tell at a glance whether a template is a page or a fragment.

---

## Pages and Request Flows

### Page 1: Jobs browser (`/jobs`)

**Layout:** Filter sidebar on the left, table in the middle, optional detail side panel on the right that slides in when a row is clicked.

**Filters (from your requirements):**

- Tier2 score minimum (numeric input, default 0)
- Tier3 score minimum (numeric input, default 0; filters to only jobs with tier3 scores when > 0)
- Seniority (multi-select: junior, mid, senior, lead, staff, principal)
- Attendance (multi-select: remote, hybrid, onsite)
- Location (text, trigram-matched against `jobs.location`)
- Title (text, trigram-matched against `jobs.title`)
- Company (text, trigram-matched against `jobs.company_name`)
- Description (text, ILIKE match against `jobs.description`, `qualifications`, `responsibilities`)
- Job listing status (multi-select: active, expired, closed) — defaults to `active` only
- Application state (multi-select: none, applied, rejected, interviewing, offer, withdrawn) — defaults to all

**Sort:** A single dropdown. Default: `tier2_score DESC`. Alternatives: `tier3_score DESC`, `date_listed DESC`, `salary_max DESC`.

**Pagination:** Server-side, 50 rows per page. Page number in URL query param.

**Request flow (initial load):**

1. `GET /jobs?<filters>` — route parses query string into a `JobFilter` dataclass, calls `services.jobs.list_jobs(filter, page)`, renders `jobs/index.html` with the resulting rows.

**Request flow (filter change):**

1. Filter form has `hx-get="/jobs"` with `hx-trigger="change"` (or `"keyup changed delay:400ms"` for text inputs), `hx-target="#jobs-table"`, `hx-swap="outerHTML"`, and `hx-push-url="true"` so the URL updates.
2. Server detects the `HX-Request` header, renders only `jobs/_table.html` instead of the full page.
3. The table swaps in place. URL updates so the current filter state is shareable and survives browser refresh.

**Request flow (row click → detail panel):**

1. Each row has `hx-get="/jobs/{id}/detail"`, `hx-target="#detail-panel"`, `hx-swap="innerHTML"`.
2. Server renders `jobs/_detail.html` with the full job record.
3. Alpine.js handles the slide-in animation and the "close panel" button. Panel state (open/closed, current job) is entirely client-side.

**The detail panel contains:**

- All displayed columns plus description, qualifications, responsibilities.
- Tier2 score and explanation.
- Tier3 score and explanation (if present; otherwise a note that deep analysis has not been run).
- Normalized skills and frameworks (joined from `job_skills` and `job_frameworks`).
- Raw `url` as a clickable link that opens in a new tab.
- **Job status control:** a dropdown/select that immediately updates `jobs.status` on change (`PATCH /jobs/{id}/status`). Only transitions allowed: `active ↔ closed`, `active → expired` (manual). `expired` is also assigned automatically by the pipeline; the UI lets you set it manually too.
- **"Log application" button:** visible only if `jobs.status = 'active'` and `jobs.application_id IS NULL`. Opens the new-application form (see Page 4).
- **"View application" link:** visible only if `jobs.application_id IS NOT NULL`. Navigates to `/applications/{application_id}`.

### Page 2: Applications browser (`/applications`)

**Layout:** Mirrors the jobs page. Filter sidebar, table, side panel on row click.

**Table columns:**

- Application state (submitted, rejected, interviewing, offer, withdrawn) — see schema change below
- Date applied
- Job title (from joined job)
- Company (from joined job)
- Interviews completed
- Reached human (yes/no)
- Offer (yes/no)

**Filters:**

- State (multi-select)
- Date applied range (from/to)
- Company (text, trigram-matched)
- Assistance level (multi-select)
- Has offer (yes/no/any)

**Sort:** Default `date_applied DESC`. Alternatives: `state`, `company_name`.

**Request flow:** Identical to the jobs page.

**Detail panel:** Renders an editable form bound to the application. Each field updates via `PATCH /applications/{id}` on blur or change — HTMX handles the update, server returns the updated row fragment, the row in the table behind the panel also refreshes (via `hx-swap-oob="true"` — HTMX out-of-band swap, which updates an element outside the primary target).

**Form fields on the detail panel:**

- Date applied (date input)
- State (select)
- Assistance level (select: ai, assisted, human)
- Resume (select dropdown populated from files in `data/resumes/`)
- Cover letter (textarea, free text)
- Cold calls (integer input)
- Reached human (checkbox → stored as 0/1)
- Interviews (integer input)
- Offer (checkbox → stored as 0/1)

### Page 3: Log application flow (`POST /applications`)

**Entry point:** "Log application" button on the job detail panel. Button has `hx-get="/applications/new?job_id={id}"`, `hx-target="#detail-panel"`, which replaces the job detail panel contents with the new-application form.

**Form (`applications/_new_form.html`):** Same fields as the detail-panel edit form, pre-populated with sensible defaults:

- Date applied: today
- State: `submitted`
- Assistance level: empty (required on submit)
- Resume: empty (required on submit — must be a file in `data/resumes/`)
- Cover letter: empty (optional)
- Counters: 0
- Offer: false

**Submit:** `hx-post="/applications"` with the job_id as a hidden field.

**Server transaction (in `services.applications.log_application`):**

```python
# Pseudocode; actual implementation calls db.operations functions.
with transaction:
    application_id = db.operations.create_application(
        job_id=job_id,
        date_applied=...,
        state='submitted',
        assistance_level=...,
        resume=<filename from dropdown>,
        cover_letter=...,
        ...
    )
    db.operations.update_job_status(job_id, 'active')  # listing itself stays active
    # create_application already updates jobs.application_id (existing behavior)
```

The existing `create_application` function in `db/operations.py` already writes the back-pointer on `jobs.application_id` in the same transaction (confirmed in the current codebase). We are adding a new **`applications.state` column** rather than overloading `jobs.status`, so no change to `jobs.status` is needed at log time.

**Response:** Server returns an HTMX redirect (`HX-Redirect: /applications/{application_id}`) so the browser navigates to the applications page with the new application's detail panel open.

**Why this flow over "create blank row, then redirect":**

- No orphan blank applications from abandoned forms.
- `jobs` table never shows "applied" for a job that wasn't actually applied to yet.
- No ambiguous draft/final state on the application row.
- Standard form-submit pattern, which is what users expect.

### Page 4: Config editors (`/config/queries`, `/config/profile`)

Two simple pages, one per file.

**`queries.yaml` editor:**

- Parse the YAML on load, render a table of queries with `name`, `q`, `location`, `gl`, `hl`, `lrad` as editable fields.
- "Add query" button adds a blank row (client-side via Alpine).
- "Remove" button on each row.
- "Save" button validates the resulting YAML (re-serializing and round-tripping through `yaml.safe_load` to confirm validity), then atomically writes it: write to `queries.yaml.tmp`, then `os.replace()` to `queries.yaml`. This prevents a half-written file if the process dies mid-write.
- Save failure (validation error) shows an inline error and does not overwrite the file.

**`career_profile.md` editor:**

- Single large textarea with monospace font.
- "Save" button writes atomically (same tmp + replace pattern).
- No validation beyond "file is writable."

**Why edit these in the UI instead of telling users to use `vim`:** your mom cannot be assumed to edit YAML in a text editor. The `queries.yaml` form reduces the risk of syntax errors. The `career_profile.md` editor is less critical (a textarea is barely better than a text editor) but provides a unified surface — one app, all the levers.

---

## Schema Changes

The only schema change required is splitting application progress out of `jobs.status`.

### Rationale

Your proposed `jobs.status` enum was `["active", "applied", "expired", "closed", "rejected", "interviewing", "offer"]`. This conflates two orthogonal concepts:

- **Listing state** (property of the job posting itself): is the job still accepting applications? Values: `active`, `expired`, `closed`.
- **Application progress** (property of _your_ application to it): how far along are you? Values: `submitted`, `rejected`, `interviewing`, `offer`, `withdrawn`.

Collapsing them means a job you're interviewing for has status `interviewing` and you no longer know if the posting is still live. If the posting expires while you're mid-interview, you lose either state (overwrite) or accuracy (leave stale). Storing them separately solves this at the cost of one JOIN in table queries, which is negligible.

### Migration

Add a nullable `state` column to `applications`:

```sql
ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS state TEXT
    CHECK (state IN ('submitted', 'rejected', 'interviewing', 'offer', 'withdrawn'));

-- Backfill: any existing application is assumed 'submitted' unless offer=1
UPDATE applications SET state = 'offer' WHERE offer = 1 AND state IS NULL;
UPDATE applications SET state = 'submitted' WHERE state IS NULL;
```

Keep the boolean `offer` column on `applications` for now — it is pipeline-visible and removing it would be a breaking change. The UI treats `state = 'offer'` as the source of truth for display; `offer = 1` is maintained in lockstep by the service layer for backwards compatibility.

Tighten `jobs.status`:

```sql
-- Drop extraction_failed rows from the user-visible view (they stay in the table
-- for pipeline reprocessing). Add 'closed' to the allowed values.
-- No CHECK constraint exists on jobs.status today; we add one now:
ALTER TABLE jobs
    ADD CONSTRAINT jobs_status_check
    CHECK (status IN ('active', 'expired', 'closed', 'duplicate', 'extraction_failed'));
```

`duplicate` and `extraction_failed` stay in the DB (the pipeline uses them) but are hidden from the default dashboard view.

### Why not do a bigger migration

It's tempting to also normalize `applications.state` into an enum table, add a `status_history` audit log, rename `offer` to `has_offer`, etc. Don't. The schema as it stands is adequate for v1. Each of those changes has independent merit but also independent cost, and bundling them increases the risk of regression. Ship the minimum.

---

## New Functions in `db/operations.py`

The dashboard adds new database access functions. Every new function follows existing conventions: it uses the pooled connection via `with connection() as conn`, uses `RealDictCursor` for reads, returns dicts, and raises on unexpected errors.

**Reads:**

- `list_jobs(filter: JobFilter, sort: str, page: int, page_size: int) -> tuple[list[dict], int]` — returns (rows, total_count). `JobFilter` is a dataclass defined in `app/services/jobs.py`; the function accepts it but the signature above uses primitives to keep `db/operations.py` free of app-layer imports.
- `get_job_detail(job_id: int) -> dict | None` — full job record joined with skills, frameworks, and application (if any).
- `list_applications(filter: ApplicationFilter, sort: str, page: int, page_size: int) -> tuple[list[dict], int]` — joined with basic job info (title, company).
- `get_application_detail(application_id: int) -> dict | None` — application joined with full job record.

**Writes:**

- `update_job_status(job_id: int, status: str) -> None` — validates status is in the allowed set, updates `jobs.status`.
- `update_application(application_id: int, **fields) -> None` — _already exists_; we extend the `_UPDATABLE` set to include `state`.
- Existing `create_application` already handles the atomic insert + back-pointer. No change required.

**What we do not add:** no `bulk_update_jobs`, no `delete_job`, no `delete_application`. The v1 dashboard does not delete rows. If you want a job gone, you set its status to `closed`; if you want an application gone, you set its state to `withdrawn`. Deletion is destructive and rarely the right answer.

---

## Resume File Handling

Resumes live as files in `data/resumes/`. The `applications.resume` column stores only the filename (e.g. `"resume_ds_2026.md"`), never a full path.

**Why this pattern:**

- Consistent with existing `data/career_profile.md` convention.
- No browser file-picker required (browsers deliberately don't expose filesystem paths to web apps — this is a security guarantee, not a limitation to work around).
- No file upload → no storage concerns, no deduplication, no MIME validation.
- The user manages their own resume files with their own tools. The dashboard just points at them.

**Dashboard integration:**

- On the application form, the `resume` field is a `<select>` populated from `os.listdir(settings.RESUMES_DIR)`, filtered to files (not subdirectories) and sorted alphabetically.
- If no files exist, the form displays a message pointing to `data/resumes/` with instructions to add a file there.
- `RESUMES_DIR` is a new entry in `config/settings.py`, defaulting to `<project_root>/data/resumes/`, overridable via env var.

**Cover letters:** stored as text in the `applications.cover_letter` column directly. A cover letter is per-application, short enough to fit comfortably in a TEXT column, and pointless to store as a file. This diverges from the resume pattern deliberately — resumes are reusable templates; cover letters are disposable one-offs.

---

## Freshness and Read-Only Pipeline Visibility

The dashboard shows pipeline freshness but never triggers pipeline work. The top-of-page header includes a small badge:

- Latest `date_ingested` across all jobs (→ "Last ingestion: 3 hours ago").
- Count of jobs ingested in the last 24 hours.
- Count of active jobs total.
- Count of active jobs with `tier3_score IS NULL` (→ "23 jobs awaiting deep analysis").

These are four lightweight queries cached in-memory for 60 seconds per process. No polling, no websockets, no background tasks. If the user wants fresh data, they refresh the page.

**Why not a "run ingestion" button:**

- Backfill takes hours. `daily_run.py` takes minutes. `score_top_jobs.py` makes paid LLM calls. None of these are things you want to trigger by accident from a web UI.
- Launching subprocesses from a web app requires status tracking, log capture, cancellation, and concurrency control — an entire background-jobs subsystem for a feature the CLI already handles better.
- The CLI is more honest about what's happening: you see the logs, you see when it finishes, you can Ctrl+C it.

If in-UI script launching becomes genuinely necessary later, the right design is a new `pipeline_runs` table (columns: `run_id`, `script_name`, `status`, `started_at`, `finished_at`, `log_path`), a route that spawns the script via `subprocess.Popen` and inserts a row, and a polling UI. This is documented here so the path exists but is not built.

---

## Deployment Modes

The dashboard supports two deployment modes, controlled by a single env var `DASHBOARD_MODE`:

- **`personal` (default):** single-tenant, no auth, binds to `127.0.0.1` by default. All routes enabled. This is the mode used by you, your family, and any cloner running locally.
- **`public` (future):** only routes under `/public/*` are enabled; the personal routes return 404. Designed to be deployed behind a reverse proxy with HTTPS. The public dataviz site, when it exists, lives under `/public/*` and uses a separate service module (`app/services/public.py`) that touches only aggregate, zero-tenant queries.

**Why this seam matters:** the personal dashboard and the public site have fundamentally different data access patterns (per-user vs aggregate), different auth requirements (none vs possibly some), and different performance profiles (low-traffic vs potentially public). Forcing them through the same service layer creates accidental coupling. Keeping them as sibling router groups in the same FastAPI app is the cheapest way to share the DB connection and the schema while keeping the code paths independent.

**The rule:** personal routes and public routes never share service code. They can share `db/operations.py` functions (especially aggregate taxonomy reads), but not services. This is documented in `app/README.md` and enforced by directory convention (`app/services/` for personal, future `app/services/public/` for public).

For v1, only `personal` mode exists. The `public` mode is a feature flag that currently is a no-op — we're just making sure the routes are organized in a way that doesn't preclude adding it later.

---

## Setup Sequence (Additions)

Existing setup (from `ARCHITECTURE.md`) is unchanged. The dashboard adds:

8. `pip install -e .[dashboard]` — installs FastAPI, uvicorn, jinja2, python-multipart. These are new entries in `pyproject.toml` under an optional `dashboard` extra so the pipeline can be installed without them.
9. `mkdir -p data/resumes && cp <your resume files> data/resumes/`
10. `uvicorn app.main:app --reload` — starts the dashboard on `http://127.0.0.1:8000`.

The dashboard assumes the database already has schema and (optionally) data. It does not create tables, does not seed, and does not fail if tables are empty — empty states render gracefully with "no jobs yet, run `scripts/backfill.py`" hints.

---

## Testing Strategy

Two test layers, matching the existing project conventions:

**Unit tests (`tests/test_app_services.py`):** Test services with a mocked `db.operations` module. Verify that `log_application` calls `create_application` with the right args, that filter-to-query translation produces the expected SQL params, that YAML validation rejects malformed input.

**Integration tests (`tests/test_app_routes.py`):** Spin up a `TestClient` against `app.main.app`, use a test database (via the existing Postgres in CI, or a throwaway schema), hit each route, assert status codes and that the returned HTML contains expected text. HTMX responses are just HTML strings — no special test tooling required.

**What we do not test:** visual regression, end-to-end browser tests (Playwright/Selenium). The app is small enough, and its interactions mechanical enough, that the cost of these tests exceeds the cost of catching bugs by using the app.

---

## Open Decisions / Future Work

1. **Deep analysis trigger in UI.** If the workflow "select interesting jobs from tier2_score, then run tier3 deep analysis on them" becomes common, a checkbox-and-button flow in the jobs table might be warranted. This is a script-launch feature in disguise and follows the path described in "Freshness" above.

2. **Application note-taking.** Interview notes, recruiter conversations, deadlines — none of these fit the current schema. If application tracking becomes the primary workflow (as the job search progresses past early filtering), an `application_notes` table with a timeline-style UI becomes a natural addition.

3. **Public dataviz site.** When built, lives under `/public/*` in the same FastAPI app. Read-only aggregate queries over `job_skills`, `job_frameworks`, and `jobs` aggregated by date. No user accounts in v1 of the public site — the interesting dataviz is skill demand and salary distributions, which need no per-user state.

4. **Search over application cover letters.** If you apply to enough jobs to need this, a full-text search index over `applications.cover_letter` (`pg_trgm` or `tsvector`) plus a search box on the applications page is a small addition.

5. **Light/dark mode.** Tailwind makes this trivial, but it is not a v1 requirement.

6. **Keyboard shortcuts.** A power-user win if the dashboard becomes heavily used. `j`/`k` to navigate rows, `Enter` to open detail, `Esc` to close panel — all doable with a small amount of Alpine.js listening at the body level. Defer to post-v1.
