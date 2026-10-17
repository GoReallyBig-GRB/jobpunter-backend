import os
import asyncio
from datetime import datetime, timezone
from uuid import uuid4, UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.api.errors import DomainError
from app.application.opportunities.evaluation_service import CandidateEvaluationService
from app.infrastructure.db.models import Job, JobDescriptionVersion, JobProvenance, Source, User
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
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def client_for(db, user):
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=user.id, auth_provider="test"
    )
    connection = db.connection()

    def override_db():
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def create_user(db, label):
    user = User(display_name=f"{label} {uuid4()}")
    db.add(user)
    db.flush()
    return user


def create_source(db, *, source_type, permission="APPROVED", operational="AVAILABLE"):
    now = datetime.now(timezone.utc)
    source = Source(
        name=f"Source {uuid4()}",
        source_type=source_type,
        status="ACTIVE",
        permission_review_status=permission,
        permission_review_reference="policy-review://job-pool-test" if permission == "APPROVED" else None,
        permission_reviewed_at=now if permission == "APPROVED" else None,
        operational_state=operational,
    )
    db.add(source)
    db.flush()
    return source


def create_job_with_provenance(db, source, *, owner_id=None, title="Backend Engineer"):
    now = datetime.now(timezone.utc)
    job = Job(
        identity_status="CANONICAL",
        lifecycle_status="ACTIVE",
        freshness_status="FRESH",
        canonical_title=title,
        company_name="Example Employer",
        application_url="https://jobs.example.test/apply",
        location_country="Nigeria",
        work_mode="REMOTE",
        location_restrictions={},
        metadata_json={},
        first_discovered_at=now,
        last_observed_at=now,
        last_verified_at=now,
    )
    db.add(job)
    db.flush()
    db.add(JobProvenance(
        job_id=job.id,
        source_id=source.id,
        raw_source_record_id=None,
        submitted_by_user_id=owner_id,
        source_url="https://jobs.example.test/role",
        normalized_source_url="https://jobs.example.test/role",
        observation_type="ACQUIRED",
        asserted_by="SOURCE",
        observed_at=now,
        confidence=1.0,
        status="OBSERVED",
        metadata_json={},
    ))
    db.add(JobDescriptionVersion(
        job_id=job.id,
        version_number=1,
        content=("Responsibilities include product delivery and team coordination. "
                 "Requirements include relevant experience and stakeholder management. ") * 3,
        content_hash=f"job-pool-{uuid4()}",
        completeness_status="COMPLETE",
        retrieved_at=now,
        requirement_extraction_status="PENDING",
    ))
    db.flush()
    return job


def test_pool_lists_only_publicly_approved_jobs_and_candidates_own_manual_submissions(db):
    owner = create_user(db, "Owner")
    other = create_user(db, "Other")
    public_source = create_source(db, source_type="GREENHOUSE_JOB_BOARD")
    pending_source = create_source(
        db, source_type="GREENHOUSE_JOB_BOARD", permission="PENDING", operational="UNAVAILABLE"
    )
    unavailable_source = create_source(
        db, source_type="GREENHOUSE_JOB_BOARD", permission="APPROVED", operational="UNAVAILABLE"
    )
    manual_source = create_source(
        db, source_type="MANUAL_SUBMISSION", permission="PENDING", operational="AVAILABLE"
    )
    public_job = create_job_with_provenance(db, public_source, title="Public Product Manager")
    blocked_job = create_job_with_provenance(db, pending_source, title="Unapproved Product Manager")
    unavailable_source_job = create_job_with_provenance(
        db, unavailable_source, title="Approved But Unobserved Product Manager"
    )
    private_job = create_job_with_provenance(
        db, manual_source, owner_id=owner.id, title="Private Submitted Role"
    )
    db.commit()

    owner_client = client_for(db, owner)
    try:
        owner_response = owner_client.get("/api/v1/candidate/jobs/pool")
        assert owner_response.status_code == 200, owner_response.text
        owner_items = {item["id"]: item for item in owner_response.json()["data"]}
        assert str(public_job.id) in owner_items
        assert str(private_job.id) in owner_items
        assert str(blocked_job.id) not in owner_items
        assert str(unavailable_source_job.id) in owner_items
        assert owner_items[str(unavailable_source_job.id)]["provenance"][0]["source_operational_state"] == "UNAVAILABLE"
        owner_detail = owner_client.get(f"/api/v1/candidate/jobs/pool/{private_job.id}")
        assert owner_detail.status_code == 200, owner_detail.text
        assert owner_detail.json()["data"]["description"]["content"]
    finally:
        owner_client.close()

    # FastAPI dependency overrides are app-global, so switch the principal only after
    # the owner's requests have completed.
    other_client = client_for(db, other)
    try:
        other_response = other_client.get("/api/v1/candidate/jobs/pool")
        assert other_response.status_code == 200, other_response.text
        other_ids = {item["id"] for item in other_response.json()["data"]}
        assert str(public_job.id) in other_ids
        assert str(private_job.id) not in other_ids
        assert str(blocked_job.id) not in other_ids
        assert str(unavailable_source_job.id) in other_ids

        hidden_detail = other_client.get(f"/api/v1/candidate/jobs/pool/{private_job.id}")
        assert hidden_detail.status_code == 404
    finally:
        other_client.close()


def test_pool_filters_search_and_pagination_are_consistent(db):
    user = create_user(db, "Pool search")
    source = create_source(db, source_type="GREENHOUSE_JOB_BOARD")
    create_job_with_provenance(db, source, title="Technical Product Manager")
    create_job_with_provenance(db, source, title="Backend Engineer")
    db.commit()
    client = client_for(db, user)
    try:
        response = client.get(
            "/api/v1/candidate/jobs/pool",
            params={"query": "product", "limit": 1, "offset": 0},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["pagination"] == {"limit": 1, "offset": 0, "total": 1}
        assert len(payload["data"]) == 1
        assert payload["data"][0]["title"] == "Technical Product Manager"
        assert len(payload["data"][0]["description"]["preview"]) <= 320
    finally:
        client.close()


def test_manual_submission_provenance_is_not_public_even_if_source_is_approved(db):
    owner = create_user(db, "Manual owner")
    other = create_user(db, "Manual other")
    manual_source = create_source(
        db, source_type="MANUAL_SUBMISSION", permission="APPROVED", operational="AVAILABLE"
    )
    private_job = create_job_with_provenance(
        db, manual_source, owner_id=owner.id, title="Manual only role"
    )
    db.commit()
    client = client_for(db, other)
    try:
        response = client.get("/api/v1/candidate/jobs/pool")
        assert response.status_code == 200, response.text
        assert str(private_job.id) not in {item["id"] for item in response.json()["data"]}
        assert client.get(f"/api/v1/candidate/jobs/pool/{private_job.id}").status_code == 404
    finally:
        client.close()


def test_other_candidate_cannot_evaluate_private_manual_submission(db):
    owner = create_user(db, "Private job owner")
    other = create_user(db, "Private job outsider")
    manual_source = create_source(
        db, source_type="MANUAL_SUBMISSION", permission="PENDING", operational="AVAILABLE"
    )
    private_job = create_job_with_provenance(
        db, manual_source, owner_id=owner.id, title="Owner-only Manual Role"
    )
    db.commit()

    with pytest.raises(DomainError) as exc:
        asyncio.run(CandidateEvaluationService(db).evaluate(
            other.id, private_job.id, "technical-product-manager"
        ))
    assert exc.value.code == "JOB_NOT_FOUND"
    assert exc.value.status_code == 404
