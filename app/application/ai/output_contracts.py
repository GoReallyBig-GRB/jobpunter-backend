"""Server-owned structured-output contracts for all supported AI tasks.

Contracts are intentionally enforced in application code rather than relying on
provider JSON mode or caller-supplied schema descriptions.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError, model_validator


class EvidenceStatus(StrEnum):
    # PRD v1.5 canonical evaluation states.
    DIRECT = "DIRECT"
    STRONG_TRANSFERABLE = "STRONG_TRANSFERABLE"
    TRANSFERABLE_CANONICAL = "TRANSFERABLE"
    PARTIAL_CANONICAL = "PARTIAL"
    WEAK_TRANSFERABLE = "WEAK_TRANSFERABLE"
    MISSING_CANONICAL = "MISSING"
    CONTRADICTED_CANONICAL = "CONTRADICTED"
    REQUIRES_VERIFICATION = "REQUIRES_VERIFICATION"
    DISQUALIFYING = "DISQUALIFYING"
    # Legacy lowercase values remain accepted at the AI boundary and are
    # normalized to canonical PRD states before any decision is persisted.
    LEGACY_DIRECT = "direct"
    LEGACY_TRANSFERABLE = "transferable"
    LEGACY_PARTIAL = "partial"
    LEGACY_MISSING = "missing"
    LEGACY_CONTRADICTED = "contradicted"
    LEGACY_UNVERIFIED = "unverified"
    LEGACY_STRONG_TRANSFERABLE = "strong_transferable"
    LEGACY_WEAK_TRANSFERABLE = "weak_transferable"
    LEGACY_REQUIRES_VERIFICATION = "requires_verification"
    LEGACY_DISQUALIFYING = "disqualifying"


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


class ExtractedJobRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    requirement_type: Literal[
        "SKILL", "EXPERIENCE", "EDUCATION", "CERTIFICATION", "LOCATION",
        "WORK_AUTHORIZATION", "WORK_MODE", "EMPLOYMENT_TYPE", "OTHER",
    ]
    description: NonBlank
    source_excerpt: NonBlank
    importance: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    hard_constraint: bool
    confidence: float = Field(ge=0, le=1)


class JobRequirementExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements: list[ExtractedJobRequirement] = Field(max_length=100)


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
    audited_artifact_types: list[Literal["tailored_cv", "cover_letter", "application_q_and_a"]] = Field(
        min_length=1, max_length=3
    )
    unassessed_claims: list[NonBlank] = Field(max_length=200)
    coverage_complete: bool

    @model_validator(mode="after")
    def validate_coverage_metadata(self):
        if len(self.audited_artifact_types) != len(set(self.audited_artifact_types)):
            raise ValueError("audited_artifact_types must not contain duplicates")
        if self.coverage_complete and self.unassessed_claims:
            raise ValueError("coverage_complete cannot be true when unassessed_claims are present")
        return self


_TASK_OUTPUT_ADAPTERS: dict[str, TypeAdapter] = {
    "document_parsing": TypeAdapter(DocumentParsingOutput),
    "profile_extraction": TypeAdapter(ProfileExtractionOutput),
    "jd_extraction": TypeAdapter(JobDescriptionOutput),
    "job_requirement_extraction": TypeAdapter(JobRequirementExtractionOutput),
    "requirement_classification": TypeAdapter(RequirementClassificationOutput),
    "evidence_matching": TypeAdapter(EvidenceMatchingOutput),
    "career_pathway_discovery": TypeAdapter(CareerPathwayDiscoveryOutput),
    "cv_generation": TypeAdapter(CVGenerationOutput),
    "cover_letter": TypeAdapter(CoverLetterOutput),
    "application_questions": TypeAdapter(ApplicationQuestionsOutput),
    "suggested_answers": TypeAdapter(SuggestedAnswersOutput),
    "claim_evidence_validation": TypeAdapter(ClaimEvidenceValidationOutput),
}

TASK_OUTPUT_CONTRACT_VERSIONS = MappingProxyType({
    task: ("v3" if task == "evidence_matching" else "v2" if task == "jd_extraction" else "v1") for task in _TASK_OUTPUT_ADAPTERS
})


def get_task_output_contract_version(task: str) -> str:
    """Return the version of the server-owned output contract for a registered task."""
    try:
        return TASK_OUTPUT_CONTRACT_VERSIONS[task]
    except KeyError as exc:
        raise ValueError("Unknown AI task output contract version.") from exc


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


def validate_task_output(
    task: str, parsed_data: object, input_data: dict | None = None
) -> object:
    """Validate structured output and task-specific source-grounding invariants."""
    adapter = _TASK_OUTPUT_ADAPTERS.get(task)
    if adapter is None:
        return parsed_data
    try:
        validated = adapter.validate_python(parsed_data, strict=False)
        result = validated.model_dump(mode="json", exclude_unset=True)
        if task == "evidence_matching":
            if not isinstance(input_data, dict):
                raise ValueError("Evidence matching input is unavailable.")
            requirements = input_data.get("requirements")
            evidence_rows = input_data.get("approved_candidate_evidence")
            if not isinstance(requirements, list) or not isinstance(evidence_rows, list):
                raise ValueError("Evidence matching inputs are invalid.")
            requirement_map = {
                item["requirement_id"]: " ".join(item["requirement_text"].casefold().split())
                for item in requirements if isinstance(item, dict)
            }
            evidence_ids = {
                item["id"] for item in evidence_rows if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
            seen_requirement_ids = set()
            for match in result["matches"]:
                requirement_id = match["requirement_id"]
                if requirement_id not in requirement_map:
                    raise ValueError("Evidence match references a requirement that was not supplied.")
                if requirement_id in seen_requirement_ids:
                    raise ValueError("Evidence matching output contains a duplicate requirement.")
                seen_requirement_ids.add(requirement_id)
                supplied_text = requirement_map[requirement_id]
                output_text = " ".join(match["requirement_text"].casefold().split())
                if output_text != supplied_text:
                    raise ValueError("Evidence match requirement text must match the supplied requirement.")
                if any(ref not in evidence_ids for ref in match["evidence_refs"]):
                    raise ValueError("Evidence match references candidate evidence that was not supplied.")
            if seen_requirement_ids != set(requirement_map):
                raise ValueError("Evidence matching output must cover every supplied requirement exactly once.")
        if task == "jd_extraction":
            description = input_data.get("job_description") if isinstance(input_data, dict) else None
            if not isinstance(description, str):
                raise ValueError("Job description extraction input is unavailable.")
            normalized_description = " ".join(description.casefold().split())
            for requirement in result["requirements"]:
                excerpt = " ".join(requirement["source_text"].casefold().split())
                if not excerpt or excerpt not in normalized_description:
                    raise ValueError("Extracted requirement source text is not grounded in the supplied job description.")
        if task == "job_requirement_extraction":
            description = input_data.get("job_description") if isinstance(input_data, dict) else None
            if not isinstance(description, str):
                raise ValueError("Job requirement extraction input is unavailable.")
            normalized_description = " ".join(description.casefold().split())
            for requirement in result["requirements"]:
                excerpt = " ".join(requirement["source_excerpt"].casefold().split())
                if not excerpt or excerpt not in normalized_description:
                    raise ValueError("Extracted source excerpt is not grounded in the supplied job description.")
        if task == "document_parsing":
            document_text = input_data.get("document_text") if isinstance(input_data, dict) else None
            if not isinstance(document_text, str):
                raise ValueError("Document parsing input is unavailable.")
            normalized_document = " ".join(document_text.casefold().split())
            for field in result["fields"]:
                source_text = field.get("source_text")
                if source_text is not None:
                    excerpt = " ".join(source_text.casefold().split())
                    if not excerpt or excerpt not in normalized_document:
                        raise ValueError("Extracted source text is not grounded in the uploaded document.")
        if task == "claim_evidence_validation":
            if not isinstance(input_data, dict):
                raise ValueError("Claim validation input is unavailable.")
            artifact_rows = input_data.get("artifacts")
            evidence_rows = input_data.get("approved_candidate_evidence")
            if not isinstance(artifact_rows, list) or not isinstance(evidence_rows, list):
                raise ValueError("Claim validation inputs are invalid.")
            artifact_types = {item.get("artifact_type") for item in artifact_rows if isinstance(item, dict)}
            evidence_ids = {item.get("id") for item in evidence_rows if isinstance(item, dict)}
            if set(result["audited_artifact_types"]) != artifact_types:
                raise ValueError("Audited artifact types must match the supplied artifacts.")
            for check in result["checks"]:
                if check["artifact_type"] not in artifact_types:
                    raise ValueError("Claim check references an artifact type that was not supplied.")
                if any(ref not in evidence_ids for ref in check["evidence_refs"]):
                    raise ValueError("Claim check references evidence that was not supplied.")
        return result
    except (ValidationError, ValueError, KeyError, TypeError) as exc:
        raise InvalidTaskOutput(task) from exc

class InvalidTaskOutput(ValueError):
    """Provider output violates the server-owned contract for its AI task."""
