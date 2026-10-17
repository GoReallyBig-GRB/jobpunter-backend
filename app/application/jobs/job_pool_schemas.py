"""Stable candidate-facing contracts for the canonical Job Pool."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class JobPoolProvenance(BaseModel):
    source_id: UUID
    source_name: str
    source_type: str
    source_url: str | None
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


class JobPoolDescription(JobPoolDescriptionPreview):
    content: str


class JobPoolItem(BaseModel):
    id: UUID
    title: str
    company_name: str | None
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
    lifecycle_status: str
    freshness_status: str
    first_discovered_at: datetime
    last_observed_at: datetime
    last_verified_at: datetime | None
    description: JobPoolDescriptionPreview | None
    provenance: list[JobPoolProvenance]


class JobPoolDetail(JobPoolItem):
    description: JobPoolDescription | None


class JobPoolPagination(BaseModel):
    limit: int
    offset: int
    total: int


class JobPoolListResponse(BaseModel):
    data: list[JobPoolItem]
    pagination: JobPoolPagination


class JobPoolDetailResponse(BaseModel):
    data: JobPoolDetail
