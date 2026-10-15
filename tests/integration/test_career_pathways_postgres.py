"""PostgreSQL persistence and isolation tests for career pathway records."""
import os
from datetime import datetime, timezone
from uuid import uuid4
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import (
    CareerPathway, CareerPathwayEvent, CandidateProfile, Evidence, EvidenceSource, User,
)
from app.api.deps import get_principal
from app.infrastructure.db.session import get_db
from app.main import app
from app.ports.identity import AuthenticatedPrincipal
from app.application.career_pathways import service as pathway_service
from fastapi.testclient import TestClient

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


def _api_client(db, user):
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=user.id, auth_provider="test"
    )
    connection = db.connection()

    def override_db():
        request_session = Session(
            bind=connection, join_transaction_mode="create_savepoint"
        )
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _pathway_payload(**overrides):
    payload = {
        "title": "Technical Product Management",
        "role_family": "Product Management",
        "target_roles": ["Technical Product Manager", "Platform Product Manager"],
        "target_seniority": ["Senior", "Lead"],
        "domains": ["B2B SaaS"],
        "supporting_evidence_refs": [],
        "gaps": ["More direct platform roadmap evidence"],
        "rationale": "Candidate-defined path based on documented product delivery.",
        "confidence": 0.8,
    }
    payload.update(overrides)
    return payload


def test_pathway_crud_revision_audit_and_candidate_isolation(db):
    owner, _ = _candidate(db, "Pathway API owner")
    other, _ = _candidate(db, "Other pathway candidate")
    client = _api_client(db, owner)
    try:
        created = client.post("/api/v1/candidate/career-pathways", json=_pathway_payload())
        assert created.status_code == 201
        row = created.json()["data"]
        pathway_id = row["id"]
        assert row["source_type"] == "USER_DEFINED"
        assert row["approval_status"] == "APPROVED"
        assert row["approved_by_user_id"] == str(owner.id)
        assert row["revision"] == 1

        updated_payload = _pathway_payload(
            title="Platform Product Management",
            target_roles=["Platform Product Manager"],
            expected_revision=1,
        )
        updated = client.patch(
            f"/api/v1/candidate/career-pathways/{pathway_id}", json=updated_payload
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["revision"] == 2
        assert updated.json()["data"]["target_roles"] == ["Platform Product Manager"]

        stale = client.patch(
            f"/api/v1/candidate/career-pathways/{pathway_id}",
            json=_pathway_payload(expected_revision=1),
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"

        history = client.get(f"/api/v1/candidate/career-pathways/{pathway_id}/events")
        assert history.status_code == 200
        assert [event["event_type"] for event in history.json()["data"]] == ["CREATED", "EDITED"]

        app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
            user_id=other.id, auth_provider="test"
        )
        assert client.get(f"/api/v1/candidate/career-pathways/{pathway_id}").status_code == 404
        assert client.get("/api/v1/candidate/career-pathways").json()["data"] == []
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()


def test_ai_suggestions_remain_pending_until_candidate_approval(db, monkeypatch):
    owner, profile = _candidate(db, "AI pathway candidate")
    source = EvidenceSource(
        source_type="manual", source_reference="candidate-entered-evidence", metadata_json={}
    )
    db.add(source)
    db.flush()
    evidence = Evidence(
        candidate_profile_id=profile.id,
        evidence_source_id=source.id,
        evidence_type="experience",
        content={"summary": "Led product delivery and coordinated technical teams."},
        status="APPROVED",
    )
    db.add(evidence)
    db.flush()

    async def fake_execute_ai_task(request, *, db, user_id, request_id=None):
        assert request.task == "career_pathway_discovery"
        return SimpleNamespace(
            execution_id=uuid4(),
            parsed_data={
                "pathways": [{
                    "title": "Technical Product Management",
                    "role_family": "Product Management",
                    "target_roles": ["Technical Product Manager"],
                    "target_seniority": ["Senior"],
                    "domains": ["B2B SaaS"],
                    "rationale": "Supported by the supplied product delivery evidence.",
                    "supporting_evidence_refs": [str(evidence.id)],
                    "gaps": ["More direct platform roadmap evidence"],
                    "confidence": 0.7,
                }],
                "caveats": [],
            },
        )

    monkeypatch.setattr(pathway_service, "execute_ai_task", fake_execute_ai_task)
    client = _api_client(db, owner)
    try:
        suggested = client.post(
            "/api/v1/candidate/career-pathways/suggestions",
            json={"instruction": "Focus on technical product roles."},
        )
        assert suggested.status_code == 201
        row = suggested.json()["data"][0]
        pathway_id = row["id"]
        assert row["source_type"] == "AI_SUGGESTED"
        assert row["approval_status"] == "PENDING_REVIEW"
        assert row["approved_at"] is None

        approved = client.post(
            f"/api/v1/candidate/career-pathways/{pathway_id}/approve",
            json={"expected_revision": 1, "reason": "Reviewed and accepted"},
        )
        assert approved.status_code == 200
        assert approved.json()["data"]["approval_status"] == "APPROVED"
        assert approved.json()["data"]["approved_by_user_id"] == str(owner.id)
        assert approved.json()["data"]["revision"] == 2

        history = client.get(f"/api/v1/candidate/career-pathways/{pathway_id}/events")
        assert [event["event_type"] for event in history.json()["data"]] == ["SUGGESTED", "APPROVED"]

        edited = client.patch(
            f"/api/v1/candidate/career-pathways/{pathway_id}",
            json=_pathway_payload(expected_revision=2),
        )
        assert edited.status_code == 409
        assert edited.json()["error"]["code"] == "AI_SUGGESTION_IMMUTABLE"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()


def test_pathway_suggestions_require_current_approved_evidence(db):
    owner, _ = _candidate(db, "Evidence-free candidate")
    client = _api_client(db, owner)
    try:
        response = client.post("/api/v1/candidate/career-pathways/suggestions", json={})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "APPROVED_EVIDENCE_REQUIRED"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()


def test_retiring_pathway_is_revision_checked_and_audited(db):
    owner, _ = _candidate(db, "Retiring pathway candidate")
    client = _api_client(db, owner)
    try:
        created = client.post("/api/v1/candidate/career-pathways", json=_pathway_payload())
        pathway_id = created.json()["data"]["id"]
        retired = client.post(
            f"/api/v1/candidate/career-pathways/{pathway_id}/retire",
            json={"expected_revision": 1, "reason": "Target is no longer relevant"},
        )
        assert retired.status_code == 200
        assert retired.json()["data"]["approval_status"] == "RETIRED"
        assert retired.json()["data"]["revision"] == 2
        history = client.get(f"/api/v1/candidate/career-pathways/{pathway_id}/events")
        assert [event["event_type"] for event in history.json()["data"]] == ["CREATED", "RETIRED"]
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()
