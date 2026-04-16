# Google Jobs API — SerpApi

**API Endpoint:** `https://serpapi.com/search?engine=google_jobs`

Our Google Jobs API allows you to scrape results from the Google Jobs search page. Head to the [playground](https://serpapi.com/playground?engine=google_jobs) for a live and interactive demo.

- **API Uptime:** 99.924%
- **Avg. Response Time:** 2.96s
- [Release Notes](https://serpapi.com/google-jobs-api/release-notes) | [API Status](https://serpapi.com/status/google_jobs)

---

## API Parameters

### Search Query

| Parameter | Required     | Description                   |
| --------- | ------------ | ----------------------------- |
| `q`       | **Required** | The query you want to search. |

---

### Geographic Location

| Parameter  | Required | Description                                                                                                                                                                                                                                                                                                                                                                            |
| ---------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `location` | Optional | Defines where you want the search to originate. If several locations match, the most popular one is selected. See the [Locations API](https://serpapi.com/locations-api) for precise control. `location` and `uule` cannot be used together. It is recommended to specify `location` at the city level to simulate a real user's search. If omitted, the proxy's location may be used. |
| `uule`     | Optional | Google encoded location. `uule` and `location` cannot be used together.                                                                                                                                                                                                                                                                                                                |

---

### Localization

| Parameter       | Required | Description                                                                                                                                                                                |
| --------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `google_domain` | Optional | Google domain to use. Defaults to `google.com`. See [Google domains page](https://serpapi.com/google-domains) for the full list.                                                           |
| `gl`            | Optional | Two-letter country code for the Google search (e.g., `us` for United States, `uk` for United Kingdom, `fr` for France). See [Google countries page](https://serpapi.com/google-countries). |
| `hl`            | Optional | Two-letter language code for the Google Jobs search (e.g., `en` for English, `es` for Spanish, `fr` for French). See [Google languages page](https://serpapi.com/google-languages).        |

---

### Pagination

| Parameter         | Required | Description                                                                                                                                                                                                                                                                                         |
| ----------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `next_page_token` | Optional | Defines the next page token for retrieving the next page of results. Up to **10 results** are returned per page. The token is found in the JSON response at `serpapi_pagination -> next_page_token`. **Note:** Usage of the `start` parameter (results offset) has been **discontinued** by Google. |

---

### Advanced Google Jobs Parameters

| Parameter | Required | Description                                                                                                                                                                                                                                                          |
| --------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `chips`   | Optional | Defines additional query conditions. Values are extracted from chip elements at the top of the job search page (e.g., `city:Owg_06VPwoli_nfhBo8LyA==` for New York). **⚠️ Deprecated by Google.**                                                                    |
| `lrad`    | Optional | Defines search radius in kilometers. Does not strictly limit the radius.                                                                                                                                                                                             |
| `ltype`   | Optional | Filters results by work from home (set to `1`). **⚠️ Deprecated by Google.**                                                                                                                                                                                         |
| `uds`     | Optional | Enables search filtering using a string provided by Google. `uds` values are found under the `filters` section of the response, alongside `q` and `serpapi_link` values for each filter. Multiple filters from the same category can be combined in a single search. |

---

### SerpApi Parameters

| Parameter    | Required     | Description                                                                                                                                                                                                                                                                                                            |
| ------------ | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `engine`     | **Required** | Set to `google_jobs` to use the Google Jobs API engine.                                                                                                                                                                                                                                                                |
| `api_key`    | **Required** | Your SerpApi private key.                                                                                                                                                                                                                                                                                              |
| `no_cache`   | Optional     | Forces SerpApi to fetch fresh results even if a cached version exists. Cache expires after 1 hour. Cached searches are free and do not count against your monthly quota. Set to `false` (default) to allow cached results or `true` to bypass. Cannot be used with `async`.                                            |
| `async`      | Optional     | Defines how your search is submitted. `false` (default) opens an HTTP connection until results are ready. `true` submits the search and allows retrieval later via the [Search Archive API](https://serpapi.com/search-archive-api). Cannot be used with `no_cache`. Should not be used with Ludicrous Speed accounts. |
| `zero_trace` | Optional     | **Enterprise only.** Enables [ZeroTrace mode](https://serpapi.com/zero-trace). Set to `false` (default) or `true`. When enabled, search parameters, search files, and metadata are not stored on SerpApi servers.                                                                                                      |
| `output`     | Optional     | Defines the output format. `json` (default) returns a structured JSON response. `html` returns the raw HTML retrieved from Google.                                                                                                                                                                                     |

---

## JSON Response Structure

### `jobs_results`

Each result in `jobs_results` contains:

| Field                 | Description                                                                                                                      |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `title`               | Job title                                                                                                                        |
| `company_name`        | Name of the hiring company                                                                                                       |
| `location`            | Job location                                                                                                                     |
| `via`                 | Platform the job was listed on                                                                                                   |
| `share_link`          | Google link to the job detail                                                                                                    |
| `thumbnail`           | Company logo or image URL                                                                                                        |
| `extensions`          | Array of job tags (e.g., "Full-time", "Work from home", "Health insurance")                                                      |
| `detected_extensions` | Structured boolean/string version of extensions (e.g., `health_insurance`, `paid_time_off`, `schedule_type`, `work_from_home`)   |
| `description`         | Full job description text                                                                                                        |
| `job_highlights`      | Structured highlights with titles such as "Qualifications", "Responsibilities", and "Benefits", each containing an `items` array |
| `apply_options`       | Array of apply links with `title` and `link`                                                                                     |
| `job_id`              | Unique job identifier (base64 encoded)                                                                                           |

### `filters`

The `filters` array contains available search filters. Each filter includes:

| Field          | Description                                                                                               |
| -------------- | --------------------------------------------------------------------------------------------------------- |
| `name`         | Filter name (e.g., "Remote", "Date posted")                                                               |
| `link`         | Google search URL for that filter                                                                         |
| `serpapi_link` | SerpApi search URL for that filter                                                                        |
| `uds`          | Filter token to pass as the `uds` parameter                                                               |
| `q`            | Corresponding search query string                                                                         |
| `options`      | (For multi-option filters) Array of sub-options, each with `name`, `link`, `serpapi_link`, `uds`, and `q` |

### `search_metadata`

| Field              | Description                                        |
| ------------------ | -------------------------------------------------- |
| `id`               | Unique search ID in SerpApi                        |
| `status`           | Search status: `Processing` → `Success` or `Error` |
| `json_endpoint`    | URL to retrieve the cached JSON result             |
| `google_jobs_url`  | The actual Google Jobs URL that was searched       |
| `raw_html_file`    | URL to the raw HTML file                           |
| `created_at`       | Timestamp when search was created                  |
| `processed_at`     | Timestamp when search was processed                |
| `total_time_taken` | Total time in seconds                              |

> If a search has failed, an `error` field will contain an error message.

### HTML Results

HTML output is useful for debugging JSON results or for accessing features not yet supported by SerpApi. It returns the raw HTML from Google. Note: full HTML responses are not available when pagination is in use. `search_metadata.prettify_html_file` contains a link to the prettified version, viewable in the playground.

---

## Usage Examples

### Example 1 — Search with `q: barista new york`

**Request URL:**

```
https://serpapi.com/search.json?engine=google_jobs&q=barista+new+york
```

**Example JSON Response (truncated):**

```json
{
  "search_metadata": {
    "id": "66b21cfa10a07127b90afedd",
    "status": "Success",
    "google_jobs_url": "https://www.google.com/search?q=barista+new+york&udm=8",
    "total_time_taken": 0.84
  },
  "search_parameters": {
    "q": "barista new york",
    "engine": "google_jobs",
    "google_domain": "google.com"
  },
  "filters": [
    {
      "name": "No degree",
      "serpapi_link": "https://serpapi.com/search.json?engine=google_jobs&q=barista+new+york+no+degree&...",
      "uds": "ADvngMjvPWL3...",
      "q": "barista new york no degree"
    },
    {
      "name": "Date posted",
      "options": [
        {
          "name": "Yesterday",
          "q": "barista new york since yesterday",
          "uds": "..."
        },
        {
          "name": "Last 3 days",
          "q": "barista new york in the last 3 days",
          "uds": "..."
        },
        {
          "name": "Last week",
          "q": "barista new york in the last week",
          "uds": "..."
        }
      ]
    }
  ],
  "jobs_results": [
    {
      "title": "Barista - Lyon Village",
      "company_name": "Peet's Coffee Inc.",
      "location": "Anywhere",
      "via": "Workday",
      "extensions": ["23 days ago", "Work from home", "Part-time"],
      "detected_extensions": {
        "paid_time_off": true,
        "dental_coverage": true,
        "posted_at": "23 days ago",
        "schedule_type": "Part-time",
        "work_from_home": true
      },
      "description": "For over 50 years, our goal has been to offer the best coffees & teas...",
      "apply_options": [
        {
          "title": "Workday",
          "link": "https://peets.wd12.myworkdayjobs.com/..."
        }
      ],
      "job_id": "eyJqb2JfdGl0bGUiOiJCYXJpc3RhIC0gTHlvbiBW..."
    }
  ]
}
```

---

### Example 2 — Filter by Work From Home with `ltype=1`

Set `ltype=1` to filter results by **Working From Home**.

**Request URL:**

```
https://serpapi.com/search.json?engine=google_jobs&q=Java+Developer&ltype=1
```

**Example `jobs_results` entry:**

```json
{
  "title": "Java Full stack Developer",
  "company_name": "Unique System Skills LLC",
  "location": "Anywhere",
  "via": "Dice",
  "extensions": ["22 hours ago", "Work from home", "Contractor"],
  "detected_extensions": {
    "posted_at": "22 hours ago",
    "work_from_home": true,
    "schedule_type": "Contractor",
    "qualifications": "No degree mentioned"
  },
  "description": "We have an opening for Java Full stack Developer...",
  "apply_options": [
    { "title": "Dice", "link": "https://www.dice.com/job-detail/..." }
  ],
  "job_id": "eyJqb2JfdGl0bGUiOiJKYXZhIEZ1bGwgc3RhY2sgRGV2..."
}
```

---

### Example 3 — Using Multiple Filters with `uds`

Multiple filters from the same category can be combined in a single search using the `uds` parameter. Retrieve valid `uds` values from the `filters` array in a previous response.

**Request URL:**

```
https://serpapi.com/search.json?engine=google_jobs&q=barista&uds=ADvngMjIlLeH...
```

**Example `jobs_results` entry:**

```json
{
  "title": "Barista Market Developer Lead - NYC",
  "company_name": "Oatly AB",
  "location": "Anywhere",
  "via": "Oatly AB",
  "extensions": ["Work from home", "Full-time", "Health insurance"],
  "detected_extensions": {
    "health_insurance": true,
    "paid_time_off": true,
    "schedule_type": "Full-time",
    "work_from_home": true
  },
  "job_highlights": [
    {
      "title": "Qualifications",
      "items": [
        "Must be located in New York, NY and have lived there for 3+ years",
        "..."
      ]
    },
    {
      "title": "Benefits",
      "items": [
        "Flexible vacation, great benefits, start-up vibe with established history",
        "..."
      ]
    },
    {
      "title": "Responsibilities",
      "items": [
        "Create, lead, and execute development strategy for Oatly Barista Edition within NYC",
        "..."
      ]
    }
  ],
  "apply_options": [
    { "title": "Oatly AB", "link": "https://careers.oatly.com/jobs/..." },
    { "title": "Jobgether", "link": "https://jobgether.com/offer/..." }
  ],
  "job_id": "eyJqb2JfdGl0bGUiOiJCYXJpc3RhIE1hcmtldCBEZXZlbG9wZXIgTGVhZCAtIE5ZQyIs..."
}
```

---

## Related APIs

- [Google Jobs Listing API](https://serpapi.com/google-jobs-listing-api) — for paginated job listing results
- [Locations API](https://serpapi.com/locations-api) — for precise location targeting
- [Search Archive API](https://serpapi.com/search-archive-api) — retrieve async search results
- [Account API](https://serpapi.com/account-api) — check your API usage and key details

---

_© 2016–2026 SerpApi, LLC. Documentation sourced from [serpapi.com/google-jobs-api](https://serpapi.com/google-jobs-api)._
