import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.infrastructure.db.session import get_db
from app.infrastructure.db.models import User
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

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        profile_response = client.get("/api/v1/candidate/profile")
        assert profile_response.status_code == 200
        profile_id = profile_response.json()["data"]["id"]

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
