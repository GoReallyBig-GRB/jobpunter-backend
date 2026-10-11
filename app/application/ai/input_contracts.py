"""Task-specific input contracts for AI tasks that affect job qualification."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.domain.jobs.completeness import has_substantive_job_description

INPUT_CONTRACT_VERSION = "v2"


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


class ApprovedEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    type: str = Field(min_length=1, max_length=80)
    content: Any


class EvidenceMatchingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirements: list[MatchingRequirementInput] = Field(min_length=1, max_length=200)
    approved_candidate_evidence: list[ApprovedEvidenceInput] = Field(max_length=500)
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
    def artifact_types_must_be_unique(self):
        types = [item.artifact_type for item in self.artifacts]
        if len(types) != len(set(types)):
            raise ValueError("Artifact types must not be duplicated.")
        return self


class InvalidTaskInput(ValueError):
    """A task input did not satisfy its server-owned input contract."""


_INPUT_MODELS = {
    "document_parsing": DocumentParsingInput,
    "jd_extraction": JobDescriptionInput,
    "job_requirement_extraction": JobDescriptionInput,
    "evidence_matching": EvidenceMatchingInput,
    "claim_evidence_validation": ClaimEvidenceValidationInput,
}


def validate_task_input(task: str, prompt: str) -> dict[str, Any]:
    """Validate JSON task payloads for decision-critical AI tasks."""
    model = _INPUT_MODELS.get(task)
    if model is None:
        return {"prompt": prompt}
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
