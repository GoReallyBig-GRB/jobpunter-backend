"""Candidate-owned career pathway API contracts."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CareerPathwayFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=255)
    role_family: str = Field(min_length=1, max_length=255)
    target_roles: list[str] = Field(min_length=1, max_length=50)
    target_seniority: list[str] = Field(default_factory=list, max_length=20)
    domains: list[str] = Field(default_factory=list, max_length=50)
    supporting_evidence_refs: list[str] = Field(default_factory=list, max_length=50)
    gaps: list[str] = Field(default_factory=list, max_length=50)
    rationale: str = Field(min_length=1, max_length=5000)
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("target_roles", "target_seniority", "domains", "gaps")
    @classmethod
    def normalize_unique_values(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("Pathway lists must not contain blank values.")
        if len({value.casefold() for value in normalized}) != len(normalized):
            raise ValueError("Pathway lists must not contain duplicate values.")
        return normalized

    @field_validator("supporting_evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            try:
                normalized.append(str(UUID(value)))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("supporting_evidence_refs must contain Evidence UUIDs.") from exc
        if len(normalized) != len(set(normalized)):
            raise ValueError("supporting_evidence_refs must not contain duplicates.")
        return normalized


class CareerPathwayCreate(CareerPathwayFields):
    pass


class CareerPathwayUpdate(CareerPathwayFields):
    expected_revision: int = Field(ge=1)


class CareerPathwayDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=1000)


class CareerPathwaySuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    instruction: str | None = Field(default=None, max_length=4000)


class CareerPathwayResponse(BaseModel):
    id: UUID
    candidate_profile_id: UUID
    title: str
    role_family: str
    target_roles: list[str]
    target_seniority: list[str]
    domains: list[str]
    supporting_evidence_refs: list[str]
    gaps: list[str]
    rationale: str
    confidence: float | None
    source_type: Literal["USER_DEFINED", "AI_SUGGESTED"]
    approval_status: Literal["DRAFT", "PENDING_REVIEW", "APPROVED", "REJECTED", "RETIRED"]
    approved_at: datetime | None
    approved_by_user_id: UUID | None
    revision: int
    created_at: datetime
    updated_at: datetime


class CareerPathwayEventResponse(BaseModel):
    id: UUID
    career_pathway_id: UUID
    actor_user_id: UUID
    event_type: str
    previous_status: str | None
    new_status: str | None
    pathway_revision: int
    reason: str | None
    details: dict
    created_at: datetime
