import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.opportunities.service import OpportunityService
from app.infrastructure.db.models import (
    Application,
    CandidateEvaluation,
    CandidateProfile,
    Job,
    Punt,
    User,
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


def candidate(db):
    user = User(display_name=f"Candidate {uuid4()}")
    db.add(user)
    db.flush()
    profile = CandidateProfile(user_id=user.id, status="ACTIVE")
    db.add(profile)
    db.flush()
    return user, profile


def job(db, title):
    record = Job(canonical_title=title, lifecycle_status="OPEN", freshness_status="FRESH")
    db.add(record)
    db.flush()
    return record


def evaluation(db, profile, job_record, *, outcome="APPLY", validity="CURRENT", blockers=None):
    record = CandidateEvaluation(
        candidate_profile_id=profile.id,
        job_id=job_record.id,
        target_role_key="technical-product-manager",
        evaluation_version=1,
        validity_status=validity,
        outcome=outcome,
        score=85,
        hard_blockers=blockers or [],
        evidence_summary={},
        explanation={},
        policy_version="test-v1",
        evaluated_at=datetime.now(timezone.utc),
    )
    db.add(record)
    db.flush()
    return record


def test_candidate_cannot_create_punt_from_another_candidates_evaluation(db):
    user_a, _ = candidate(db)
    _, profile_b = candidate(db)
    job_record = job(db, "Technical Product Manager")
    foreign_evaluation = evaluation(db, profile_b, job_record)

    with pytest.raises(DomainError) as error:
        OpportunityService(db).create_punt(user_a.id, foreign_evaluation.id)

    assert error.value.status_code == 404
    assert db.scalar(select(Punt)) is None


def test_candidate_cannot_access_another_candidates_application(db):
    user_a, _ = candidate(db)
    user_b, profile_b = candidate(db)
    job_record = job(db, "Product Manager")
    own_evaluation = evaluation(db, profile_b, job_record)
    service_b = OpportunityService(db)
    punt = service_b.create_punt(user_b.id, own_evaluation.id)
    application = service_b.create_application(user_b.id, punt.id)

    with pytest.raises(DomainError) as error:
        OpportunityService(db).transition_application(
            user_a.id, application.id, "APPROVED", expected_revision=application.revision
        )

    assert error.value.status_code == 404
    db.refresh(application)
    assert application.status == "PLANNED"
    assert db.scalar(select(Application).where(Application.id == application.id)) is not None


def test_repeated_application_creation_returns_single_candidate_job_application(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record)
    service = OpportunityService(db)
    punt = service.create_punt(user.id, ev.id)

    first = service.create_application(user.id, punt.id)
    second = service.create_application(user.id, punt.id)

    assert first.id == second.id
    assert len(db.scalars(select(Application).where(
        Application.candidate_profile_id == profile.id,
        Application.job_id == job_record.id,
    )).all()) == 1



def test_opportunity_api_denies_cross_candidate_ids_and_lists_only_owned_records(db):
    from fastapi.testclient import TestClient

    from app.api.deps import get_principal
    from app.infrastructure.db.session import get_db
    from app.main import app
    from app.ports.identity import AuthenticatedPrincipal

    user_a, _profile_a = candidate(db)
    user_b, profile_b = candidate(db)
    job_record = job(db, "Technical Product Manager")
    foreign_evaluation = evaluation(db, profile_b, job_record)
    service_b = OpportunityService(db)
    foreign_punt = service_b.create_punt(user_b.id, foreign_evaluation.id)
    foreign_application = service_b.create_application(user_b.id, foreign_punt.id)

    connection = db.connection()

    def override_db():
        request_session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=user_a.id, auth_provider="test"
    )
    client = TestClient(app)
    try:
        own_punts = client.get("/api/v1/candidate/punts")
        own_applications = client.get("/api/v1/candidate/applications")
        assert own_punts.status_code == 200
        assert own_punts.json()["data"] == []
        assert own_applications.status_code == 200
        assert own_applications.json()["data"] == []

        create_foreign_punt = client.post(
            "/api/v1/candidate/punts",
            json={"evaluation_id": str(foreign_evaluation.id)},
        )
        assert create_foreign_punt.status_code == 404

        create_foreign_application = client.post(
            "/api/v1/candidate/applications",
            json={"punt_id": str(foreign_punt.id)},
        )
        assert create_foreign_application.status_code == 404

        transition_foreign_application = client.post(
            f"/api/v1/candidate/applications/{foreign_application.id}/transition",
            json={"target_status": "APPROVED", "expected_revision": foreign_application.revision},
        )
        assert transition_foreign_application.status_code == 404

        db.refresh(foreign_application)
        assert foreign_application.status == "PLANNED"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()
