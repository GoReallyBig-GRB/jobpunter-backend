from datetime import datetime, timezone
from app.domain.candidate.errors import InvalidEvidenceTransition
from app.domain.candidate.states import can_transition

def transition_evidence(evidence, target_status: str, *, now: datetime | None = None):
    if not can_transition(evidence.status, target_status):
        raise InvalidEvidenceTransition(f"Cannot transition evidence from {evidence.status} to {target_status}")
    evidence.status = target_status
    evidence.revision += 1
    evidence.updated_at = now or datetime.now(timezone.utc)
    if target_status == "APPROVED":
        evidence.verified_at = evidence.updated_at
    return evidence
