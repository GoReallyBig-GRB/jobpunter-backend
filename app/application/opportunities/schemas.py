from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field


class PuntCreate(BaseModel):
    evaluation_id: UUID


class ApplicationCreate(BaseModel):
    punt_id: UUID


class ApplicationTransitionRequest(BaseModel):
    target_status: str = Field(min_length=1, max_length=32)
    expected_revision: int = Field(ge=1)


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
    status: str
    submitted_at: datetime | None
    revision: int
    created_at: datetime
    updated_at: datetime
