from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any



_RESPONSIBILITY_SIGNAL = re.compile(
    r"\\b(responsibilit(?:y|ies)|what you(?:'|’)ll do|what you will do|duties|"
    r"you will|you'll|day.to.day|role overview|key activities|deliverables|"
    r"manage|lead|develop|coordinate|design|implement|support|own|drive)\\b",
    re.IGNORECASE,
)
_QUALIFICATION_SIGNAL = re.compile(
    r"\\b(requirements?|qualifications?|must.have|nice.to.have|preferred|"
    r"experience|proficiency|proficient|skills?|degree|certification|"
    r"ability to|knowledge of|minimum of|at least \\d+ years)\\b",
    re.IGNORECASE,
)


def has_substantive_job_description(value: str | None) -> bool:
    """Reject title/link boilerplate and descriptions lacking both role duties and qualifications."""
    text = " ".join((value or "").split())
    words = re.findall(r"\\b[\\w+#.-]+\\b", text)
    if len(words) < 80:
        return False
    return bool(_RESPONSIBILITY_SIGNAL.search(text) and _QUALIFICATION_SIGNAL.search(text))


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
    description = str(job.get("job_description") or job.get("description") or "").strip()
    if present["job_description"] and not has_substantive_job_description(description):
        present["job_description"] = False
    missing = tuple(field for field in _REQUIRED_FIELDS if not present[field])

    if not any(present.values()):
        return CompletenessAssessment(JobDescriptionStatus.UNAVAILABLE, missing, False)

    if not present["canonical_title"] or not present["company_name"]:
        return CompletenessAssessment(JobDescriptionStatus.REQUIRES_REVIEW, missing, False)

    if missing:
        return CompletenessAssessment(JobDescriptionStatus.PARTIAL, missing, False)

    return CompletenessAssessment(JobDescriptionStatus.FULL, (), True)
