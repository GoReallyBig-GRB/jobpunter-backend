from datetime import datetime
from uuid import UUID
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    content: dict
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
    claim_content: dict
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
