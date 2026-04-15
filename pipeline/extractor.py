"""
LLM metadata extractor.

Sends raw job data to a cheap LLM via OpenRouter and returns a structured
extraction result.  The system prompt includes the full canonical skill and
framework taxonomy as reference material so the model uses consistent names.

Extraction schema:
  employment_type       — enum or null
  attendance            — enum or null
  seniority             — enum or null
  experience_years_min  — int or null
  experience_years_max  — int or null
  salary_min            — int or null
  salary_max            — int or null
  salary_currency       — string or null
  salary_period         — enum or null
  skills                — list[str]
  frameworks            — list[str]

Error handling:
  First failure  → retry once with the same input
  Second failure → return None (caller stores job as extraction_failed)
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache

from config.settings import EXTRACTION_MODEL, SKILLS_MD_PATH, FRAMEWORKS_MD_PATH
from llm.client import complete_json

logger = logging.getLogger(__name__)

# Enums the LLM is allowed to return (null is always allowed)
_EMPLOYMENT_TYPES = {"full-time", "part-time", "contract", "internship"}
_ATTENDANCES      = {"remote", "hybrid", "onsite"}
_SENIORITIES      = {"junior", "mid", "senior", "lead", "staff", "principal"}
_SALARY_PERIODS   = {"yearly", "hourly", "monthly", "contract"}

_EXTRACTION_SCHEMA = """
{
  "employment_type":      "full-time" | "part-time" | "contract" | "internship" | null,
  "attendance":           "remote" | "hybrid" | "onsite" | null,
  "seniority":            "junior" | "mid" | "senior" | "lead" | "staff" | "principal" | null,
  "experience_years_min": integer | null,
  "experience_years_max": integer | null,
  "salary_min":           integer | null,
  "salary_max":           integer | null,
  "salary_currency":      string | null,
  "salary_period":        "yearly" | "hourly" | "monthly" | "contract" | null,
  "skills":               [string, ...],
  "frameworks":           [string, ...]
}
"""

_FEW_SHOT = [
    {
        "title": "Senior Data Scientist",
        "description": (
            "We're looking for a Senior Data Scientist to join our ML platform team. "
            "You'll build and deploy predictive models at scale. Requirements: 5+ years "
            "Python, deep expertise in TensorFlow or PyTorch, AWS experience, strong SQL skills. "
            "Full-time remote position. Salary: $160,000–$200,000/year."
        ),
        "qualifications": "5+ years Python, TensorFlow or PyTorch, AWS, SQL proficiency.",
        "responsibilities": "Build predictive models, deploy to production, collaborate with data engineers.",
    },
    {
        "result": {
            "employment_type": "full-time",
            "attendance": "remote",
            "seniority": "senior",
            "experience_years_min": 5,
            "experience_years_max": None,
            "salary_min": 160000,
            "salary_max": 200000,
            "salary_currency": "USD",
            "salary_period": "yearly",
            "skills": ["Predictive Modeling", "Deep Learning", "Model Deployment", "SQL/NoSQL Management"],
            "frameworks": ["Python", "TensorFlow", "PyTorch", "AWS", "SQL"],
        }
    },
    {
        "title": "Data Engineer (Contract)",
        "description": (
            "Seeking a Data Engineer to design and maintain ETL pipelines feeding our Snowflake "
            "data warehouse. Must have Apache Airflow and Spark experience. 3–5 years required. "
            "Hybrid in Seattle, WA. $75–95/hr."
        ),
        "qualifications": "3–5 years ETL, Apache Airflow, Apache Spark, Snowflake.",
        "responsibilities": "Build ETL pipelines, maintain data warehouse, optimise queries.",
    },
    {
        "result": {
            "employment_type": "contract",
            "attendance": "hybrid",
            "seniority": "mid",
            "experience_years_min": 3,
            "experience_years_max": 5,
            "salary_min": 75,
            "salary_max": 95,
            "salary_currency": "USD",
            "salary_period": "hourly",
            "skills": ["ETL/ELT Processes", "Data Warehousing", "Data Orchestration", "Query Optimization"],
            "frameworks": ["Apache Airflow", "Apache Spark", "Snowflake", "Python"],
        }
    },
]


@lru_cache(maxsize=1)
def _build_system_prompt() -> str:
    skills_text     = SKILLS_MD_PATH.read_text(encoding="utf-8")
    frameworks_text = FRAMEWORKS_MD_PATH.read_text(encoding="utf-8")
    return f"""You are a structured data extractor for job listings.

Your task: extract metadata from a job description and return it as JSON.

Output schema (use null for any field not clearly stated in the listing — never guess):
{_EXTRACTION_SCHEMA}

Rules:
- skills and frameworks must use canonical names from the taxonomy below where possible.
  If a skill or tool is not in the taxonomy, include it as-is — the normaliser handles it.
- Return empty lists [] for skills/frameworks if none are mentioned, never null.
- salary_min/max must be integers (no decimals, no currency symbols).
- For salary_period: "yearly" for annual, "hourly" for hourly, "monthly" for monthly.
- Never infer seniority from job title alone if the description contradicts it.
- Respond ONLY with valid JSON matching the schema.

--- SKILL TAXONOMY (use these canonical names) ---
{skills_text}

--- FRAMEWORK & TOOLING TAXONOMY (use these canonical names) ---
{frameworks_text}"""


def _format_job_input(job: dict) -> str:
    title            = job.get("title", "")
    company          = job.get("company_name", "")
    location         = job.get("location", "")
    description      = job.get("description", "")
    qualifications   = job.get("qualifications", "")
    responsibilities = job.get("responsibilities", "")

    parts = [f"Title: {title}", f"Company: {company}", f"Location: {location}"]
    if description:
        parts.append(f"Description:\n{description}")
    if qualifications:
        parts.append(f"Qualifications:\n{qualifications}")
    if responsibilities:
        parts.append(f"Responsibilities:\n{responsibilities}")
    return "\n\n".join(parts)


def _build_messages(job_text: str) -> list[dict]:
    messages = [{"role": "system", "content": _build_system_prompt()}]

    # Inject few-shot examples as alternating user/assistant turns
    for i in range(0, len(_FEW_SHOT), 2):
        ex_in  = _FEW_SHOT[i]
        ex_out = _FEW_SHOT[i + 1]
        ex_text = (
            f"Title: {ex_in['title']}\n\n"
            f"Description:\n{ex_in['description']}\n\n"
            f"Qualifications:\n{ex_in['qualifications']}\n\n"
            f"Responsibilities:\n{ex_in['responsibilities']}"
        )
        messages.append({"role": "user",      "content": ex_text})
        messages.append({"role": "assistant", "content": json.dumps(ex_out["result"])})

    messages.append({"role": "user", "content": job_text})
    return messages


def _validate_and_clean(raw: dict) -> dict:
    """Coerce enum fields to known values (or null) and ensure list fields exist."""
    result = dict(raw)
    if result.get("employment_type") not in _EMPLOYMENT_TYPES:
        result["employment_type"] = None
    if result.get("attendance") not in _ATTENDANCES:
        result["attendance"] = None
    if result.get("seniority") not in _SENIORITIES:
        result["seniority"] = None
    if result.get("salary_period") not in _SALARY_PERIODS:
        result["salary_period"] = None
    result["skills"]     = [s for s in (result.get("skills") or []) if isinstance(s, str) and s.strip()]
    result["frameworks"] = [f for f in (result.get("frameworks") or []) if isinstance(f, str) and f.strip()]
    return result


class Extractor:
    def __init__(self, model: str = EXTRACTION_MODEL) -> None:
        self._model = model

    def extract(self, job: dict) -> dict | None:
        """
        Extract structured metadata from a raw job dict.
        Returns the extraction result dict, or None on repeated failure.
        """
        job_text = _format_job_input(job)
        messages = _build_messages(job_text)

        for attempt in range(2):
            try:
                raw = complete_json(self._model, messages, temperature=0.0, max_tokens=1024)
                return _validate_and_clean(raw)
            except Exception as exc:
                logger.warning(
                    f"Extraction attempt {attempt + 1} failed for "
                    f"{job.get('title')!r} @ {job.get('company_name')!r}: {exc}"
                )

        logger.error(
            f"Extraction failed after 2 attempts: "
            f"{job.get('title')!r} @ {job.get('company_name')!r}"
        )
        return None
