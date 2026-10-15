import os
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.infrastructure.db.session import get_db
from app.infrastructure.db.models import AuditEvent, User
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


def install_client(db, user):
    connection = db.connection()

    def override_db():
        request_session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=user.id, auth_provider="test"
    )
    return TestClient(app)


def test_evidence_correction_is_revisioned_audited_and_invalidates_pending_claim_review(db):
    owner = make_user(db)
    client = install_client(db, owner)
    try:
        created = client.post(
            "/api/v1/candidate/evidence",
            json={
                "evidence_type": "employment_record",
                "content": {"role": "Analyst", "employer": "Example Co"},
                "source_type": "candidate_upload",
                "source_reference": "test://evidence/correction",
                "source_metadata": {"filename": "employment.pdf"},
            },
        )
        assert created.status_code == 201
        evidence_id = created.json()["data"]["id"]

        claim = client.post(
            "/api/v1/candidate/claims",
            json={
                "claim_type": "employment_experience",
                "claim_content": {"role": "Analyst", "employer": "Example Co"},
                "evidence_ids": [evidence_id],
            },
        )
        assert claim.status_code == 201
        claim_id = claim.json()["data"]["id"]
        submitted = client.post(
            f"/api/v1/candidate/claims/{claim_id}/submit",
            json={"expected_revision": 1},
        )
        assert submitted.status_code == 200
        assert submitted.json()["data"]["status"] == "PENDING_REVIEW"

        corrected = client.patch(
            f"/api/v1/candidate/evidence/{evidence_id}",
            json={
                "expected_revision": 1,
                "reason": "Corrected role wording from the source document",
                "content": {"role": "Senior Analyst", "employer": "Example Co"},
            },
        )
        assert corrected.status_code == 200, corrected.text
        corrected_data = corrected.json()
        assert corrected_data["revision"] == 2
        assert corrected_data["content"]["role"] == "Senior Analyst"
        assert corrected_data["source"]["source_reference"] == "test://evidence/correction"
        assert corrected_data["linked_claims"][0]["claim_status"] == "DRAFT"

        stale = client.patch(
            f"/api/v1/candidate/evidence/{evidence_id}",
            json={
                "expected_revision": 1,
                "reason": "Stale edit",
                "content": {"role": "Stale"},
            },
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"

        events = db.query(AuditEvent).filter(
            AuditEvent.resource_type == "evidence",
            AuditEvent.resource_id == UUID(evidence_id),
            AuditEvent.event_type == "EVIDENCE_UPDATED",
        ).all()
        assert len(events) == 1
        assert events[0].actor_id == owner.id
        assert events[0].reason == "Corrected role wording from the source document"
        assert events[0].details["changed_fields"] == ["content"]
        assert events[0].details["previous_revision"] == 1
        assert events[0].details["revision"] == 2

        claim_events = db.query(AuditEvent).filter(
            AuditEvent.resource_type == "candidate_claim",
            AuditEvent.resource_id == UUID(claim_id),
            AuditEvent.event_type == "CLAIM_REVIEW_INVALIDATED_BY_EVIDENCE_UPDATE",
        ).all()
        assert len(claim_events) == 1
        assert claim_events[0].to_status == "DRAFT"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)


def test_approved_evidence_cannot_be_edited(db):
    owner = make_user(db)
    client = install_client(db, owner)
    try:
        created = client.post(
            "/api/v1/candidate/evidence",
            json={
                "evidence_type": "employment_record",
                "content": {"role": "Analyst"},
                "source_type": "candidate_upload",
                "source_reference": "test://evidence/approved",
            },
        )
        assert created.status_code == 201
        evidence_id = created.json()["data"]["id"]
        submitted = client.post(
            f"/api/v1/candidate/evidence/{evidence_id}/submit",
            json={"expected_revision": 1},
        )
        assert submitted.status_code == 200
        approved = client.post(
            f"/api/v1/candidate/evidence/{evidence_id}/approve",
            json={"expected_revision": 2},
        )
        assert approved.status_code == 200

        correction = client.patch(
            f"/api/v1/candidate/evidence/{evidence_id}",
            json={
                "expected_revision": 3,
                "reason": "Attempt to change approved evidence",
                "content": {"role": "Senior Analyst"},
            },
        )
        assert correction.status_code == 409
        assert correction.json()["error"]["code"] == "EVIDENCE_NOT_EDITABLE"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)


def test_date_correction_validates_against_existing_date_fields(db):
    owner = make_user(db)
    client = install_client(db, owner)
    try:
        created = client.post(
            "/api/v1/candidate/evidence",
            json={
                "evidence_type": "employment_record",
                "content": {"role": "Analyst"},
                "source_type": "candidate_upload",
                "source_reference": "test://evidence/date-range",
            },
        )
        assert created.status_code == 201
        evidence_id = created.json()["data"]["id"]

        first_correction = client.patch(
            f"/api/v1/candidate/evidence/{evidence_id}",
            json={
                "expected_revision": 1,
                "reason": "Record the end of the evidence period",
                "effective_to": "2026-01-01T00:00:00Z",
            },
        )
        assert first_correction.status_code == 200, first_correction.text

        invalid_correction = client.patch(
            f"/api/v1/candidate/evidence/{evidence_id}",
            json={
                "expected_revision": 2,
                "reason": "Attempt an invalid effective date",
                "effective_from": "2026-02-01T00:00:00Z",
            },
        )
        assert invalid_correction.status_code == 422
        assert invalid_correction.json()["error"]["code"] == "INVALID_EVIDENCE_DATES"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
