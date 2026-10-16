"""Stable candidate-facing opportunity feed contract for frontend integration."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class OpportunityProvenanceResponse(BaseModel):
    source_id: UUID
    source_name: str
    source_type: str
    source_url: str | None
    external_job_id: str | None
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    observation_count: int
    observation_type: str | None
    status: str | None


class OpportunityDescriptionResponse(BaseModel):
    version_number: int
    content: str
    content_hash: str
    completeness_status: str
    retrieved_at: datetime
    requirement_extraction_status: str


class OpportunityJobResponse(BaseModel):
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
    application_route_type: str | None
    application_route_verification: dict
    lifecycle_status: str
    freshness_status: str
    first_discovered_at: datetime
    last_observed_at: datetime
    last_verified_at: datetime | None
    metadata: dict
    description: OpportunityDescriptionResponse | None
    provenance: list[OpportunityProvenanceResponse]
    punt_status: str | None


class OpportunityEvaluationResponse(BaseModel):
    id: UUID
    target_role_key: str
    evaluation_version: int
    validity_status: str
    outcome: str
    score: float | None
    hard_blockers: list
    evidence_summary: dict
    explanation: dict
    policy_version: str
    evaluated_at: datetime


class CandidateOpportunityFeedItem(BaseModel):
    job: OpportunityJobResponse
    evaluation: OpportunityEvaluationResponse


class CandidateOpportunityPagination(BaseModel):
    limit: int
    offset: int
    total: int


class CandidateOpportunityFeedResponse(BaseModel):
    data: list[CandidateOpportunityFeedItem]
    pagination: CandidateOpportunityPagination
