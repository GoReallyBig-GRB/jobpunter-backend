from datetime import datetime
from uuid import UUID
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator


def _has_profile_record_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_profile_record_content(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_profile_record_content(item) for item in value)
    return True


def _validate_repeatable_profile_records(instance: BaseModel) -> None:
    for section_name in (
        "career_history", "projects", "achievements", "challenges", "leadership",
        "skills_tools", "education", "certifications",
    ):
        records = getattr(instance, section_name, None)
        if records is not None and any(
            not _has_profile_record_content(record) for record in records
        ):
            raise ValueError(
                f"{section_name} records must contain at least one non-empty field."
            )

class CandidateProfileSections(BaseModel):
    """Editable, candidate-owned factual sections; preferences stay in their own resource."""

    model_config = ConfigDict(extra="forbid")

    identity: dict[str, Any] = Field(default_factory=dict)
    eligibility: dict[str, Any] = Field(default_factory=dict)
    professional_identity: dict[str, Any] = Field(default_factory=dict)
    career_history: list[dict[str, Any]] = Field(default_factory=list)
    projects: list[dict[str, Any]] = Field(default_factory=list)
    achievements: list[dict[str, Any]] = Field(default_factory=list)
    challenges: list[dict[str, Any]] = Field(default_factory=list)
    leadership: list[dict[str, Any]] = Field(default_factory=list)
    skills_tools: list[dict[str, Any]] = Field(default_factory=list)
    education: list[dict[str, Any]] = Field(default_factory=list)
    certifications: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_repeatable_records(self):
        _validate_repeatable_profile_records(self)
        return self


class CandidateProfileSectionsPatch(BaseModel):
    """Section-level patch so editing one section cannot erase the others."""

    model_config = ConfigDict(extra="forbid")

    identity: dict[str, Any] | None = None
    eligibility: dict[str, Any] | None = None
    professional_identity: dict[str, Any] | None = None
    career_history: list[dict[str, Any]] | None = None
    projects: list[dict[str, Any]] | None = None
    achievements: list[dict[str, Any]] | None = None
    challenges: list[dict[str, Any]] | None = None
    leadership: list[dict[str, Any]] | None = None
    skills_tools: list[dict[str, Any]] | None = None
    education: list[dict[str, Any]] | None = None
    certifications: list[dict[str, Any]] | None = None

    @model_validator(mode="after")
    def validate_repeatable_records(self):
        _validate_repeatable_profile_records(self)
        return self

    @model_validator(mode="before")
    @classmethod
    def reject_null_sections(cls, value):
        if isinstance(value, dict) and any(
            key in value and value[key] is None for key in cls.model_fields
        ):
            raise ValueError("Profile sections cannot be null; use an empty object or list to clear a section.")
        return value

    @model_validator(mode="after")
    def require_at_least_one_section(self):
        if not self.model_fields_set:
            raise ValueError("At least one profile section must be supplied.")
        return self


class CandidateProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    sections: CandidateProfileSectionsPatch


class CandidateProfileResponse(BaseModel):
    id: UUID
    user_id: UUID
    status: str
    revision: int
    profile_data: CandidateProfileSections


class EvidenceCreate(BaseModel):
    evidence_type: str = Field(min_length=1, max_length=64)
    content: dict = Field(min_length=1)
    source_type: str = Field(min_length=1, max_length=64)
    source_reference: str = Field(min_length=1)
    source_metadata: dict = Field(default_factory=dict)

class EvidenceResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: UUID
    candidate_profile_id: UUID
    evidence_type: str
    status: str
    content: dict
    revision: int
    verified_at: datetime | None = None

class EvidenceTransitionRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class CandidateClaimCreate(BaseModel):
    claim_type: str = Field(min_length=1, max_length=64)
    claim_content: dict = Field(min_length=1)
    evidence_ids: list[UUID] = Field(default_factory=list)


class CandidateClaimResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: UUID
    candidate_profile_id: UUID
    claim_type: str
    claim_content: dict
    status: str
    revision: int
    evidence_ids: list[UUID]


class CandidateClaimTransitionRequest(BaseModel):
    expected_revision: int = Field(ge=1)
