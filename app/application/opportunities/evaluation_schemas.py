from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CandidateEvaluationCreate(BaseModel):
    job_id: UUID
    target_role_key: str = Field(min_length=1, max_length=255)


class CandidateEvaluationDetail(BaseModel):
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
    candidate_profile_revision: int
    preferences_revision: int
    job_description_version_id: UUID | None
    input_snapshot: dict
    evaluated_at: datetime
