import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.infrastructure.db.session import get_db
from app.infrastructure.db.models import AuditEvent, CandidateProfileFactProvenance, Evidence, User
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


def test_evidence_library_list_detail_pagination_and_related_provenance(db):
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
        first = client.post(
            "/api/v1/candidate/evidence",
            json={
                "evidence_type": "employment_record",
                "content": {"role": "Analyst"},
                "source_type": "candidate_upload",
                "source_reference": "test://evidence/first",
                "source_metadata": {"filename": "first.pdf"},
            },
        )
        second = client.post(
            "/api/v1/candidate/evidence",
            json={
                "evidence_type": "certification",
                "content": {"name": "Example certification"},
                "source_type": "candidate_upload",
                "source_reference": "test://evidence/second",
                "source_metadata": {"filename": "second.pdf"},
            },
        )
        assert first.status_code == 201
        assert second.status_code == 201
        first_id = first.json()["data"]["id"]
        profile_id = first.json()["data"]["candidate_profile_id"]

        claim = client.post(
            "/api/v1/candidate/claims",
            json={
                "claim_type": "employment_experience",
                "claim_content": {"role": "Analyst"},
                "evidence_ids": [first_id],
            },
        )
        assert claim.status_code == 201

        db.add(CandidateProfileFactProvenance(
            candidate_profile_id=UUID(profile_id),
            section="career_history",
            fact_path="[0].role",
            fact_value="Analyst",
            profile_revision=2,
            source_type="EVIDENCE",
            source_reference=f"evidence:{first_id}",
            evidence_id=UUID(first_id),
            document_import_id=None,
            source_excerpt="Role: Analyst",
            confidence=0.98,
            confirmed_at=None,
            confirmed_by_user_id=None,
        ))
        db.flush()

        listing = client.get("/api/v1/candidate/evidence?limit=1&offset=0")
        assert listing.status_code == 200, listing.text
        listing_data = listing.json()
        assert listing_data["pagination"] == {"limit": 1, "offset": 0, "total": 2}
        assert len(listing_data["data"]) == 1
        assert listing_data["data"][0]["id"] in {first_id, second.json()["data"]["id"]}

        detail = client.get(f"/api/v1/candidate/evidence/{first_id}")
        assert detail.status_code == 200, detail.text
        item = detail.json()
        assert item["source"]["source_type"] == "candidate_upload"
        assert item["source"]["source_reference"] == "test://evidence/first"
        assert item["source"]["source_metadata"] == {"filename": "first.pdf"}
        assert item["verification_status"] == "UNVERIFIED"
        assert item["health_status"] == "UNVERIFIED"
        assert item["linked_claims"] == [{
            "claim_id": claim.json()["data"]["id"],
            "claim_type": "employment_experience",
            "claim_status": "DRAFT",
            "relationship_type": "SUPPORTS",
        }]
        assert len(item["linked_profile_facts"]) == 1
        assert item["linked_profile_facts"][0]["fact_path"] == "[0].role"
        assert item["linked_profile_facts"][0]["source_excerpt"] == "Role: Analyst"

        filtered = client.get("/api/v1/candidate/evidence?status=DRAFT&evidence_type=certification")
        assert filtered.status_code == 200
        assert filtered.json()["pagination"]["total"] == 1
        assert filtered.json()["data"][0]["evidence_type"] == "certification"

        app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
            user_id=other_user.id, auth_provider="test"
        )
        other_listing = client.get("/api/v1/candidate/evidence")
        assert other_listing.status_code == 200
        assert other_listing.json()["pagination"]["total"] == 0
        foreign_detail = client.get(f"/api/v1/candidate/evidence/{first_id}")
        assert foreign_detail.status_code == 404
        assert foreign_detail.json()["error"]["code"] == "NOT_FOUND"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)


def test_evidence_library_exposes_verified_and_expired_health(db):
    owner = make_user(db)
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
        evidence_ids = {}
        for label in ("current", "expired"):
            created = client.post(
                "/api/v1/candidate/evidence",
                json={
                    "evidence_type": "employment_record",
                    "content": {"role": "Product Manager", "case": label},
                    "source_type": "candidate_statement",
                    "source_reference": f"test://evidence-health/{label}",
                },
            )
            assert created.status_code == 201, created.text
            evidence_id = created.json()["data"]["id"]
            submitted = client.post(
                f"/api/v1/candidate/evidence/{evidence_id}/submit",
                json={"expected_revision": 1},
            )
            assert submitted.status_code == 200, submitted.text
            approved = client.post(
                f"/api/v1/candidate/evidence/{evidence_id}/approve",
                json={"expected_revision": 2},
            )
            assert approved.status_code == 200, approved.text
            evidence_ids[label] = UUID(evidence_id)

        expired = db.get(Evidence, evidence_ids["expired"])
        expired.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.flush()

        current_detail = client.get(f"/api/v1/candidate/evidence/{evidence_ids['current']}")
        assert current_detail.status_code == 200, current_detail.text
        assert current_detail.json()["verification_status"] == "VERIFIED"
        assert current_detail.json()["health_status"] == "CURRENT"

        expired_detail = client.get(f"/api/v1/candidate/evidence/{evidence_ids['expired']}")
        assert expired_detail.status_code == 200, expired_detail.text
        assert expired_detail.json()["verification_status"] == "VERIFIED"
        assert expired_detail.json()["health_status"] == "EXPIRED"
        assert expired_detail.json()["source"]["source_reference"] == "test://evidence-health/expired"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()




def test_evidence_library_edit_preserves_source_audits_revision_and_denies_foreign_owner(db):
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
        created = client.post(
            "/api/v1/candidate/evidence",
            json={
                "evidence_type": "employment_record",
                "content": {"role": "Analyst"},
                "source_type": "candidate_upload",
                "source_reference": "test://evidence/editable",
                "source_metadata": {"filename": "employment.pdf"},
            },
        )
        assert created.status_code == 201, created.text
        evidence_id = created.json()["data"]["id"]

        updated = client.patch(
            f"/api/v1/candidate/evidence/{evidence_id}",
            headers={"x-request-id": "evidence-edit-request"},
            json={
                "expected_revision": 1,
                "reason": "Corrected role title",
                "content": {"role": "Senior Analyst"},
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["revision"] == 2
        assert updated.json()["content"] == {"role": "Senior Analyst"}
        assert updated.json()["source"]["source_reference"] == "test://evidence/editable"
        assert updated.json()["source"]["source_metadata"] == {"filename": "employment.pdf"}

        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "EVIDENCE_UPDATED",
                AuditEvent.resource_id == UUID(evidence_id),
                AuditEvent.request_id == "evidence-edit-request",
            )
        )
        assert audit is not None
        assert audit.actor_id == owner.id
        assert audit.reason == "Corrected role title"
        assert audit.details["changed_fields"] == ["content"]
        assert audit.details["previous_revision"] == 1
        assert audit.details["revision"] == 2
        assert audit.details["source_reference"] == "test://evidence/editable"

        stale = client.patch(
            f"/api/v1/candidate/evidence/{evidence_id}",
            json={
                "expected_revision": 1,
                "reason": "Stale correction",
                "content": {"role": "Stale"},
            },
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"

        app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
            user_id=other_user.id, auth_provider="test"
        )
        foreign = client.patch(
            f"/api/v1/candidate/evidence/{evidence_id}",
            json={
                "expected_revision": 2,
                "reason": "Foreign correction",
                "content": {"role": "Unauthorized"},
            },
        )
        assert foreign.status_code == 404
        assert foreign.json()["error"]["code"] == "NOT_FOUND"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()

    db.expire_all()
    persisted = db.get(Evidence, UUID(evidence_id))
    assert persisted is not None
    assert persisted.content == {"role": "Senior Analyst"}
    assert persisted.revision == 2


def test_evidence_library_rejects_edits_after_evidence_is_approved(db):
    owner = make_user(db)
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
        created = client.post(
            "/api/v1/candidate/evidence",
            json={
                "evidence_type": "employment_record",
                "content": {"role": "Analyst"},
                "source_type": "candidate_statement",
                "source_reference": "test://evidence/approved-immutable",
            },
        )
        assert created.status_code == 201, created.text
        evidence_id = created.json()["data"]["id"]

        submitted = client.post(
            f"/api/v1/candidate/evidence/{evidence_id}/submit",
            json={"expected_revision": 1},
        )
        assert submitted.status_code == 200, submitted.text
        approved = client.post(
            f"/api/v1/candidate/evidence/{evidence_id}/approve",
            json={"expected_revision": 2},
        )
        assert approved.status_code == 200, approved.text

        update_response = client.patch(
            f"/api/v1/candidate/evidence/{evidence_id}",
            json={
                "expected_revision": 3,
                "reason": "Attempt to rewrite approved evidence",
                "content": {"role": "Senior Analyst"},
            },
        )
        assert update_response.status_code == 409
        assert update_response.json()["error"]["code"] == "EVIDENCE_NOT_EDITABLE"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()

    db.expire_all()
    persisted = db.get(Evidence, UUID(evidence_id))
    assert persisted is not None
    assert persisted.status == "APPROVED"
    assert persisted.revision == 3
    assert persisted.content == {"role": "Analyst"}

