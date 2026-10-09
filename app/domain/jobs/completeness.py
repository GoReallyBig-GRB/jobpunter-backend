from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class JobDescriptionStatus(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    URL_INACCESSIBLE = "URL_INACCESSIBLE"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    CLOSED = "CLOSED"
    STALE = "STALE"


@dataclass(frozen=True)
class CompletenessAssessment:
    status: JobDescriptionStatus
    missing_fields: tuple[str, ...]
    suitable_for_evaluation: bool


_REQUIRED_FIELDS = (
    "canonical_title",
    "company_name",
    "job_description",
    "application_url",
)


def assess_job_completeness(
    job: dict[str, Any],
    *,
    retrieval_status: str | None = None,
    lifecycle_status: str | None = None,
    freshness_status: str | None = None,
) -> CompletenessAssessment:
    """Assess information without inferring missing facts.

    FULL requires title, employer, actual description text, and an application URL.
    Title/employer-only records cannot be treated as ready for confident evaluation.
    """
    retrieval = (retrieval_status or "").strip().upper()
    lifecycle = (lifecycle_status or "").strip().upper()
    freshness = (freshness_status or "").strip().upper()

    if lifecycle == "CLOSED" or retrieval == "CLOSED":
        return CompletenessAssessment(JobDescriptionStatus.CLOSED, (), False)
    if freshness == "STALE" or retrieval == "STALE":
        return CompletenessAssessment(JobDescriptionStatus.STALE, (), False)
    if retrieval == "URL_INACCESSIBLE":
        return CompletenessAssessment(
            JobDescriptionStatus.URL_INACCESSIBLE, ("job_description",), False
        )
    if retrieval == "UNAVAILABLE":
        return CompletenessAssessment(
            JobDescriptionStatus.UNAVAILABLE, _REQUIRED_FIELDS, False
        )

    present = {
        field: bool(str(job.get(field) or "").strip())
        for field in _REQUIRED_FIELDS
    }
    missing = tuple(field for field in _REQUIRED_FIELDS if not present[field])

    if not any(present.values()):
        return CompletenessAssessment(JobDescriptionStatus.UNAVAILABLE, missing, False)

    if not present["canonical_title"] or not present["company_name"]:
        return CompletenessAssessment(JobDescriptionStatus.REQUIRES_REVIEW, missing, False)

    if missing:
        return CompletenessAssessment(JobDescriptionStatus.PARTIAL, missing, False)

    return CompletenessAssessment(JobDescriptionStatus.FULL, (), True)
