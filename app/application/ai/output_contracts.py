"""Server-owned structured-output contracts for safety-sensitive AI tasks.

Contracts are intentionally enforced in application code rather than relying on
provider JSON mode or caller-supplied schema descriptions.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class EvidenceStatus(StrEnum):
    DIRECT = "direct"
    TRANSFERABLE = "transferable"
    PARTIAL = "partial"
    MISSING = "missing"
    CONTRADICTED = "contradicted"
    UNVERIFIED = "unverified"


NonBlank = Annotated[str, Field(min_length=1, max_length=12_000)]


class EvidenceMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    requirement_id: NonBlank
    requirement_text: NonBlank
    status: EvidenceStatus
    rationale: NonBlank
    evidence_refs: list[NonBlank] = Field(max_length=100)


class EvidenceMatchingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[EvidenceMatch] = Field(min_length=1, max_length=500)


class ExtractedField(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: NonBlank
    value: str | None
    source_text: NonBlank | None = None
    confidence: Literal["high", "medium", "low", "unknown"] = "unknown"


class DocumentParsingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: NonBlank
    fields: list[ExtractedField] = Field(max_length=300)
    conflicts: list[NonBlank] = Field(default_factory=list, max_length=100)
    unresolved_items: list[NonBlank] = Field(default_factory=list, max_length=100)


class ProfileExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[ExtractedField] = Field(max_length=300)
    summary: str = Field(max_length=5000)
    gaps: list[NonBlank] = Field(default_factory=list, max_length=100)


class JobRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: NonBlank
    category: NonBlank
    mandatory: bool
    source_text: NonBlank


class JobDescriptionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_title: NonBlank | None
    company: NonBlank | None
    location: NonBlank | None
    employment_type: str | None
    responsibilities: list[NonBlank] = Field(max_length=150)
    requirements: list[JobRequirement] = Field(max_length=200)
    eligibility_conditions: list[NonBlank] = Field(max_length=100)
    application_instructions: list[NonBlank] = Field(max_length=100)
    uncertainties: list[NonBlank] = Field(default_factory=list, max_length=100)


class ClassifiedRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    requirement_text: NonBlank
    category: Literal[
        "experience", "skills", "education", "certification", "location",
        "work_authorization", "compensation", "availability", "responsibility", "other",
    ]
    severity: Literal["must_have", "preferred", "informational", "unclear"]
    requirement_type: Literal["hard_constraint", "qualification", "preference", "duty", "other"]
    source_text: NonBlank
    rationale: str = Field(default="", max_length=3000)


class RequirementClassificationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements: list[ClassifiedRequirement] = Field(max_length=200)


class CareerPathway(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: NonBlank
    rationale: NonBlank
    supporting_evidence: list[NonBlank] = Field(default_factory=list, max_length=50)
    gaps: list[NonBlank] = Field(default_factory=list, max_length=50)
    uncertainty: Literal["low", "medium", "high"]


class CareerPathwayDiscoveryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pathways: list[CareerPathway] = Field(max_length=50)
    caveats: list[NonBlank] = Field(default_factory=list, max_length=100)


class DraftOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: NonBlank
    missing_information: list[NonBlank] = Field(default_factory=list, max_length=100)
    unsupported_claims: list[NonBlank] = Field(default_factory=list, max_length=100)


class ApplicationQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: NonBlank
    suggested_answer: str = Field(default="", max_length=8000)
    evidence_basis: list[NonBlank] = Field(default_factory=list, max_length=50)
    missing_information: list[NonBlank] = Field(default_factory=list, max_length=50)


class ApplicationQuestionsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[ApplicationQuestion] = Field(max_length=100)


class SuggestedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: NonBlank
    answer: NonBlank
    evidence_basis: list[NonBlank] = Field(default_factory=list, max_length=50)
    missing_information: list[NonBlank] = Field(default_factory=list, max_length=50)


class SuggestedAnswersOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[SuggestedAnswer] = Field(max_length=100)


_TASK_OUTPUT_ADAPTERS: dict[str, TypeAdapter] = {
    "evidence_matching": TypeAdapter(EvidenceMatchingOutput),
}


def validate_task_output(task: str, parsed_data: object) -> object:
    """Validate known task-specific output contracts; unknown contracts fail closed only for that task."""
    adapter = _TASK_OUTPUT_ADAPTERS.get(task)
    if adapter is None:
        return parsed_data
    try:
        return adapter.validate_python(parsed_data, strict=False).model_dump(mode="json")
    except ValidationError as exc:
        raise InvalidTaskOutput(task) from exc


class InvalidTaskOutput(ValueError):
    """Provider output violates the server-owned contract for its AI task."""
