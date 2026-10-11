import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.application.candidates.schemas import EvidenceCreate
from app.application.candidates.service import CandidateService
from app.infrastructure.db.models import User
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


def make_user(db):
    user = User(display_name=f"Candidate {uuid4()}")
    db.add(user)
    db.flush()
    return user


def make_evidence(service, user_id, label):
    return service.create_evidence(
        user_id,
        EvidenceCreate(
            evidence_type="employment_record",
            content={"role": "Product Manager", "organization": label},
            source_type="candidate_upload",
            source_reference=f"test://claim-api/{uuid4()}",
            source_metadata={"test": True},
        ),
    )


def client_for(db, initial_user_id):
    connection = db.connection()
    principal = {"user_id": initial_user_id}

    def override_db():
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=principal["user_id"], auth_provider="test"
    )
    client = TestClient(app)
    return client, principal


def test_claim_api_lifecycle_approves_only_with_approved_evidence(db):
    user = make_user(db)
    service = CandidateService(db)
    evidence = make_evidence(service, user.id, "Supported Example Ltd")
    service.transition_evidence(user.id, evidence.id, "PENDING_REVIEW", expected_revision=1)
    service.transition_evidence(user.id, evidence.id, "APPROVED", expected_revision=2)

    client, _principal = client_for(db, user.id)
    try:
        created = client.post("/api/v1/candidate/claims", json={
            "claim_type": "employment_experience",
            "claim_content": {"employer": "Supported Example Ltd", "role": "Product Manager"},
            "evidence_ids": [str(evidence.id)],
        })
        assert created.status_code == 201, created.text
        claim = created.json()["data"]
        assert claim["status"] == "DRAFT"
        assert claim["evidence_ids"] == [str(evidence.id)]

        submitted = client.post(
            f"/api/v1/candidate/claims/{claim['id']}/submit",
            json={"expected_revision": 1},
        )
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["data"]["status"] == "PENDING_REVIEW"

        approved = client.post(
            f"/api/v1/candidate/claims/{claim['id']}/approve",
            json={"expected_revision": 2},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["data"]["status"] == "APPROVED"

        listed = client.get("/api/v1/candidate/claims")
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()["data"]] == [claim["id"]]
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()


def test_claim_api_rejects_unsupported_claim_and_isolates_candidates(db):
    owner = make_user(db)
    other_user = make_user(db)
    service = CandidateService(db)
    draft_evidence = make_evidence(service, owner.id, "Unverified Example Ltd")
    client, principal = client_for(db, owner.id)
    try:
        created = client.post("/api/v1/candidate/claims", json={
            "claim_type": "employment_experience",
            "claim_content": {"employer": "Unverified Example Ltd", "role": "Product Manager"},
            "evidence_ids": [str(draft_evidence.id)],
        })
        assert created.status_code == 201, created.text
        claim_id = created.json()["data"]["id"]

        submitted = client.post(
            f"/api/v1/candidate/claims/{claim_id}/submit",
            json={"expected_revision": 1},
        )
        assert submitted.status_code == 200, submitted.text
        rejected_approval = client.post(
            f"/api/v1/candidate/claims/{claim_id}/approve",
            json={"expected_revision": 2},
        )
        assert rejected_approval.status_code == 409
        assert rejected_approval.json()["error"]["code"] == "UNSUPPORTED_CLAIM"

        principal["user_id"] = other_user.id
        listed = client.get("/api/v1/candidate/claims")
        assert listed.status_code == 200
        assert listed.json()["data"] == []

        cross_candidate_claim = client.post("/api/v1/candidate/claims", json={
            "claim_type": "employment_experience",
            "claim_content": {"employer": "Unverified Example Ltd"},
            "evidence_ids": [str(draft_evidence.id)],
        })
        assert cross_candidate_claim.status_code == 404
        assert cross_candidate_claim.json()["error"]["code"] == "EVIDENCE_NOT_FOUND"

        cross_candidate_transition = client.post(
            f"/api/v1/candidate/claims/{claim_id}/submit",
            json={"expected_revision": 2},
        )
        assert cross_candidate_transition.status_code == 404
        assert cross_candidate_transition.json()["error"]["code"] == "NOT_FOUND"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()
