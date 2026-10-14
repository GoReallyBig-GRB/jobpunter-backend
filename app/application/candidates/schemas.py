from datetime import datetime
from uuid import UUID
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator


def _has_profile_record_content(value: Any) -> bool:
    if isinstance(value, BaseModel):
        value = value.model_dump(exclude_unset=True)
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

class CandidateIdentitySection(BaseModel):
    """Known identity/contact fields, with forward-compatible extra fields."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    full_name: str | None = None
    preferred_name: str | None = None
    email: str | None = None
    phone: str | None = None
    current_location: str | None = None
    country: str | None = None
    linkedin_url: str | None = None
    portfolio_url: str | None = None
    github_url: str | None = None
    website_url: str | None = None


class CandidateEligibilitySection(BaseModel):
    """Candidate facts about legal/work eligibility, distinct from job preferences."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    work_authorizations: list[str] | None = None
    eligible_countries: list[str] | None = None
    work_authorization_countries: list[str] | None = None
    languages: list[str] | None = None
    professional_credentials: list[Any] | None = None
    relocation_status: str | None = None
    notes: str | None = None


class CandidateProfessionalIdentitySection(BaseModel):
    """Professional identity facts used to orient career history and generated materials."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    profession: str | None = None
    current_role: str | None = None
    years_experience: int | float | None = None
    summary: str | None = None
    headline: str | None = None
    industries: list[str] | None = None
    domains: list[str] | None = None
    functional_areas: list[str] | None = None
    seniority: str | None = None
    specializations: list[str] | None = None


class CandidateProfileRecord(BaseModel):
    """Typed common profile fields with forward-compatible preservation of extra facts."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    name: str | None = None
    title: str | None = None
    role: str | None = None
    employer: str | None = None
    company: str | None = None
    organization: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    employment_type: str | None = None
    institution: str | None = None
    school: str | None = None
    qualification: str | None = None
    degree: str | None = None
    field: str | None = None
    certification: str | None = None
    issuing_organization: str | None = None
    credential_id: str | None = None
    credential_url: str | None = None
    summary: str | None = None
    problem: str | None = None
    context: str | None = None
    challenge: str | None = None
    solution: str | None = None
    achievement: str | None = None
    verification_status: str | None = None
    status: str | None = None
    candidate_role: str | None = None
    current_role: str | None = None
    product: str | None = None
    ownership: str | None = None
    product_status: str | None = None
    responsibilities: list[str] | None = None
    projects: list[Any] | None = None
    tools: list[str] | None = None
    stakeholders: list[str] | None = None
    customers: list[Any] | None = None
    teams: list[Any] | None = None
    leadership_responsibilities: list[Any] | None = None
    tradeoffs: list[Any] | None = None
    results: list[Any] | None = None
    decisions: list[Any] | None = None
    constraints: list[Any] | None = None
    challenges: list[Any] | None = None
    outcomes: list[Any] | None = None
    skills: list[str] | None = None
    languages: list[str] | None = None
    users: list[Any] | str | None = None
    evidence: list[Any] | dict[str, Any] | str | None = None
    outcome: Any = None
    metrics: Any = None
    team_size: int | None = None
    years_experience: int | float | None = None


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

    identity: CandidateIdentitySection | None = None
    eligibility: CandidateEligibilitySection | None = None
    professional_identity: CandidateProfessionalIdentitySection | None = None
    career_history: list[CandidateProfileRecord] | None = None
    projects: list[CandidateProfileRecord] | None = None
    achievements: list[CandidateProfileRecord] | None = None
    challenges: list[CandidateProfileRecord] | None = None
    leadership: list[CandidateProfileRecord] | None = None
    skills_tools: list[CandidateProfileRecord] | None = None
    education: list[CandidateProfileRecord] | None = None
    certifications: list[CandidateProfileRecord] | None = None

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


class CandidateClaimUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    claim_content: dict | None = Field(default=None, min_length=1)
    evidence_ids: list[UUID] | None = None

    @model_validator(mode="after")
    def require_update_fields(self):
        supplied = self.model_fields_set.intersection({"claim_content", "evidence_ids"})
        if not supplied:
            raise ValueError("At least one claim field must be supplied.")
        if "claim_content" in supplied and self.claim_content is None:
            raise ValueError("claim_content cannot be null.")
        if "evidence_ids" in supplied and self.evidence_ids is None:
            raise ValueError("evidence_ids cannot be null.")
        return self


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
