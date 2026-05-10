"""
Re-export shim — db/operations.py has been split into focused modules.

Import directly from the relevant module instead:
  from db.jobs         import insert_job, list_jobs, get_job_detail, ...
  from db.taxonomy     import merge_skill, promote_skill, get_all_canonical_skills, ...
  from db.applications import create_application, list_applications, ...
"""
from db.jobs import (
    expire_old_jobs,
    get_active_job_count,
    get_active_t3_scored_jobs,
    get_freshness_stats,
    get_job_detail,
    get_jobs_by_ids,
    get_jobs_for_reprocessing,
    get_top_scored_jobs,
    insert_job,
    list_jobs,
    mark_job_failed,
    update_job_status,
    update_tier2_scores,
    update_tier3_scores,
)
from db.taxonomy import (
    discard_framework,
    discard_skill,
    get_all_canonical_frameworks,
    get_all_canonical_skills,
    get_candidate_frameworks,
    get_candidate_frameworks_above_threshold,
    get_candidate_skills,
    get_candidate_skills_above_threshold,
    get_taxonomy_prompt_text,
    mark_framework_promoted,
    mark_skill_promoted,
    merge_framework,
    merge_skill,
    promote_framework,
    promote_skill,
)
from db.applications import (
    create_application,
    expire_stale_applications,
    get_all_applications,
    get_application,
    get_application_by_job,
    get_application_detail,
    get_application_stats,
    list_applications,
    update_application,
)

__all__ = [
    # jobs
    "expire_old_jobs", "get_active_job_count", "get_active_t3_scored_jobs",
    "get_freshness_stats", "get_job_detail", "get_jobs_by_ids",
    "get_jobs_for_reprocessing", "get_top_scored_jobs", "insert_job",
    "list_jobs", "mark_job_failed", "update_job_status",
    "update_tier2_scores", "update_tier3_scores",
    # taxonomy
    "discard_framework", "discard_skill", "get_all_canonical_frameworks",
    "get_all_canonical_skills", "get_candidate_frameworks",
    "get_candidate_frameworks_above_threshold", "get_candidate_skills",
    "get_candidate_skills_above_threshold", "get_taxonomy_prompt_text",
    "mark_framework_promoted", "mark_skill_promoted", "merge_framework",
    "merge_skill", "promote_framework", "promote_skill",
    # applications
    "create_application", "expire_stale_applications", "get_all_applications",
    "get_application", "get_application_by_job", "get_application_detail",
    "get_application_stats", "list_applications", "update_application",
]
