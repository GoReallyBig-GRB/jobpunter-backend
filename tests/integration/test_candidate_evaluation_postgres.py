import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.infrastructure.db.models import (
    CandidateEvaluation, CandidateProfile, Evidence, EvidenceSource, Job,
    JobDescriptionVersion, User,
)
from app.infrastructure.db.session import get_db
from app.main import app
from app.ports.identity import AuthenticatedPrincipal


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


def make_user(db, label):
    user = User(display_name=f"{label} {uuid4()}")
    db.add(user)
    db.flush()
    profile = CandidateProfile(user_id=user.id, status="ACTIVE", revision=1)
    db.add(profile)
    db.flush()
    return user, profile


def test_evaluation_api_is_candidate_specific_and_persists_provenance(db):
    user_a, profile_a = make_user(db, "Candidate A")
    user_b, profile_b = make_user(db, "Candidate B")
    now = datetime.now(timezone.utc)
    job = Job(
        canonical_title="Technical Product Manager",
        lifecycle_status="OPEN",
        freshness_status="CURRENT",
        metadata_json={},
        location_restrictions={},
    )
    db.add(job)
    db.flush()
    jd_text = (
        "Required experience with product roadmaps, API integrations, SQL data models, "
        "technical requirements, agile delivery, engineering teams, stakeholder management, "
        "release planning, and cross-functional software development. " * 3
    )
    jd = JobDescriptionVersion(
        job_id=job.id,
        version_number=1,
        content=jd_text,
        content_hash="test-job-description-hash",
        completeness_status="COMPLETE",
        retrieved_at=now,
        requirement_extraction_status="COMPLETE",
    )
    source = EvidenceSource(source_type="TEST", source_reference="test://candidate-evidence")
    db.add_all([jd, source])
    db.flush()
    db.add_all([
        Evidence(
            candidate_profile_id=profile_a.id,
            evidence_source_id=source.id,
            status="APPROVED",
            evidence_type="PROJECT",
            content={"summary": "Technical product manager delivered product roadmaps API integrations SQL data models technical requirements agile delivery engineering teams stakeholder management release planning"},
        ),
        Evidence(
            candidate_profile_id=profile_b.id,
            evidence_source_id=source.id,
            status="APPROVED",
            evidence_type="PROJECT",
            content={"summary": "Retail cashier managed checkout lines customer service store inventory and daily till reconciliation"},
        ),
    ])
    db.flush()
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
        first = client.post(
            "/api/v1/candidate/evaluations",
            json={"job_id": str(job.id), "target_role_key": "technical-product-manager"},
        )
        assert first.status_code == 201, first.text
        first_data = first.json()["data"]
        assert first_data["candidate_profile_revision"] == 1
        assert first_data["job_description_version_id"] == str(jd.id)
        assert first_data["input_snapshot"]["job_description_content_hash"] == jd.content_hash
        assert first_data["preferences_revision"] == 0

        app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
            user_id=user_b.id, auth_provider="test"
        )
        second = client.post(
            "/api/v1/candidate/evaluations",
            json={"job_id": str(job.id), "target_role_key": "technical-product-manager"},
        )
        assert second.status_code == 201, second.text
        second_data = second.json()["data"]
        assert first_data["score"] > second_data["score"]
        assert first_data["evidence_summary"]["matched_terms"] != second_data["evidence_summary"]["matched_terms"]

        evaluations = list(db.scalars(select(CandidateEvaluation)).all())
        assert len(evaluations) == 2
        assert {row.candidate_profile_id for row in evaluations} == {profile_a.id, profile_b.id}

        app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
            user_id=user_a.id, auth_provider="test"
        )
        listed = client.get("/api/v1/candidate/evaluations")
        assert listed.status_code == 200
        assert len(listed.json()["data"]) == 1
        assert listed.json()["data"][0]["id"] == first_data["id"]
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()
