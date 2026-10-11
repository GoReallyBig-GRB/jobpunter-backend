from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field

class CandidateProfileResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: UUID
    user_id: UUID
    status: str
    revision: int

class EvidenceCreate(BaseModel):
    evidence_type: str = Field(min_length=1, max_length=64)
    content: dict
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
    claim_content: dict
    evidence_ids: list[UUID] = Field(default_factory=list)


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
