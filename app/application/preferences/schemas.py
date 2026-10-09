from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CandidatePreferencesData(BaseModel):
    """Versioned search constraints; never merged into factual career evidence."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    target_roles: list[str] = Field(default_factory=list, max_length=50, alias="targetRoles")
    target_seniority: list[str] = Field(default_factory=list, max_length=20, alias="targetSeniority")
    target_industries: list[str] = Field(default_factory=list, max_length=50, alias="targetIndustries")
    excluded_industries: list[str] = Field(default_factory=list, max_length=50, alias="excludedIndustries")
    preferred_locations: list[str] = Field(default_factory=list, max_length=50, alias="preferredLocations")
    remote_preference: Literal["REMOTE_ONLY", "REMOTE_PREFERRED", "FLEXIBLE", "ONSITE_OK"] = Field(
        default="FLEXIBLE", alias="remotePreference"
    )
    eligible_countries: list[str] = Field(default_factory=list, max_length=50, alias="eligibleCountries")
    work_authorization_countries: list[str] = Field(
        default_factory=list, max_length=50, alias="workAuthorizationCountries"
    )
    salary_min: float | None = Field(default=None, ge=0, alias="salaryMin")
    salary_max: float | None = Field(default=None, ge=0, alias="salaryMax")
    salary_currency: str | None = Field(default=None, min_length=3, max_length=8, alias="salaryCurrency")
    salary_period: Literal["HOUR", "MONTH", "YEAR", "CONTRACT", "UNSPECIFIED"] = Field(
        default="UNSPECIFIED", alias="salaryPeriod"
    )
    willing_to_relocate: bool | None = Field(default=None, alias="willingToRelocate")
    employment_types: list[str] = Field(default_factory=list, max_length=20, alias="employmentTypes")
    excluded_job_types: list[str] = Field(default_factory=list, max_length=50, alias="excludedJobTypes")
    max_application_fee: float = Field(default=0, ge=0, alias="maxApplicationFee")
    transferability_policy: Literal["STRICT", "MODERATE", "FLEXIBLE"] = Field(
        default="MODERATE", alias="transferabilityPolicy"
    )
    preferred_terminology: list[str] = Field(default_factory=list, max_length=100, alias="preferredTerminology")
    avoided_terminology: list[str] = Field(default_factory=list, max_length=100, alias="avoidedTerminology")
    evidence_emphasis: list[str] = Field(default_factory=list, max_length=50, alias="evidenceEmphasis")
    other_search_constraints: list[str] = Field(default_factory=list, max_length=100, alias="otherSearchConstraints")

    @classmethod
    def default_preferences(cls) -> "CandidatePreferencesData":
        return cls()


class CandidatePreferencesPut(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_revision: int = Field(ge=0, alias="expectedRevision")
    preferences: CandidatePreferencesData


class CandidatePreferencesResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    revision: int
    preferences: CandidatePreferencesData
