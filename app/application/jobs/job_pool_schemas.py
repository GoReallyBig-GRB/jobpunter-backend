"""Stable candidate-facing contracts for the canonical Job Pool."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class JobPoolProvenance(BaseModel):
    source_id: UUID
    source_name: str
    source_type: str
    source_operational_state: str
    source_url: str | None
    external_job_id: str | None
    content_hash: str | None
    posting_date: str | None
    discovery_method: str | None
    observed_at: datetime
    observation_type: str
    status: str


class JobPoolDescriptionPreview(BaseModel):
    version_number: int
    preview: str
    content_hash: str
    completeness_status: str
    retrieved_at: datetime
    requirement_extraction_status: str
    requirement_extraction_attempts: int
    requirement_extraction_error: str | None
    requirement_extraction_version: str | None
    requirement_extraction_started_at: datetime | None
    requirement_extraction_retry_at: datetime | None
    requirements_extracted_at: datetime | None


class JobPoolDescription(JobPoolDescriptionPreview):
    content: str


class JobPoolDescriptionVersionSummary(BaseModel):
    version_number: int
    content_hash: str
    completeness_status: str
    retrieved_at: datetime
    requirement_extraction_status: str
    requirement_extraction_attempts: int
    requirement_extraction_error: str | None
    requirement_extraction_version: str | None
    requirement_extraction_started_at: datetime | None
    requirement_extraction_retry_at: datetime | None
    requirements_extracted_at: datetime | None


class JobPoolRequirement(BaseModel):
    id: UUID
    requirement_type: str
    description: str
    importance: str
    severity: str | None
    dimension: str | None
    hard_constraint: bool
    extraction_status: str
    source_excerpt: str | None
    extraction_confidence: float | None
    extraction_version: str | None


class JobPoolVerification(BaseModel):
    status: str
    http_status_code: int | None
    reason_code: str
    verifier_version: str
    checked_at: datetime


class JobPoolDiscoveryEvent(BaseModel):
    event_id: UUID
    source_id: UUID
    source_name: str
    source_type: str
    source_operational_state: str
    source_url: str | None
    external_job_id: str | None
    discovery_method: str | None
    content_hash: str
    observed_at: datetime
    observation_outcome: str
    processing_status: str
    processing_error: str | None


class JobPoolItem(BaseModel):
    id: UUID
    title: str
    company_name: str | None
    seniority: str | None
    employment_type: str | None
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_period: str | None
    location_country: str | None
    location_region: str | None
    location_city: str | None
    original_location_text: str | None
    work_mode: str | None
    remote_eligibility: str | None
    location_restrictions: dict
    application_url: str | None
    posting_date: str | None
    identity_fingerprint: str | None
    jd_retrieval_status: str | None
    verification_status: str | None
    first_discovered_at: datetime
    last_observed_at: datetime
    last_jd_retrieved_at: datetime | None
    last_verified_at: datetime | None
    last_evaluated_at: datetime | None
    description: JobPoolDescriptionPreview | None
    provenance: list[JobPoolProvenance]


class JobPoolDetail(JobPoolItem):
    description: JobPoolDescription | None
    verification: JobPoolVerification | None
    description_versions: list[JobPoolDescriptionVersionSummary]
    description_versions_truncated: bool
    requirements: list[JobPoolRequirement]
    requirements_truncated: bool
    responsibilities: list[str]
    discovery_history: list[JobPoolDiscoveryEvent]
    discovery_history_truncated: bool


class JobPoolPagination(BaseModel):
    limit: int
    offset: int
    total: int


class JobPoolListResponse(BaseModel):
    data: list[JobPoolItem]
    pagination: JobPoolPagination


class JobPoolDetailResponse(BaseModel):
    data: JobPoolDetail
