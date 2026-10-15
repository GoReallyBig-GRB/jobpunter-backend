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


class CandidateProfileRecordBase(BaseModel):
    """Common typed fields shared by repeatable profile records."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    name: str | None = None
    title: str | None = None
    role: str | None = None
    summary: str | None = None
    status: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    evidence: list[Any] | dict[str, Any] | str | None = None
    verification_status: str | None = None


class CandidateCareerHistoryRecord(CandidateProfileRecordBase):
    """Employment record fields defined by the Master Career Profile contract."""

    employer: str | None = None
    company: str | None = None
    organization: str | None = None
    job_title: str | None = None
    employment_type: str | None = None
    industries: list[str] | None = None
    domains: list[str] | None = None
    responsibilities: list[str] | None = None
    products: list[Any] | None = None
    projects: list[Any] | None = None
    users: list[Any] | str | None = None
    customers: list[Any] | None = None
    candidate_role: str | None = None
    ownership: str | None = None
    tools: list[str] | None = None
    technologies: list[str] | None = None
    stakeholders: list[str] | None = None
    teams: list[Any] | None = None
    leadership_responsibilities: list[Any] | None = None
    achievements: list[Any] | None = None
    challenges: list[Any] | None = None
    outcomes: list[Any] | None = None


class CandidateProjectRecord(CandidateProfileRecordBase):
    """Project/product fields, including explicit delivery state."""

    product: str | None = None
    description: str | None = None
    problem: str | None = None
    problem_addressed: str | None = None
    context: str | None = None
    users: list[Any] | str | None = None
    customers: list[Any] | None = None
    stakeholders: list[Any] | None = None
    candidate_role: str | None = None
    responsibilities: list[str] | None = None
    ownership: str | None = None
    decisions: list[Any] | None = None
    constraints: list[Any] | None = None
    challenges: list[Any] | None = None
    solution: str | None = None
    tools: list[str] | None = None
    technologies: list[str] | None = None
    outcomes: list[Any] | None = None
    results: list[Any] | None = None
    evidence: list[Any] | dict[str, Any] | str | None = None
    product_status: str | None = None


class CandidateAchievementRecord(CandidateProfileRecordBase):
    """Achievement and measurable outcome fields."""

    achievement: str | None = None
    context: str | None = None
    action: str | None = None
    outcome: Any = None
    results: list[Any] | None = None
    metrics: Any = None
    impact: str | None = None
    source: str | dict[str, Any] | None = None
    evidence: list[Any] | dict[str, Any] | str | None = None


class CandidateChallengeRecord(CandidateProfileRecordBase):
    """Challenge/problem-solving record fields."""

    challenge: str | None = None
    problem: str | None = None
    context: str | None = None
    constraints: list[Any] | None = None
    solution: str | None = None
    decisions: list[Any] | None = None
    outcomes: list[Any] | None = None
    results: list[Any] | None = None
    tools: list[str] | None = None
    evidence: list[Any] | dict[str, Any] | str | None = None


class CandidateLeadershipRecord(CandidateProfileRecordBase):
    """Leadership and collaboration record fields."""

    responsibilities: list[str] | None = None
    leadership_responsibilities: list[Any] | None = None
    team_size: int | None = None
    teams: list[Any] | None = None
    stakeholders: list[Any] | None = None
    customers: list[Any] | None = None
    decisions: list[Any] | None = None
    outcomes: list[Any] | None = None
    results: list[Any] | None = None


class CandidateSkillToolRecord(CandidateProfileRecordBase):
    """Skill, tool, and technology fields."""

    skill: str | None = None
    tool: str | None = None
    technology: str | None = None
    skills: list[str] | None = None
    tools: list[str] | None = None
    technologies: list[str] | None = None
    proficiency: str | None = None
    years_experience: int | float | None = None
    last_used: str | None = None


class CandidateEducationRecord(CandidateProfileRecordBase):
    """Education and academic qualification fields."""

    institution: str | None = None
    school: str | None = None
    qualification: str | None = None
    degree: str | None = None
    field: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    graduation_date: str | None = None
    grade: str | None = None
    status: str | None = None
    evidence: list[Any] | dict[str, Any] | str | None = None


class CandidateCertificationRecord(CandidateProfileRecordBase):
    """Certification and professional credential fields."""

    certification: str | None = None
    qualification: str | None = None
    issuing_organization: str | None = None
    credential_id: str | None = None
    credential_url: str | None = None
    issue_date: str | None = None
    expiry_date: str | None = None
    status: str | None = None
    evidence: list[Any] | dict[str, Any] | str | None = None


# Backward-compatible alias for callers that used the former shared schema.
CandidateProfileRecord = CandidateProfileRecordBase


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
    career_history: list[CandidateCareerHistoryRecord] | None = None
    projects: list[CandidateProjectRecord] | None = None
    achievements: list[CandidateAchievementRecord] | None = None
    challenges: list[CandidateChallengeRecord] | None = None
    leadership: list[CandidateLeadershipRecord] | None = None
    skills_tools: list[CandidateSkillToolRecord] | None = None
    education: list[CandidateEducationRecord] | None = None
    certifications: list[CandidateCertificationRecord] | None = None

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


class CandidateProfileFactSourceInput(BaseModel):
    """Explicitly link a patched profile fact to a confirmed imported document field."""

    model_config = ConfigDict(extra="forbid")

    document_import_id: UUID
    field_name: str = Field(min_length=1, max_length=255)


class CandidateProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    sections: CandidateProfileSectionsPatch
    fact_sources: dict[str, CandidateProfileFactSourceInput] = Field(default_factory=dict)


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

class EvidenceSourceSummary(BaseModel):
    id: UUID
    source_type: str
    source_reference: str
    source_metadata: dict
    created_at: datetime


class EvidenceClaimLinkSummary(BaseModel):
    claim_id: UUID
    claim_type: str
    claim_status: str
    relationship_type: str


class EvidenceProfileFactSummary(BaseModel):
    id: UUID
    section: str
    fact_path: str
    fact_value: Any
    profile_revision: int
    source_type: str
    source_reference: str
    source_excerpt: str | None = None
    confidence: Any = None
    confirmed_at: datetime | None = None
    created_at: datetime


class EvidenceLibraryItemResponse(BaseModel):
    id: UUID
    candidate_profile_id: UUID
    evidence_type: str
    status: str
    content: dict
    revision: int
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    verified_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    verification_status: str
    health_status: str
    conflict_review_status: str
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    missing_information_status: str
    missing_information: list[Any] = Field(default_factory=list)
    source: EvidenceSourceSummary
    linked_claims: list[EvidenceClaimLinkSummary] = Field(default_factory=list)
    linked_profile_facts: list[EvidenceProfileFactSummary] = Field(default_factory=list)


class EvidenceLibraryListResponse(BaseModel):
    data: list[EvidenceLibraryItemResponse]
    pagination: dict[str, int]


class EvidenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)
    evidence_type: str | None = Field(default=None, min_length=1, max_length=64)
    content: dict | None = Field(default=None, min_length=1)
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_update(self):
        editable = {"evidence_type", "content", "effective_from", "effective_to", "expires_at"}
        supplied = self.model_fields_set.intersection(editable)
        if not supplied:
            raise ValueError("At least one evidence field must be supplied.")
        if "evidence_type" in supplied and self.evidence_type is None:
            raise ValueError("evidence_type cannot be null.")
        if "content" in supplied and self.content is None:
            raise ValueError("content cannot be null.")
        if not self.reason.strip():
            raise ValueError("A nonblank reason is required.")
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective_to cannot precede effective_from.")
        if (
            self.effective_from is not None
            and self.expires_at is not None
            and self.expires_at < self.effective_from
        ):
            raise ValueError("expires_at cannot precede effective_from.")
        return self


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
