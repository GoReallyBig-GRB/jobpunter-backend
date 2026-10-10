"""Server-owned structured-output contracts for all supported AI tasks.

Contracts are intentionally enforced in application code rather than relying on
provider JSON mode or caller-supplied schema descriptions.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError


class EvidenceStatus(StrEnum):
    DIRECT = "direct"
    TRANSFERABLE = "transferable"
    PARTIAL = "partial"
    MISSING = "missing"
    CONTRADICTED = "contradicted"
    UNVERIFIED = "unverified"


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12_000)]
SummaryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5_000)]


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
    summary: SummaryText
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


class CVGenerationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: NonBlank
    summary: NonBlank | None = None
    missing_information: list[NonBlank] = Field(default_factory=list, max_length=100)
    unsupported_claims: list[NonBlank] = Field(default_factory=list, max_length=100)


class CoverLetterOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: NonBlank
    missing_information: list[NonBlank] = Field(default_factory=list, max_length=100)
    unsupported_claims: list[NonBlank] = Field(default_factory=list, max_length=100)


class ApplicationQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: NonBlank
    suggested_answer: NonBlank
    evidence_basis: list[NonBlank] = Field(max_length=50)
    confidence: Literal["high", "medium", "low", "unknown"]
    unsupported_claims: list[NonBlank] = Field(max_length=50)
    missing_information: list[NonBlank] = Field(default_factory=list, max_length=50)


class ApplicationQuestionsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[ApplicationQuestion] = Field(max_length=100)


class SuggestedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: NonBlank
    answer: NonBlank
    evidence_basis: list[NonBlank] = Field(max_length=50)
    confidence: Literal["high", "medium", "low", "unknown"]
    unsupported_claims: list[NonBlank] = Field(max_length=50)
    missing_information: list[NonBlank] = Field(default_factory=list, max_length=50)


class SuggestedAnswersOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[SuggestedAnswer] = Field(max_length=100)


class ClaimEvidenceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    artifact_type: Literal["tailored_cv", "cover_letter", "application_q_and_a"]
    claim_text: NonBlank
    status: Literal["supported", "unsupported", "contradicted", "unverifiable"]
    evidence_refs: list[NonBlank] = Field(max_length=50)
    rationale: NonBlank
    confidence: Literal["high", "medium", "low", "unknown"]


class ClaimEvidenceValidationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: list[ClaimEvidenceCheck] = Field(min_length=1, max_length=500)
    unassessed_claims: list[NonBlank] = Field(default_factory=list, max_length=200)
    coverage_complete: bool


_TASK_OUTPUT_ADAPTERS: dict[str, TypeAdapter] = {
    "document_parsing": TypeAdapter(DocumentParsingOutput),
    "profile_extraction": TypeAdapter(ProfileExtractionOutput),
    "jd_extraction": TypeAdapter(JobDescriptionOutput),
    "requirement_classification": TypeAdapter(RequirementClassificationOutput),
    "evidence_matching": TypeAdapter(EvidenceMatchingOutput),
    "career_pathway_discovery": TypeAdapter(CareerPathwayDiscoveryOutput),
    "cv_generation": TypeAdapter(CVGenerationOutput),
    "cover_letter": TypeAdapter(CoverLetterOutput),
    "application_questions": TypeAdapter(ApplicationQuestionsOutput),
    "suggested_answers": TypeAdapter(SuggestedAnswersOutput),
    "claim_evidence_validation": TypeAdapter(ClaimEvidenceValidationOutput),
}

_GEMINI_SCHEMA_KEYS = {
    "$defs", "$ref", "$anchor", "type", "format", "title", "description", "enum",
    "items", "prefixItems", "minItems", "maxItems", "minimum", "maximum", "anyOf",
    "oneOf", "properties", "additionalProperties", "required", "propertyOrdering",
}


def provider_json_schema(task: str) -> dict:
    """Return a provider-compatible schema; Pydantic enforces omitted constraints at runtime."""
    adapter = _TASK_OUTPUT_ADAPTERS.get(task)
    if adapter is None:
        raise ValueError("Unknown AI task output contract.")
    schema = adapter.json_schema()

    def clean(node: object) -> object:
        if isinstance(node, list):
            return [clean(item) for item in node]
        if not isinstance(node, dict):
            return node
        result = {}
        for key, value in node.items():
            if key not in _GEMINI_SCHEMA_KEYS:
                continue
            if key in {"$defs", "properties"} and isinstance(value, dict):
                result[key] = {name: clean(definition) for name, definition in value.items()}
            else:
                result[key] = clean(value)
        return result

    return clean(schema)


def validate_task_output(task: str, parsed_data: object) -> object:
    """Validate structured output against the registered task-specific contract."""
    adapter = _TASK_OUTPUT_ADAPTERS.get(task)
    if adapter is None:
        return parsed_data
    try:
        validated = adapter.validate_python(parsed_data, strict=False)
        return validated.model_dump(mode="json", exclude_unset=True)
    except ValidationError as exc:
        raise InvalidTaskOutput(task) from exc

class InvalidTaskOutput(ValueError):
    """Provider output violates the server-owned contract for its AI task."""
