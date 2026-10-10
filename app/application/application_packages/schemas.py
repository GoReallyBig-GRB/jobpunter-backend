from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ArtifactType = Literal["tailored_cv", "cover_letter", "application_q_and_a"]
Confidence = Literal["high", "medium", "low", "unknown"]


class ApplicationPackageItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: ArtifactType
    content: dict
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    confidence: Confidence = "unknown"
    unsupported_claims: list[str] = Field(default_factory=list, max_length=100)
    ai_execution_id: UUID | None = None

    @field_validator("content")
    @classmethod
    def content_must_not_be_empty(cls, value: dict) -> dict:
        if not value:
            raise ValueError("Artifact content cannot be empty.")
        return value

    @model_validator(mode="after")
    def validate_artifact_shape(self):
        if self.artifact_type in {"tailored_cv", "cover_letter"}:
            body = self.content.get("content")
            if not isinstance(body, str) or not body.strip():
                raise ValueError("CV and cover-letter artifacts require non-empty content.")
        if self.artifact_type == "application_q_and_a":
            answers = self.content.get("answers")
            if not isinstance(answers, list):
                raise ValueError("Q&A content must contain an answers list.")
            for answer in answers:
                if not isinstance(answer, dict):
                    raise ValueError("Each Q&A answer must be an object.")
                if not isinstance(answer.get("question"), str) or not answer["question"].strip():
                    raise ValueError("Each Q&A item requires a question.")
                if not isinstance(answer.get("answer"), str) or not answer["answer"].strip():
                    raise ValueError("Each Q&A item requires an editable suggested answer.")
                if answer.get("confidence", "unknown") not in {"high", "medium", "low", "unknown"}:
                    raise ValueError("Q&A confidence must be high, medium, low, or unknown.")
                if not isinstance(answer.get("evidence_basis", []), list):
                    raise ValueError("Q&A evidence_basis must be a list.")
                if not isinstance(answer.get("unsupported_claims", []), list):
                    raise ValueError("Q&A unsupported_claims must be a list.")
        return self


class ApplicationPackageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ApplicationPackageItemCreate] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_artifact_types(self):
        types = [item.artifact_type for item in self.items]
        if len(types) != len(set(types)):
            raise ValueError("Each artifact type may appear only once in a package version.")
        return self


class ApplicationPackageItemResponse(BaseModel):
    id: UUID
    artifact_type: ArtifactType
    content: dict
    evidence_refs: list[str]
    confidence: Confidence
    unsupported_claims: list[str]
    ai_execution_id: UUID | None
    content_hash: str
    created_at: datetime


class ApplicationPackageResponse(BaseModel):
    id: UUID
    application_id: UUID
    version_number: int
    status: str
    source_snapshot: dict
    created_by_user_id: UUID
    created_at: datetime
    items: list[ApplicationPackageItemResponse]
