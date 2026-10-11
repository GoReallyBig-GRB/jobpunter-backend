"""Task-specific input contracts for AI tasks that affect job qualification."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.domain.jobs.completeness import has_substantive_job_description

INPUT_CONTRACT_VERSION = "v1"


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


class InvalidTaskInput(ValueError):
    """A task input did not satisfy its server-owned input contract."""


_INPUT_MODELS = {
    "jd_extraction": JobDescriptionInput,
    "job_requirement_extraction": JobDescriptionInput,
    "evidence_matching": EvidenceMatchingInput,
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
