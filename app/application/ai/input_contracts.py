"""Task-specific, bounded input contracts for governed AI tasks."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.domain.jobs.completeness import has_substantive_job_description

INPUT_CONTRACT_VERSION = "v4"


class JobDescriptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_title: str | None = Field(default=None, max_length=500)
    employer: str | None = Field(default=None, max_length=500)
    job_description: str = Field(min_length=80, max_length=70000)

    @field_validator("job_description")
    @classmethod
    def description_must_be_substantive(cls, value: str) -> str:
        if not has_substantive_job_description(value):
            raise ValueError("Job description must contain substantive duties and qualifications.")
        return value


class MatchingRequirementInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str = Field(min_length=1, max_length=80)
    requirement_text: str = Field(min_length=1, max_length=12000)
    mandatory: bool
    dimension: str | None = Field(default=None, max_length=80)
    requirement_type: str | None = Field(default=None, max_length=80)
    importance: str | None = Field(default=None, max_length=40)
    hard_constraint: bool | None = None
    source_excerpt: str | None = Field(default=None, max_length=12000)
    extraction_version: str | None = Field(default=None, max_length=80)


class ApprovedEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    type: str = Field(min_length=1, max_length=80)
    content: Any
    revision: int | None = Field(default=None, ge=0)

    @field_validator("content")
    @classmethod
    def evidence_content_must_be_present(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError("Approved evidence content must not be empty.")
        if isinstance(value, (dict, list)) and not value:
            raise ValueError("Approved evidence content must not be empty.")
        return value


class ClassifiableRequirementInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str = Field(min_length=1, max_length=80)
    requirement_text: str = Field(min_length=1, max_length=12000)
    source_excerpt: str = Field(min_length=1, max_length=12000)


class CandidateMatchingPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transferability_policy: Literal[
        "STRICT", "MODERATE", "FLEXIBLE", "CONSERVATIVE", "BALANCED", "AGGRESSIVE"
    ] = "MODERATE"
    evidence_emphasis: list[str] = Field(default_factory=list, max_length=100)
    preferred_terminology: list[str] = Field(default_factory=list, max_length=100)
    avoided_terminology: list[str] = Field(default_factory=list, max_length=100)
    other_search_constraints: list[str] = Field(default_factory=list, max_length=100)


class EvidenceMatchingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_title: str | None = Field(default=None, max_length=500)
    employer: str | None = Field(default=None, max_length=500)
    job_description_version_id: str = Field(min_length=1, max_length=80)
    requirements: list[MatchingRequirementInput] = Field(min_length=1, max_length=200)
    approved_candidate_evidence: list[ApprovedEvidenceInput] = Field(max_length=500)
    candidate_preferences: CandidateMatchingPreferences = Field(default_factory=CandidateMatchingPreferences)
    instruction: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def record_ids_must_be_unique(self):
        requirement_ids = [item.requirement_id for item in self.requirements]
        evidence_ids = [item.id for item in self.approved_candidate_evidence]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("Requirement IDs must be unique.")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Evidence IDs must be unique.")
        return self


class DocumentParsingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_text: str = Field(min_length=1, max_length=70000)

    @field_validator("document_text")
    @classmethod
    def document_text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Document text must not be blank.")
        return value


class ProfileExtractionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_text: str = Field(min_length=1, max_length=70000)
    existing_profile: dict[str, Any] = Field(default_factory=dict, max_length=100)
    instruction: str | None = Field(default=None, max_length=4000)

    @field_validator("source_text")
    @classmethod
    def source_text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Profile extraction source text must not be blank.")
        return value


class RequirementClassificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_description: str = Field(min_length=80, max_length=70000)
    requirements: list[ClassifiableRequirementInput] = Field(min_length=1, max_length=200)
    instruction: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def requirements_must_be_unique_and_source_grounded(self):
        ids = [item.requirement_id for item in self.requirements]
        texts = [" ".join(item.requirement_text.casefold().split()) for item in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("Requirement IDs must be unique.")
        if len(texts) != len(set(texts)):
            raise ValueError("Requirement text must be unique for deterministic classification coverage.")
        normalized_description = " ".join(self.job_description.casefold().split())
        for item in self.requirements:
            excerpt = " ".join(item.source_excerpt.casefold().split())
            if not excerpt or excerpt not in normalized_description:
                raise ValueError("Requirement source excerpts must be present in the supplied job description.")
        return self


class CareerPathwayDiscoveryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_profile: dict[str, Any] = Field(default_factory=dict, max_length=100)
    approved_candidate_evidence: list[ApprovedEvidenceInput] = Field(min_length=1, max_length=500)
    current_target_roles: list[str] = Field(default_factory=list, max_length=50)
    instruction: str | None = Field(default=None, max_length=4000)


class CVGenerationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_profile: dict[str, Any] = Field(default_factory=dict, max_length=100)
    approved_candidate_evidence: list[ApprovedEvidenceInput] = Field(min_length=1, max_length=500)
    target_role: str | None = Field(default=None, max_length=500)
    job_description: str | None = Field(default=None, max_length=70000)
    instruction: str | None = Field(default=None, max_length=4000)


class CoverLetterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_title: str = Field(min_length=1, max_length=500)
    employer: str = Field(min_length=1, max_length=500)
    job_description: str = Field(min_length=80, max_length=70000)
    candidate_profile: dict[str, Any] = Field(default_factory=dict, max_length=100)
    approved_candidate_evidence: list[ApprovedEvidenceInput] = Field(min_length=1, max_length=500)
    instruction: str | None = Field(default=None, max_length=4000)


class ApplicationQuestionsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_title: str = Field(min_length=1, max_length=500)
    employer: str = Field(min_length=1, max_length=500)
    job_description: str = Field(min_length=80, max_length=70000)
    approved_candidate_evidence: list[ApprovedEvidenceInput] = Field(min_length=1, max_length=500)
    known_questions: list[str] = Field(default_factory=list, max_length=100)
    instruction: str | None = Field(default=None, max_length=4000)


class SuggestedAnswersInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[str] = Field(min_length=1, max_length=100)
    approved_candidate_evidence: list[ApprovedEvidenceInput] = Field(min_length=1, max_length=500)
    job_context: dict[str, Any] = Field(default_factory=dict, max_length=100)
    instruction: str | None = Field(default=None, max_length=4000)


class ApplicationArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_type: Literal["tailored_cv", "cover_letter", "application_q_and_a"]
    content: Any
    declared_evidence_refs: list[str] = Field(max_length=200)
    declared_unsupported_claims: list[str] = Field(max_length=200)


class ClaimEvidenceRecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    type: str = Field(min_length=1, max_length=80)
    content: Any
    verified_at: str | None = Field(default=None, max_length=80)


class ClaimEvidenceValidationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifacts: list[ApplicationArtifactInput] = Field(min_length=1, max_length=3)
    approved_candidate_evidence: list[ClaimEvidenceRecordInput] = Field(max_length=500)

    @model_validator(mode="after")
    def artifact_types_and_evidence_ids_must_be_unique(self):
        types = [item.artifact_type for item in self.artifacts]
        evidence_ids = [item.id for item in self.approved_candidate_evidence]
        if len(types) != len(set(types)):
            raise ValueError("Artifact types must not be duplicated.")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Evidence IDs must not be duplicated.")
        return self


class InvalidTaskInput(ValueError):
    """A task input did not satisfy its server-owned input contract."""


_INPUT_MODELS = {
    "document_parsing": DocumentParsingInput,
    "profile_extraction": ProfileExtractionInput,
    "jd_extraction": JobDescriptionInput,
    "job_requirement_extraction": JobDescriptionInput,
    "requirement_classification": RequirementClassificationInput,
    "evidence_matching": EvidenceMatchingInput,
    "career_pathway_discovery": CareerPathwayDiscoveryInput,
    "cv_generation": CVGenerationInput,
    "cover_letter": CoverLetterInput,
    "application_questions": ApplicationQuestionsInput,
    "suggested_answers": SuggestedAnswersInput,
    "claim_evidence_validation": ClaimEvidenceValidationInput,
}


def validate_task_input(task: str, prompt: str) -> dict[str, Any]:
    """Validate JSON payloads for tasks with a server-owned typed input contract.

    Every registered task must provide a JSON object matching its server-owned
    model. Unknown tasks fail closed rather than falling back to free-form prompts.
    """
    model = _INPUT_MODELS.get(task)
    if model is None:
        raise InvalidTaskInput(task)
    try:
        parsed = json.loads(prompt)
        if not isinstance(parsed, dict):
            raise InvalidTaskInput(task)
        return model.model_validate(parsed).model_dump(mode="json")
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise InvalidTaskInput(task) from exc


def get_task_input_schema(task: str) -> dict[str, Any] | None:
    model = _INPUT_MODELS.get(task)
    return model.model_json_schema() if model is not None else None
