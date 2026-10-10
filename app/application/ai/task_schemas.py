"""Server-owned AI task output contracts, validated after generation."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractedField(StrictOutput):
    name: str = Field(min_length=1, max_length=160)
    value: str | None = Field(default=None, max_length=4000)
    source_text: str | None = Field(default=None, max_length=4000)
    confidence: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"] = "UNKNOWN"


class DocumentParsingOutput(StrictOutput):
    document_type: str = Field(min_length=1, max_length=120)
    fields: list[ExtractedField] = Field(max_length=300)
    conflicts: list[str] = Field(default_factory=list, max_length=100)
    unresolved_items: list[str] = Field(default_factory=list, max_length=100)


class ProfileExtractionOutput(StrictOutput):
    fields: list[ExtractedField] = Field(max_length=300)
    summary: str = Field(default="", max_length=5000)
    gaps: list[str] = Field(default_factory=list, max_length=100)


class Requirement(StrictOutput):
    text: str = Field(min_length=1, max_length=2000)
    category: str = Field(min_length=1, max_length=100)
    mandatory: bool
    source_text: str = Field(min_length=1, max_length=2000)


class JobDescriptionOutput(StrictOutput):
    job_title: str | None = Field(default=None, max_length=300)
    company: str | None = Field(default=None, max_length=300)
    location: str | None = Field(default=None, max_length=300)
    employment_type: str | None = Field(default=None, max_length=120)
    responsibilities: list[str] = Field(default_factory=list, max_length=150)
    requirements: list[Requirement] = Field(default_factory=list, max_length=200)
    eligibility_conditions: list[str] = Field(default_factory=list, max_length=100)
    application_instructions: list[str] = Field(default_factory=list, max_length=100)
    uncertainties: list[str] = Field(default_factory=list, max_length=100)


class ClassifiedRequirement(StrictOutput):
    requirement_text: str = Field(min_length=1, max_length=2000)
    category: Literal["experience", "skills", "education", "certification", "location", "work_authorization", "compensation", "availability", "responsibility", "other"]
    severity: Literal["MUST_HAVE", "PREFERRED", "INFORMATIONAL", "UNCLEAR"]
    requirement_type: Literal["HARD_CONSTRAINT", "QUALIFICATION", "PREFERENCE", "DUTY", "OTHER"]
    source_text: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(default="", max_length=2000)


class RequirementClassificationOutput(StrictOutput):
    requirements: list[ClassifiedRequirement] = Field(max_length=200)


class EvidenceAssessment(StrictOutput):
    requirement_text: str = Field(min_length=1, max_length=2000)
    status: Literal["DIRECT", "TRANSFERABLE", "PARTIAL", "MISSING", "CONTRADICTED", "UNVERIFIED"]
    evidence: list[str] = Field(default_factory=list, max_length=50)
    rationale: str = Field(min_length=1, max_length=3000)
    gap: str | None = Field(default=None, max_length=2000)


class EvidenceMatchingOutput(StrictOutput):
    assessments: list[EvidenceAssessment] = Field(max_length=200)
    evidence_summary: str = Field(min_length=1, max_length=5000)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=100)


class CareerPathway(StrictOutput):
    title: str = Field(min_length=1, max_length=300)
    rationale: str = Field(min_length=1, max_length=3000)
    supporting_evidence: list[str] = Field(default_factory=list, max_length=50)
    gaps: list[str] = Field(default_factory=list, max_length=50)
    uncertainty: Literal["LOW", "MEDIUM", "HIGH"]


class CareerPathwayDiscoveryOutput(StrictOutput):
    pathways: list[CareerPathway] = Field(max_length=50)
    caveats: list[str] = Field(default_factory=list, max_length=100)


class DraftOutput(StrictOutput):
    content: str = Field(min_length=1, max_length=30000)
    missing_information: list[str] = Field(default_factory=list, max_length=100)
    unsupported_claims: list[str] = Field(default_factory=list, max_length=100)


class ApplicationQuestion(StrictOutput):
    question: str = Field(min_length=1, max_length=2000)
    suggested_answer: str = Field(default="", max_length=8000)
    evidence_basis: list[str] = Field(default_factory=list, max_length=50)
    missing_information: list[str] = Field(default_factory=list, max_length=50)


class ApplicationQuestionsOutput(StrictOutput):
    questions: list[ApplicationQuestion] = Field(max_length=100)


class SuggestedAnswer(StrictOutput):
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=8000)
    evidence_basis: list[str] = Field(default_factory=list, max_length=50)
    missing_information: list[str] = Field(default_factory=list, max_length=50)


class SuggestedAnswersOutput(StrictOutput):
    answers: list[SuggestedAnswer] = Field(max_length=100)


TASK_OUTPUT_MODELS: dict[str, type[StrictOutput]] = {
    "document_parsing": DocumentParsingOutput,
    "profile_extraction": ProfileExtractionOutput,
    "jd_extraction": JobDescriptionOutput,
    "requirement_classification": RequirementClassificationOutput,
    "evidence_matching": EvidenceMatchingOutput,
    "career_pathway_discovery": CareerPathwayDiscoveryOutput,
    "cv_generation": DraftOutput,
    "cover_letter": DraftOutput,
    "application_questions": ApplicationQuestionsOutput,
    "suggested_answers": SuggestedAnswersOutput,
}


def validate_task_output(task: str, data: object) -> StrictOutput:
    """Validate provider data against the task's server-owned contract."""
    return TASK_OUTPUT_MODELS[task].model_validate(data)


def task_output_schema(task: str) -> type[StrictOutput]:
    """Return the Pydantic model used for provider-side JSON schema guidance."""
    return TASK_OUTPUT_MODELS[task]
