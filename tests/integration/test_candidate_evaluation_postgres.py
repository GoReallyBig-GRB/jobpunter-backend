import os
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.application.candidates.schemas import CandidateClaimCreate, EvidenceCreate
from app.application.candidates.service import CandidateService
from app.application.opportunities.evaluation_service import CandidateEvaluationService
from app.application.opportunities.service import OpportunityService
from app.infrastructure.db.models import (
    CandidateClaim, CandidateEvaluation, CandidatePreferences, CandidateProfile, Evidence, EvidenceSource, Job,
    JobDescriptionVersion, Punt, User,
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
        lifecycle_status="ACTIVE",
        freshness_status="CURRENT",
        application_url="https://example.com/jobs/technical-product-manager",
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
        assert first_data["input_snapshot"]["job_description_completeness_status"] == "COMPLETE"
        assert first_data["input_snapshot"]["candidate_profile_data"] == {}
        assert first_data["input_snapshot"]["evaluation_target_role_key"] == "technical-product-manager"
        assert first_data["input_snapshot"]["evaluation_policy_version"] == first_data["policy_version"]
        assert isinstance(first_data["input_snapshot"]["ai_trace"], list)
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
        assert first_data["outcome"] == "REVIEW"
        assert second_data["outcome"] == "REVIEW"
        assert first_data["score"] is None
        assert second_data["score"] is None
        assert any("semantic" in reason.lower() for reason in first_data["explanation"]["reasons"])

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


def test_closed_job_evaluation_is_watch_and_never_apply(db):
    user, _profile = make_user(db, "Closed Job Candidate")
    job = Job(
        canonical_title="Technical Product Manager",
        lifecycle_status="CLOSED",
        freshness_status="CURRENT",
        metadata_json={},
        location_restrictions={},
    )
    db.add(job)
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
        user_id=user.id, auth_provider="test"
    )
    client = TestClient(app)
    try:
        response = client.post(
            "/api/v1/candidate/evaluations",
            json={"job_id": str(job.id), "target_role_key": "technical-product-manager"},
        )
        assert response.status_code == 201, response.text
        data = response.json()["data"]
        assert data["outcome"] == "WATCH"
        assert data["score"] is None
        assert any(
            "recorded as closed" in reason.lower()
            for reason in data["explanation"]["reasons"]
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()


def test_evaluation_excludes_future_and_expired_evidence_and_claims(db):
    user, profile = make_user(db, "Evidence Validity Candidate")
    now = datetime.now(timezone.utc)
    job = Job(
        canonical_title="Technical Product Manager",
        lifecycle_status="ACTIVE",
        freshness_status="CURRENT",
        application_url="https://example.com/jobs/technical-product-manager",
        metadata_json={},
        location_restrictions={},
    )
    db.add(job)
    db.flush()
    jd = JobDescriptionVersion(
        job_id=job.id,
        version_number=1,
        content=("Technical product manager with product roadmaps, API integrations, SQL data models, "
                 "technical requirements, agile delivery and engineering teams. " * 3),
        content_hash="evidence-validity-description",
        completeness_status="COMPLETE",
        retrieved_at=now,
        requirement_extraction_status="COMPLETE",
    )
    source = EvidenceSource(source_type="TEST", source_reference="test://evidence-validity")
    db.add_all([jd, source])
    db.flush()
    current = Evidence(
        candidate_profile_id=profile.id, evidence_source_id=source.id, status="APPROVED",
        evidence_type="PROJECT", content={"summary": "Current product roadmaps API integrations"},
    )
    future = Evidence(
        candidate_profile_id=profile.id, evidence_source_id=source.id, status="APPROVED",
        evidence_type="PROJECT", content={"summary": "Future evidence must not be used"},
        effective_from=now.replace(year=now.year + 1),
    )
    expired = Evidence(
        candidate_profile_id=profile.id, evidence_source_id=source.id, status="APPROVED",
        evidence_type="PROJECT", content={"summary": "Expired evidence must not be used"},
        effective_to=now.replace(year=now.year - 1),
    )
    future_claim = CandidateClaim(
        candidate_profile_id=profile.id, claim_type="EXPERIENCE",
        claim_content={"summary": "Future claim must not be used"}, status="APPROVED",
        effective_from=now.replace(year=now.year + 1),
    )
    expired_claim = CandidateClaim(
        candidate_profile_id=profile.id, claim_type="EXPERIENCE",
        claim_content={"summary": "Expired claim must not be used"}, status="APPROVED",
        effective_to=now.replace(year=now.year - 1),
    )
    db.add_all([current, future, expired, future_claim, expired_claim])
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
        user_id=user.id, auth_provider="test"
    )
    client = TestClient(app)
    try:
        response = client.post(
            "/api/v1/candidate/evaluations",
            json={"job_id": str(job.id), "target_role_key": "technical-product-manager"},
        )
        assert response.status_code == 201, response.text
        snapshot = response.json()["data"]["input_snapshot"]
        evidence_ids = {item["id"] for item in snapshot["approved_evidence"]}
        claim_ids = {item["id"] for item in snapshot["approved_claims"]}
        assert str(current.id) in evidence_ids
        assert str(future.id) not in evidence_ids
        assert str(expired.id) not in evidence_ids
        assert str(future_claim.id) not in claim_ids
        assert str(expired_claim.id) not in claim_ids
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()


def test_evaluation_uses_requested_role_even_when_saved_targets_differ(db):
    user, profile = make_user(db, "Role Scope Candidate")
    db.add(CandidatePreferences(
        candidate_profile_id=profile.id,
        preferences={"targetRoles": ["Sales Manager"]},
        revision=1,
    ))
    now = datetime.now(timezone.utc)
    job = Job(
        canonical_title="Technical Product Manager",
        lifecycle_status="ACTIVE",
        freshness_status="CURRENT",
        application_url="https://example.com/jobs/technical-product-manager",
        metadata_json={},
        location_restrictions={},
    )
    db.add(job)
    db.flush()
    jd = JobDescriptionVersion(
        job_id=job.id,
        version_number=1,
        content=("Technical product manager with product roadmaps, API integrations, SQL data models, "
                 "technical requirements, agile delivery and engineering teams. " * 3),
        content_hash="role-scope-description",
        completeness_status="COMPLETE",
        retrieved_at=now,
        requirement_extraction_status="COMPLETE",
    )
    db.add(jd)
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
        user_id=user.id, auth_provider="test"
    )
    client = TestClient(app)
    try:
        response = client.post(
            "/api/v1/candidate/evaluations",
            json={"job_id": str(job.id), "target_role_key": "technical-product-manager"},
        )
        assert response.status_code == 201, response.text
        data = response.json()["data"]
        assert data["evidence_summary"]["role_preference_match"] is True
        assert data["input_snapshot"]["preferences"]["targetRoles"] == ["Sales Manager"]
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()



def test_deterministic_hard_blocker_is_persisted_and_prevents_punt_creation(db):
    user, profile = make_user(db, "Excluded Industry Candidate")
    db.add(CandidatePreferences(
        candidate_profile_id=profile.id,
        preferences={"excludedIndustries": ["insurance"]},
        revision=1,
    ))
    now = datetime.now(timezone.utc)
    job = Job(
        canonical_title="Technical Product Manager",
        company_name="Insurance Example",
        lifecycle_status="ACTIVE",
        freshness_status="CURRENT",
        application_url="https://example.com/jobs/insurance-product-manager",
        metadata_json={"industry": "Insurance"},
        location_restrictions={},
    )
    db.add(job)
    db.flush()
    jd = JobDescriptionVersion(
        job_id=job.id,
        version_number=1,
        content=(
            "Required experience with product roadmaps, API integrations, SQL data models, "
            "technical requirements, agile delivery, engineering teams, stakeholder management, "
            "release planning, and cross-functional software development. " * 3
        ),
        content_hash=f"excluded-industry-{uuid4()}",
        completeness_status="COMPLETE",
        retrieved_at=now,
        requirement_extraction_status="COMPLETE",
    )
    source = EvidenceSource(
        source_type="TEST",
        source_reference=f"test://excluded-industry-{uuid4()}",
    )
    db.add_all([jd, source])
    db.flush()
    db.add(Evidence(
        candidate_profile_id=profile.id,
        evidence_source_id=source.id,
        status="APPROVED",
        evidence_type="PROJECT",
        content={
            "summary": (
                "Technical product manager delivered product roadmaps API integrations SQL data "
                "models technical requirements agile delivery engineering teams stakeholder management"
            )
        },
    ))
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
        user_id=user.id, auth_provider="test"
    )
    client = TestClient(app)
    try:
        evaluated = client.post(
            "/api/v1/candidate/evaluations",
            json={"job_id": str(job.id), "target_role_key": "technical-product-manager"},
        )
        assert evaluated.status_code == 201, evaluated.text
        data = evaluated.json()["data"]
        assert data["outcome"] == "REJECT"
        assert data["score"] == 0
        assert any(blocker["code"] == "EXCLUDED_INDUSTRY" for blocker in data["hard_blockers"])

        punt_response = client.post(
            "/api/v1/candidate/punts",
            json={"evaluation_id": data["id"]},
        )
        assert punt_response.status_code == 409, punt_response.text
        assert db.scalar(select(Punt).where(
            Punt.candidate_profile_id == profile.id,
            Punt.job_id == job.id,
        )) is None
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()



def test_listing_marks_evaluation_stale_after_approved_evidence_changes(db):
    user, profile = make_user(db, "Stale Evaluation Candidate")
    now = datetime.now(timezone.utc)
    job = Job(
        canonical_title="Operations Manager",
        company_name="Example Employer",
        lifecycle_status="ACTIVE",
        freshness_status="CURRENT",
        application_url="https://example.com/jobs/operations-manager",
        metadata_json={},
        location_restrictions={},
    )
    source = EvidenceSource(
        source_type="TEST",
        source_reference=f"test://stale-evaluation/{uuid4()}",
    )
    db.add_all([job, source])
    db.flush()
    description = JobDescriptionVersion(
        job_id=job.id,
        version_number=1,
        content=(
            "The operations manager will lead cross-functional teams, manage operational "
            "processes, improve service quality, coordinate vendors, and report performance. "
        ) * 3,
        content_hash="stale-evaluation-description",
        completeness_status="COMPLETE",
        retrieved_at=now,
        requirement_extraction_status="COMPLETE",
    )
    evidence = Evidence(
        candidate_profile_id=profile.id,
        evidence_source_id=source.id,
        status="APPROVED",
        evidence_type="PROJECT",
        content={"summary": "Led operations and coordinated cross-functional teams."},
        revision=1,
    )
    db.add_all([description, evidence])
    db.commit()

    evaluation = asyncio.run(CandidateEvaluationService(db).evaluate(
        user.id, job.id, "operations-manager"
    ))
    assert evaluation.validity_status == "CURRENT"

    evidence.content = {"summary": "Updated evidence with materially different experience."}
    evidence.revision = 2
    db.commit()

    listed = OpportunityService(db).list_evaluations(user.id)
    assert len(listed) == 1
    assert listed[0].id == evaluation.id
    assert listed[0].validity_status == "STALE"


def test_evaluation_excludes_approved_claim_when_all_supporting_evidence_expires(db):
    user, profile = make_user(db, "Expired Claim Support Candidate")
    now = datetime.now(timezone.utc)
    job = Job(
        canonical_title="Technical Product Manager",
        lifecycle_status="ACTIVE",
        freshness_status="CURRENT",
        application_url="https://example.com/jobs/technical-product-manager",
        metadata_json={},
        location_restrictions={},
    )
    jd = JobDescriptionVersion(
        job_id=job.id,
        version_number=1,
        content=("Product delivery, stakeholder management, technical planning and roadmap ownership. " * 4),
        content_hash="expired-claim-support-description",
        completeness_status="COMPLETE",
        retrieved_at=now,
        requirement_extraction_status="COMPLETE",
    )
    db.add_all([job, jd])
    db.flush()

    candidate_service = CandidateService(db)
    evidence = candidate_service.create_evidence(user.id, EvidenceCreate(
        evidence_type="employment_record",
        content={"employer": "Example Ltd", "role": "Product Manager", "years": 3},
        source_type="candidate_upload",
        source_reference="test://expired-claim-support",
    ))
    candidate_service.transition_evidence(user.id, evidence.id, "PENDING_REVIEW", expected_revision=1)
    evidence = candidate_service.transition_evidence(
        user.id, evidence.id, "APPROVED", expected_revision=2,
    )
    claim = candidate_service.create_claim(user.id, CandidateClaimCreate(
        claim_type="employment_experience",
        claim_content={"employer": "Example Ltd", "role": "Product Manager", "years": 3},
        evidence_ids=[evidence.id],
    ))
    candidate_service.transition_claim(user.id, claim.id, "PENDING_REVIEW", expected_revision=1)
    candidate_service.transition_claim(user.id, claim.id, "APPROVED", expected_revision=2)

    evidence.expires_at = now - timedelta(days=1)
    db.commit()

    evaluation = asyncio.run(CandidateEvaluationService(db).evaluate(
        user.id, job.id, "technical-product-manager",
    ))

    assert evaluation.input_snapshot["approved_claims"] == []
    assert evaluation.input_snapshot["excluded_claims"] == [{
        "id": str(claim.id),
        "reason": "NO_CURRENT_APPROVED_SUPPORTING_EVIDENCE",
    }]
