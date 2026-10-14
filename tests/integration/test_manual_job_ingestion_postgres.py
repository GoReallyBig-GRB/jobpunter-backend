import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.infrastructure.db.models import (
    AcquisitionRun, Job, JobDescriptionVersion, JobProvenance,
    RawSourceRecord, Source, User,
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
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def make_client(db, user):
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


def make_user(db):
    user = User(display_name=f"Manual ingestion test {uuid4()}")
    db.add(user)
    db.flush()
    return user


def test_manual_jd_uses_shared_canonical_pipeline_and_replays_idempotently(db):
    user = make_user(db)
    client = make_client(db, user)
    payload = {
        "channel": "MANUAL_JD",
        "title": "Technical Product Manager",
        "company": "Example Systems",
        "description": (
            "Responsibilities include leading roadmap planning, coordinating engineering delivery, "
            "defining requirements, managing stakeholders, and improving product workflows. "
            "Qualifications include product management experience, strong communication skills, "
            "technical fluency, and the ability to analyze customer needs and prioritize delivery."
        ),
        "application_url": "https://jobs.example.com/roles/123?utm_source=email",
        "external_job_id": "example-role-123",
        "location": "Remote, Nigeria",
        "application_fee_amount": 25,
        "application_fee_currency": "USD",
        "relocation_required": True,
        "posting_date": "2026-10-01T00:00:00Z",
    }
    key = f"manual-jd-{uuid4()}"
    try:
        first = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": key},
            json=payload,
        )
        assert first.status_code == 201, first.text
        first_data = first.json()["data"]
        assert first_data["acquisition_status"] == "SUCCEEDED"
        assert first_data["canonicalization"]["status"] == "CREATED"
        assert first_data["canonicalization"]["replayed"] is False

        db.expire_all()
        job_id = UUID(first_data["canonicalization"]["job_id"])
        job = db.get(Job, job_id)
        assert job is not None
        assert job.canonical_title == "Technical Product Manager"
        assert job.application_url == "https://jobs.example.com/roles/123"
        assert job.metadata_json["external_job_id"] == "example-role-123"
        assert job.metadata_json["posting_date"] == "2026-10-01T00:00:00Z"
        assert job.metadata_json["discovery_method"] == "MANUAL_JD"
        assert job.metadata_json["application_fee_amount"] == 25.0
        assert job.metadata_json["application_fee_currency"] == "USD"
        assert job.metadata_json["relocation_required"] is True
        assert db.scalar(select(func.count()).select_from(JobDescriptionVersion).where(
            JobDescriptionVersion.job_id == job_id
        )) == 1
        assert db.scalar(select(func.count()).select_from(JobProvenance).where(
            JobProvenance.job_id == job_id
        )) == 1

        replay = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": key},
            json=payload,
        )
        assert replay.status_code == 200, replay.text
        replay_data = replay.json()["data"]
        assert replay_data["idempotent_replay"] is True
        assert replay_data["canonicalization"]["replayed"] is True
        assert replay_data["canonicalization"]["job_id"] == str(job_id)
        assert db.scalar(select(func.count()).select_from(Job).where(Job.id == job_id)) == 1
        assert db.scalar(select(func.count()).select_from(JobDescriptionVersion).where(
            JobDescriptionVersion.job_id == job_id
        )) == 1
    finally:
        client.close()


def test_manual_url_requires_details_instead_of_claiming_url_fetch(db):
    user = make_user(db)
    client = make_client(db, user)
    try:
        response = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": f"url-only-{uuid4()}"},
            json={"channel": "MANUAL_URL", "source_url": "https://jobs.example.com/123"},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "MANUAL_DETAILS_REQUIRED"
    finally:
        client.close()


def test_idempotency_key_cannot_be_reused_for_different_job_content(db):
    user = make_user(db)
    client = make_client(db, user)
    key = f"manual-conflict-{uuid4()}"
    payload = {
        "channel": "MANUAL_JD",
        "title": "Product Manager",
        "company": "Example Systems",
        "description": "Responsibilities include planning and coordinating product delivery. Qualifications include relevant product experience and strong communication skills.",
    }
    try:
        first = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": key},
            json=payload,
        )
        assert first.status_code == 201, first.text
        changed = {**payload, "title": "Senior Product Manager"}
        conflict = client.post(
            "/api/v1/candidate/jobs/manual-ingestion",
            headers={"Idempotency-Key": key},
            json=changed,
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    finally:
        client.close()
