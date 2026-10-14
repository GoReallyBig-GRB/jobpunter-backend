import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.infrastructure.db.session import get_db
from app.infrastructure.db.models import CandidateProfile, CandidateProfileFactProvenance, User
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


def make_user(db):
    user = User(display_name=f"Candidate {uuid4()}")
    db.add(user)
    db.flush()
    return user


def test_fastapi_candidate_evidence_journey_and_cross_user_denial(db):
    owner = make_user(db)
    other_user = make_user(db)
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=owner.id, auth_provider="test"
    )

    connection = db.connection()

    def override_db():
        request_session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        profile_response = client.get("/api/v1/candidate/profile")
        assert profile_response.status_code == 200
        profile_id = profile_response.json()["data"]["id"]
        persisted_profile = db.scalar(
            select(CandidateProfile).where(CandidateProfile.id == profile_id)
        )
        assert persisted_profile is not None
        assert persisted_profile.user_id == owner.id
        assert profile_response.json()["data"]["profile_data"]["career_history"] == []

        profile_update = client.patch(
            "/api/v1/candidate/profile",
            json={
                "expected_revision": 1,
                "sections": {
                    "identity": {"full_name": "Test Candidate"},
                    "career_history": [{"role": "Analyst", "employer": "Example Co"}],
                },
            },
        )
        assert profile_update.status_code == 200
        assert profile_update.json()["data"]["revision"] == 2

        provenance_response = client.get("/api/v1/candidate/profile/provenance")
        assert provenance_response.status_code == 200
        provenance = provenance_response.json()["data"]
        identity_fact = next(
            item for item in provenance
            if item["section"] == "identity" and item["fact_path"] == "full_name"
        )
        assert identity_fact["fact_value"] == "Test Candidate"
        assert identity_fact["source_type"] == "CANDIDATE_INPUT"
        assert identity_fact["profile_revision"] == 2
        assert identity_fact["confirmed_by_user_id"] == str(owner.id)
        persisted_provenance = db.scalar(
            select(CandidateProfileFactProvenance).where(
                CandidateProfileFactProvenance.candidate_profile_id == profile_id,
                CandidateProfileFactProvenance.section == "identity",
                CandidateProfileFactProvenance.fact_path == "full_name",
            )
        )
        assert persisted_provenance is not None

        partial_update = client.patch(
            "/api/v1/candidate/profile",
            json={
                "expected_revision": 2,
                "sections": {"skills_tools": [{"name": "SQL", "level": "working"}]},
            },
        )
        assert partial_update.status_code == 200
        profile_data = partial_update.json()["data"]["profile_data"]
        assert profile_data["identity"]["full_name"] == "Test Candidate"
        assert profile_data["career_history"][0]["role"] == "Analyst"
        assert profile_data["skills_tools"][0]["name"] == "SQL"

        # Profile sections are isolated by the authenticated candidate, not a client-supplied ID.
        app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
            user_id=other_user.id, auth_provider="test"
        )
        other_profile = client.get("/api/v1/candidate/profile")
        assert other_profile.status_code == 200
        assert other_profile.json()["data"]["profile_data"]["identity"] == {}
        other_provenance = client.get("/api/v1/candidate/profile/provenance")
        assert other_provenance.status_code == 200
        assert other_provenance.json()["data"] == []
        other_update = client.patch(
            "/api/v1/candidate/profile",
            json={"expected_revision": 1, "sections": {"identity": {"full_name": "Other Candidate"}}},
        )
        assert other_update.status_code == 200
        assert other_update.json()["data"]["profile_data"]["identity"]["full_name"] == "Other Candidate"

        app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
            user_id=owner.id, auth_provider="test"
        )
        owner_profile = client.get("/api/v1/candidate/profile")
        assert owner_profile.json()["data"]["profile_data"]["identity"]["full_name"] == "Test Candidate"

        stale_profile_update = client.patch(
            "/api/v1/candidate/profile",
            json={"expected_revision": 1, "sections": {"identity": {"full_name": "Stale"}}},
        )
        assert stale_profile_update.status_code == 409
        assert stale_profile_update.json()["error"]["code"] == "REVISION_CONFLICT"

        create_response = client.post(
            "/api/v1/candidate/evidence",
            json={
                "evidence_type": "employment_record",
                "content": {"role": "Product Manager"},
                "source_type": "candidate_upload",
                "source_reference": f"test://evidence/{uuid4()}",
                "source_metadata": {"test": True},
            },
        )
        assert create_response.status_code == 201
        created = create_response.json()["data"]
        assert created["candidate_profile_id"] == profile_id
        assert created["status"] == "DRAFT"
        assert created["revision"] == 1
        evidence_id = created["id"]

        submitted = client.post(
            f"/api/v1/candidate/evidence/{evidence_id}/submit",
            json={"expected_revision": 1},
        )
        assert submitted.status_code == 200
        assert submitted.json()["data"]["status"] == "PENDING_REVIEW"
        assert submitted.json()["data"]["revision"] == 2

        approved = client.post(
            f"/api/v1/candidate/evidence/{evidence_id}/approve",
            json={"expected_revision": 2},
        )
        assert approved.status_code == 200
        assert approved.json()["data"]["status"] == "APPROVED"
        assert approved.json()["data"]["revision"] == 3
        assert approved.json()["data"]["verified_at"] is not None

        stale = client.post(
            f"/api/v1/candidate/evidence/{evidence_id}/archive",
            json={"expected_revision": 2},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"

        app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
            user_id=other_user.id, auth_provider="test"
        )
        foreign = client.post(
            f"/api/v1/candidate/evidence/{evidence_id}/archive",
            json={"expected_revision": 3},
        )
        assert foreign.status_code == 404
        assert foreign.json()["error"]["code"] == "NOT_FOUND"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
