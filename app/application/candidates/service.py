import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.errors import DomainError
from app.infrastructure.db.models import CandidateProfile, Evidence, EvidenceSource
from app.domain.candidate.service import transition_evidence
from app.domain.candidate.errors import InvalidEvidenceTransition
from app.application.candidates.schemas import EvidenceCreate

class CandidateService:
    def __init__(self, db: Session):
        self.db = db

    def get_or_create_profile(self, user_id: uuid.UUID) -> CandidateProfile:
        profile = self.db.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user_id))
        if profile:
            return profile
        profile = CandidateProfile(user_id=user_id)
        self.db.add(profile)
        self.db.flush()
        return profile

    def create_evidence(self, user_id: uuid.UUID, data: EvidenceCreate) -> Evidence:
        profile = self.get_or_create_profile(user_id)
        source = EvidenceSource(source_type=data.source_type, source_reference=data.source_reference, metadata_json=data.source_metadata)
        evidence = Evidence(candidate_profile_id=profile.id, evidence_source_id=source.id if source.id else None,
                            evidence_type=data.evidence_type, content=data.content, status="DRAFT")
        self.db.add(source)
        self.db.flush()
        evidence.evidence_source_id = source.id
        self.db.add(evidence)
        self.db.commit()
        self.db.refresh(evidence)
        return evidence

    def transition_evidence(self, user_id: uuid.UUID, evidence_id: uuid.UUID, target: str, expected_revision: int) -> Evidence:
        evidence = self.db.scalar(select(Evidence).join(CandidateProfile).where(Evidence.id == evidence_id, CandidateProfile.user_id == user_id))
        if not evidence:
            raise DomainError("NOT_FOUND", "Evidence not found", 404)
        if evidence.revision != expected_revision:
            raise DomainError("REVISION_CONFLICT", "Resource revision is stale", 409, {"current_revision": evidence.revision})
        try:
            transition_evidence(evidence, target)
        except InvalidEvidenceTransition as exc:
            raise DomainError("INVALID_STATE_TRANSITION", str(exc), 409) from exc
        self.db.commit()
        self.db.refresh(evidence)
        return evidence
