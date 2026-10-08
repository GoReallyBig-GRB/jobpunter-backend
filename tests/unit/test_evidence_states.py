import pytest
from types import SimpleNamespace
from app.domain.candidate.service import transition_evidence
from app.domain.candidate.errors import InvalidEvidenceTransition

def test_pending_review_can_be_approved():
    e = SimpleNamespace(status="PENDING_REVIEW", revision=1, verified_at=None, updated_at=None)
    transition_evidence(e, "APPROVED")
    assert e.status == "APPROVED"
    assert e.revision == 2
    assert e.verified_at is not None

def test_draft_cannot_be_approved_directly():
    e = SimpleNamespace(status="DRAFT", revision=1, verified_at=None, updated_at=None)
    with pytest.raises(InvalidEvidenceTransition):
        transition_evidence(e, "APPROVED")
