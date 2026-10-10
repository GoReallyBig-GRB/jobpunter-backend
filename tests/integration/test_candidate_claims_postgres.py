import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.candidates.schemas import CandidateClaimCreate, EvidenceCreate
from app.application.candidates.service import CandidateService
from app.infrastructure.db.models import (
    CandidateClaim, CandidateClaimEvidence, CandidateProfile, Evidence, User,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


@pytest.fixture
def db():
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def make_user(db):
    user = User(display_name=f"Candidate {uuid4()}")
    db.add(user)
    db.flush()
    return user


def evidence_payload():
    return EvidenceCreate(
        evidence_type="employment_record",
        content={"role": "Product Manager", "organization": "Example Ltd"},
        source_type="candidate_upload",
        source_reference=f"test://claim-evidence/{uuid4()}",
        source_metadata={"test": True},
    )


def claim_payload(evidence_ids=None):
    return CandidateClaimCreate(
        claim_type="employment_experience",
        claim_content={"employer": "Example Ltd", "role": "Product Manager", "years": 4},
        evidence_ids=evidence_ids or [],
    )


def test_claim_without_evidence_cannot_be_submitted_for_review(db):
    user = make_user(db)
    service = CandidateService(db)
    claim = service.create_claim(user.id, claim_payload())

    assert claim.status == "DRAFT"
    assert claim.revision == 1
    with pytest.raises(DomainError) as error:
        service.transition_claim(user.id, claim.id, "PENDING_REVIEW", expected_revision=1)

    assert error.value.code == "CLAIM_EVIDENCE_REQUIRED"
    db.refresh(claim)
    assert claim.status == "DRAFT"
    assert claim.revision == 1


def test_claim_approval_requires_current_approved_supporting_evidence(db):
    user = make_user(db)
    service = CandidateService(db)
    evidence = service.create_evidence(user.id, evidence_payload())
    claim = service.create_claim(user.id, claim_payload([evidence.id]))

    claim = service.transition_claim(user.id, claim.id, "PENDING_REVIEW", expected_revision=1)
    assert claim.status == "PENDING_REVIEW"
    assert claim.revision == 2

    with pytest.raises(DomainError) as error:
        service.transition_claim(user.id, claim.id, "APPROVED", expected_revision=2)
    assert error.value.code == "UNSUPPORTED_CLAIM"
    db.refresh(claim)
    assert claim.status == "PENDING_REVIEW"
    assert claim.revision == 2

    service.transition_evidence(user.id, evidence.id, "PENDING_REVIEW", expected_revision=1)
    approved_evidence = service.transition_evidence(
        user.id, evidence.id, "APPROVED", expected_revision=2,
    )
    assert approved_evidence.status == "APPROVED"
    claim = service.transition_claim(user.id, claim.id, "APPROVED", expected_revision=2)

    assert claim.status == "APPROVED"
    assert claim.revision == 3
    assert db.scalar(select(CandidateClaimEvidence).where(
        CandidateClaimEvidence.candidate_claim_id == claim.id,
        CandidateClaimEvidence.evidence_id == evidence.id,
    )) is not None


def test_claim_cannot_link_evidence_owned_by_another_candidate(db):
    owner = make_user(db)
    other_user = make_user(db)
    service = CandidateService(db)
    evidence = service.create_evidence(owner.id, evidence_payload())

    with pytest.raises(DomainError) as error:
        service.create_claim(other_user.id, claim_payload([evidence.id]))

    assert error.value.code == "EVIDENCE_NOT_FOUND"
    other_profile = db.scalar(select(CandidateProfile).where(
        CandidateProfile.user_id == other_user.id,
    ))
    assert other_profile is not None
    assert db.scalar(select(CandidateClaim).where(
        CandidateClaim.candidate_profile_id == other_profile.id,
    )) is None
