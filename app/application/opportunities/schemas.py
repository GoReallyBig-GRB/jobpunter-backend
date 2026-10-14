from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field


class PuntCreate(BaseModel):
    evaluation_id: UUID


class ApplicationCreate(BaseModel):
    punt_id: UUID


ApplicationStatus = Literal[
    "PLANNED", "APPROVED", "SUBMITTED", "INTERVIEW", "OFFER",
    "ACCEPTED", "REJECTED", "WITHDRAWN", "CLOSED",
]


class ApplicationTransitionRequest(BaseModel):
    target_status: ApplicationStatus
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=1000)
    notes: str | None = Field(default=None, max_length=5000)


class EvaluationResponse(BaseModel):
    id: UUID
    job_id: UUID
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


class PuntResponse(BaseModel):
    id: UUID
    job_id: UUID
    status: str
    qualified_at: datetime
    revision: int


class ApplicationResponse(BaseModel):
    id: UUID
    job_id: UUID
    punt_id: UUID | None
    status: ApplicationStatus
    submitted_at: datetime | None
    revision: int
    created_at: datetime
    updated_at: datetime


class ApplicationEventResponse(BaseModel):
    id: UUID
    event_type: str
    from_status: str | None
    to_status: str | None
    actor_type: str
    actor_id: UUID | None
    reason: str | None
    notes: str | None
    details: dict
    occurred_at: datetime
