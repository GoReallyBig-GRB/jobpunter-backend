import pytest
from pydantic import ValidationError

from app.application.candidates.schemas import CandidateClaimCreate, EvidenceCreate


def test_evidence_requires_at_least_one_fact():
    with pytest.raises(ValidationError):
        EvidenceCreate(
            evidence_type="employment_record",
            content={},
            source_type="candidate_upload",
            source_reference="test://empty-evidence",
        )


def test_claim_requires_at_least_one_fact():
    with pytest.raises(ValidationError):
        CandidateClaimCreate(
            claim_type="employment_experience",
            claim_content={},
        )


def test_evidence_and_claim_accept_non_empty_structured_facts():
    evidence = EvidenceCreate(
        evidence_type="employment_record",
        content={"role": "Analyst"},
        source_type="candidate_upload",
        source_reference="test://evidence",
    )
    claim = CandidateClaimCreate(
        claim_type="employment_experience",
        claim_content={"role": "Analyst"},
    )

    assert evidence.content == {"role": "Analyst"}
    assert claim.claim_content == {"role": "Analyst"}
