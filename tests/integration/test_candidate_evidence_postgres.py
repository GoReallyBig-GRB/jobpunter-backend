import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.candidates.schemas import EvidenceCreate
from app.application.candidates.service import CandidateService
from app.infrastructure.db.models import Evidence, EvidenceSource, User


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
        source_reference=f"test://evidence/{uuid4()}",
        source_metadata={"test": True},
    )


def test_evidence_persists_and_completes_full_review_lifecycle(db):
    user = make_user(db)
    service = CandidateService(db)

    evidence = service.create_evidence(user.id, evidence_payload())
    assert evidence.status == "DRAFT"
    assert evidence.revision == 1
    assert db.scalar(select(EvidenceSource).where(
        EvidenceSource.id == evidence.evidence_source_id
    )) is not None

    evidence = service.transition_evidence(
        user.id, evidence.id, "PENDING_REVIEW", expected_revision=1
    )
    assert evidence.status == "PENDING_REVIEW"
    assert evidence.revision == 2

    evidence = service.transition_evidence(
        user.id, evidence.id, "APPROVED", expected_revision=2
    )
    assert evidence.status == "APPROVED"
    assert evidence.revision == 3
    assert evidence.verified_at is not None

    evidence = service.transition_evidence(
        user.id, evidence.id, "ARCHIVED", expected_revision=3
    )
    assert evidence.status == "ARCHIVED"
    assert evidence.revision == 4


def test_evidence_rejects_stale_revision_without_mutating_record(db):
    user = make_user(db)
    service = CandidateService(db)
    evidence = service.create_evidence(user.id, evidence_payload())
    service.transition_evidence(user.id, evidence.id, "PENDING_REVIEW", expected_revision=1)

    with pytest.raises(DomainError) as error:
        service.transition_evidence(
            user.id, evidence.id, "APPROVED", expected_revision=1
        )

    assert error.value.code == "REVISION_CONFLICT"
    assert error.value.status_code == 409
    db.refresh(evidence)
    assert evidence.status == "PENDING_REVIEW"
    assert evidence.revision == 2


def test_evidence_is_not_visible_or_mutable_by_another_candidate(db):
    owner = make_user(db)
    other_user = make_user(db)
    service = CandidateService(db)
    evidence = service.create_evidence(owner.id, evidence_payload())

    with pytest.raises(DomainError) as error:
        service.transition_evidence(
            other_user.id, evidence.id, "PENDING_REVIEW", expected_revision=1
        )

    assert error.value.code == "NOT_FOUND"
    assert error.value.status_code == 404
    db.refresh(evidence)
    assert evidence.status == "DRAFT"
    assert evidence.revision == 1
