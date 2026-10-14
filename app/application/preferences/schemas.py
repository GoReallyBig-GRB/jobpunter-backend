import math
from uuid import UUID
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TerminologyPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefer: list[str] = Field(default_factory=list, max_length=100)
    avoid: list[str] = Field(default_factory=list, max_length=100)


class CandidatePreferencesData(BaseModel):
    """Versioned search constraints; never merged into factual career evidence.

    Keep the current frontend preference fields losslessly representable while
    retaining backend-specific normalized fields for future deterministic rules.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)

    target_roles: list[str] = Field(default_factory=list, max_length=50, alias="targetRoles")
    target_seniority: list[str] = Field(default_factory=list, max_length=20, alias="targetSeniority")
    target_domains: list[str] = Field(default_factory=list, max_length=50, alias="targetDomains")
    target_industries: list[str] = Field(default_factory=list, max_length=50, alias="targetIndustries")
    preferred_industries: list[str] = Field(default_factory=list, max_length=50, alias="preferredIndustries")
    excluded_industries: list[str] = Field(default_factory=list, max_length=50, alias="excludedIndustries")
    preferred_locations: list[str] = Field(default_factory=list, max_length=50, alias="preferredLocations")
    remote_preference: Literal[
        "REMOTE_ONLY", "REMOTE_PREFERRED", "REMOTE_OK", "HYBRID_OK", "FLEXIBLE", "ONSITE_OK"
    ] = Field(default="FLEXIBLE", alias="remotePreference")
    eligible_countries: list[str] = Field(default_factory=list, max_length=50, alias="eligibleCountries")
    work_authorization_countries: list[str] = Field(
        default_factory=list, max_length=50, alias="workAuthorizationCountries"
    )
    work_authorizations: list[str] = Field(default_factory=list, max_length=50, alias="workAuthorizations")
    salary_min: float | None = Field(default=None, ge=0, allow_inf_nan=False, alias="salaryMin")
    salary_max: float | None = Field(default=None, ge=0, allow_inf_nan=False, alias="salaryMax")
    salary_currency: str | None = Field(default=None, min_length=3, max_length=8, alias="salaryCurrency")
    salary_period: Literal["HOUR", "MONTH", "YEAR", "CONTRACT", "UNSPECIFIED"] = Field(
        default="UNSPECIFIED", alias="salaryPeriod"
    )
    salary_expectation: str | None = Field(default=None, max_length=1000, alias="salaryExpectation")
    willing_to_relocate: bool | None = Field(default=None, alias="willingToRelocate")
    employment_types: list[str] = Field(default_factory=list, max_length=20, alias="employmentTypes")
    excluded_job_types: list[str] = Field(default_factory=list, max_length=50, alias="excludedJobTypes")
    max_application_fee: float = Field(default=0, ge=0, allow_inf_nan=False, alias="maxApplicationFee")
    max_application_fee_currency: str | None = Field(
        default=None, min_length=3, max_length=8, alias="maxApplicationFeeCurrency"
    )
    transferability_policy: Literal[
        "STRICT", "MODERATE", "FLEXIBLE", "CONSERVATIVE", "BALANCED", "AGGRESSIVE"
    ] = Field(default="MODERATE", alias="transferabilityPolicy")
    preferred_terminology: list[str] = Field(default_factory=list, max_length=100, alias="preferredTerminology")
    avoided_terminology: list[str] = Field(default_factory=list, max_length=100, alias="avoidedTerminology")
    terminology_preferences: TerminologyPreferences | None = Field(default=None, alias="terminologyPreferences")
    evidence_emphasis: list[str] = Field(default_factory=list, max_length=50, alias="evidenceEmphasis")
    evidence_exclusions: list[str] = Field(default_factory=list, max_length=50, alias="evidenceExclusions")
    other_search_constraints: list[str] = Field(default_factory=list, max_length=100, alias="otherSearchConstraints")
    notes: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="before")
    @classmethod
    def sanitize_non_finite_numeric_inputs(cls, value):
        # JSON parsers may decode overflowing numbers such as 1e309 to inf.
        # Keep those values invalid while ensuring FastAPI can serialize the 422 response.
        if not isinstance(value, dict):
            return value
        sanitized = dict(value)
        for key in (
            "salaryMin", "salary_min", "salaryMax", "salary_max",
            "maxApplicationFee", "max_application_fee",
        ):
            number = sanitized.get(key)
            if isinstance(number, float) and not math.isfinite(number):
                sanitized[key] = "non-finite number"
        return sanitized

    @field_validator("evidence_exclusions")
    @classmethod
    def validate_evidence_exclusion_ids(cls, value: list[str]) -> list[str]:
        normalized = []
        for item in value:
            try:
                normalized.append(str(UUID(item)))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("evidenceExclusions entries must be exact Evidence Library UUIDs.") from exc
        if len(normalized) != len(set(normalized)):
            raise ValueError("evidenceExclusions must not contain duplicate evidence IDs.")
        return normalized

    @classmethod
    def default_preferences(cls) -> "CandidatePreferencesData":
        return cls()

    @field_validator("*")
    @classmethod
    def reject_blank_string_list_entries(cls, value):
        if isinstance(value, list) and any(
            isinstance(item, str) and not item.strip() for item in value
        ):
            raise ValueError("Preference lists must not contain blank entries.")
        return value

    @model_validator(mode="after")
    def validate_salary_range(self) -> "CandidatePreferencesData":
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError("salaryMin cannot exceed salaryMax")
        if self.max_application_fee > 0 and not self.max_application_fee_currency:
            raise ValueError("maxApplicationFeeCurrency is required when maxApplicationFee is greater than zero")
        return self


class CandidatePreferencesPut(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_revision: int = Field(ge=0, alias="expectedRevision")
    preferences: CandidatePreferencesData


class CandidatePreferencesResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    revision: int
    preferences: CandidatePreferencesData
