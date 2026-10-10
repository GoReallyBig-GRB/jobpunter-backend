import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import DomainError
from app.application.candidates.schemas import CandidateClaimCreate, EvidenceCreate
from app.application.candidates.service import CandidateService
from app.infrastructure.db.models import (
    CandidateClaim, CandidateClaimEvidence, CandidateProfile, Evidence, EvidenceSource, User,
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



def test_expired_or_future_effective_evidence_cannot_support_claim_approval(db):
    user = make_user(db)
    service = CandidateService(db)
    now = datetime.now(timezone.utc)
    validity_cases = [
        ("expires_at", now - timedelta(days=1)),
        ("effective_from", now + timedelta(days=1)),
    ]

    for field_name, invalid_value in validity_cases:
        evidence = service.create_evidence(user.id, evidence_payload())
        service.transition_evidence(user.id, evidence.id, "PENDING_REVIEW", expected_revision=1)
        persisted_evidence = db.get(Evidence, evidence.id)
        setattr(persisted_evidence, field_name, invalid_value)
        db.commit()
        service.transition_evidence(user.id, evidence.id, "APPROVED", expected_revision=2)

        claim = service.create_claim(user.id, claim_payload([evidence.id]))
        claim = service.transition_claim(user.id, claim.id, "PENDING_REVIEW", expected_revision=1)
        with pytest.raises(DomainError) as error:
            service.transition_claim(user.id, claim.id, "APPROVED", expected_revision=2)
        assert error.value.code == "UNSUPPORTED_CLAIM"
        db.refresh(claim)
        assert claim.status == "PENDING_REVIEW"
        assert claim.revision == 2


def test_concurrent_claim_approval_allows_only_one_revision_winner():
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=6)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    user_id = profile_id = claim_id = None
    try:
        with SessionLocal() as seed_db:
            user = make_user(seed_db)
            service = CandidateService(seed_db)
            evidence = service.create_evidence(user.id, evidence_payload())
            service.transition_evidence(user.id, evidence.id, "PENDING_REVIEW", expected_revision=1)
            service.transition_evidence(user.id, evidence.id, "APPROVED", expected_revision=2)
            claim = service.create_claim(user.id, claim_payload([evidence.id]))
            claim = service.transition_claim(user.id, claim.id, "PENDING_REVIEW", expected_revision=1)
            seed_db.commit()
            user_id, profile_id, claim_id = user.id, claim.candidate_profile_id, claim.id

        barrier = Barrier(2)

        def approve():
            with SessionLocal() as session:
                barrier.wait(timeout=10)
                try:
                    claim = CandidateService(session).transition_claim(
                        user_id, claim_id, "APPROVED", expected_revision=2,
                    )
                    return ("WON", claim.status, claim.revision)
                except DomainError as error:
                    if error.code == "REVISION_CONFLICT":
                        return ("CONFLICT", None, None)
                    raise

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(approve), executor.submit(approve)]
            results = [future.result(timeout=20) for future in futures]

        assert sum(result[0] == "WON" for result in results) == 1
        assert sum(result[0] == "CONFLICT" for result in results) == 1
        with SessionLocal() as verify_db:
            claim = verify_db.get(CandidateClaim, claim_id)
            assert claim.status == "APPROVED"
            assert claim.revision == 3
            links = verify_db.scalars(select(CandidateClaimEvidence).where(
                CandidateClaimEvidence.candidate_claim_id == claim_id,
            )).all()
            assert len(links) == 1
    finally:
        if claim_id is not None:
            with SessionLocal() as cleanup:
                evidence_ids = list(cleanup.scalars(select(Evidence.id).where(
                    Evidence.candidate_profile_id == profile_id,
                )).all())
                source_ids = list(cleanup.scalars(select(Evidence.evidence_source_id).where(
                    Evidence.id.in_(evidence_ids),
                )).all()) if evidence_ids else []
                cleanup.execute(delete(CandidateClaimEvidence).where(
                    CandidateClaimEvidence.candidate_claim_id == claim_id,
                ))
                cleanup.execute(delete(CandidateClaim).where(CandidateClaim.id == claim_id))
                if evidence_ids:
                    cleanup.execute(delete(Evidence).where(Evidence.id.in_(evidence_ids)))
                if source_ids:
                    cleanup.execute(delete(EvidenceSource).where(EvidenceSource.id.in_(source_ids)))
                cleanup.execute(delete(CandidateProfile).where(CandidateProfile.id == profile_id))
                cleanup.execute(delete(User).where(User.id == user_id))
                cleanup.commit()
        engine.dispose()
