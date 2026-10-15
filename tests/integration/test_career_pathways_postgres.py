"""PostgreSQL persistence and isolation tests for career pathway records."""
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import CareerPathway, CareerPathwayEvent, CandidateProfile, User

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


def _candidate(db, name):
    user = User(display_name=name)
    db.add(user)
    db.flush()
    profile = CandidateProfile(user_id=user.id, status="ACTIVE")
    db.add(profile)
    db.flush()
    return user, profile


def test_career_pathway_persists_candidate_scope_and_review_history(db):
    owner, owner_profile = _candidate(db, "Pathway owner")
    other, other_profile = _candidate(db, "Other candidate")
    now = datetime.now(timezone.utc)
    pathway = CareerPathway(
        candidate_profile_id=owner_profile.id,
        title="Technical Product Management",
        role_family="Product Management",
        target_roles=["Technical Product Manager", "Platform Product Manager"],
        target_seniority=["Senior", "Lead"],
        domains=["B2B SaaS", "Developer Tools"],
        supporting_evidence_refs=[],
        gaps=["More direct platform roadmap evidence"],
        rationale="Candidate-defined pathway.",
        confidence=None,
        source_type="USER_DEFINED",
        approval_status="APPROVED",
        approved_at=now,
        approved_by_user_id=owner.id,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(pathway)
    db.flush()
    event = CareerPathwayEvent(
        career_pathway_id=pathway.id,
        candidate_profile_id=owner_profile.id,
        actor_user_id=owner.id,
        event_type="CREATED",
        previous_status=None,
        new_status="APPROVED",
        pathway_revision=1,
        reason="Candidate created this pathway",
        details={"source_type": "USER_DEFINED"},
        created_at=now,
    )
    db.add(event)
    db.commit()
    pathway_id = pathway.id
    db.expire_all()

    saved = db.get(CareerPathway, pathway_id)
    assert saved is not None
    assert saved.candidate_profile_id == owner_profile.id
    assert saved.source_type == "USER_DEFINED"
    assert saved.approval_status == "APPROVED"
    assert saved.target_roles == ["Technical Product Manager", "Platform Product Manager"]
    assert saved.supporting_evidence_refs == []
    assert saved.approved_by_user_id == owner.id

    history = db.scalars(
        select(CareerPathwayEvent)
        .where(CareerPathwayEvent.career_pathway_id == saved.id)
        .order_by(CareerPathwayEvent.created_at, CareerPathwayEvent.id)
    ).all()
    assert len(history) == 1
    assert history[0].actor_user_id == owner.id
    assert history[0].details["source_type"] == "USER_DEFINED"

    # The pathway's owner boundary is represented by its profile FK; a different
    # candidate's profile cannot resolve this row through a candidate-scoped query.
    other_rows = db.scalars(
        select(CareerPathway).where(
            CareerPathway.candidate_profile_id == other_profile.id,
            CareerPathway.id == saved.id,
        )
    ).all()
    assert other_rows == []
    assert other.id != owner.id
