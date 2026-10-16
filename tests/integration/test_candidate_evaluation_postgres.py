import os
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.api.deps import get_principal
from app.application.candidates.schemas import CandidateClaimCreate, EvidenceCreate
from app.application.candidates.service import CandidateService
from app.application.jobs.requirement_review import review_requirement
from app.application.opportunities.evaluation_service import CandidateEvaluationService
from app.application.opportunities.service import OpportunityService
from app.infrastructure.db.models.governance import GovernanceConfigVersion
from app.infrastructure.db.models import (
    CandidateClaim, CandidateEvaluation, CandidatePreferences, CandidateProfile, CareerPathway, Evidence, EvidenceSource, Job,
    JobDescriptionVersion, JobRequirement, JobVerificationAttempt, Punt, User,
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
        last_verified_at=now,
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
    route_attempt = JobVerificationAttempt(
        job_id=job.id,
        checked_url=job.application_url,
        final_url=job.application_url,
        status="ACTIVE",
        http_status_code=200,
        reason_code="JOB_PAGE_AND_APPLICATION_SIGNAL_PRESENT",
        evidence={"apply_marker_detected": True},
        verifier_version="test-verifier-v1",
        checked_at=now,
        created_at=now,
    )
    db.add_all([jd, source, route_attempt])
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

        repeated = client.post(
            "/api/v1/candidate/evaluations",
            json={"job_id": str(job.id), "target_role_key": "technical-product-manager"},
        )
        assert repeated.status_code == 201, repeated.text
        repeated_data = repeated.json()["data"]
        assert repeated_data["evaluation_version"] == first_data["evaluation_version"] + 1
        assert repeated_data["id"] != first_data["id"]
        assert repeated_data["input_snapshot"]["evaluation_policy_version"] == repeated_data["policy_version"]

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
        assert len(evaluations) == 3
        assert sum(row.candidate_profile_id == profile_a.id for row in evaluations) == 2
        assert sum(row.candidate_profile_id == profile_b.id for row in evaluations) == 1

        app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
            user_id=user_a.id, auth_provider="test"
        )
        listed = client.get("/api/v1/candidate/evaluations")
        assert listed.status_code == 200
        assert len(listed.json()["data"]) == 2
        assert {row["id"] for row in listed.json()["data"]} == {
            first_data["id"], repeated_data["id"],
        }
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()


@pytest.mark.parametrize(
    ("lifecycle_status", "freshness_status", "reason_fragment"),
    [
        ("CLOSED", "CURRENT", "recorded as closed"),
        ("ACTIVE", "STALE", "job record is stale"),
    ],
)
def test_closed_or_stale_job_evaluation_is_watch_and_skips_semantic_matching(
    db, monkeypatch, lifecycle_status, freshness_status, reason_fragment
):
    user, _profile = make_user(db, "Unavailable Job Candidate")
    now = datetime.now(timezone.utc)
    job = Job(
        canonical_title="Technical Product Manager",
        lifecycle_status=lifecycle_status,
        freshness_status=freshness_status,
        application_url="https://example.com/jobs/unavailable",
        metadata_json={},
        location_restrictions={},
    )
    db.add(job)
    db.flush()
    description = JobDescriptionVersion(
        job_id=job.id,
        version_number=1,
        content=(
            "Responsibilities include product delivery and API integration. "
            "Experience with SQL query design is required. "
            "Coordinate stakeholders and engineering teams. "
        ) * 5,
        content_hash=f"unavailable-job-{uuid4().hex}",
        completeness_status="COMPLETE",
        retrieved_at=now,
        requirement_extraction_status="COMPLETE",
        requirement_extraction_version="job-requirement-extraction-v3",
    )
    db.add(description)
    db.flush()
    db.add(JobRequirement(
        job_description_version_id=description.id,
        requirement_type="SKILL",
        description="Experience with SQL query design",
        importance="HIGH",
        severity="MUST_HAVE",
        dimension="TECHNICAL",
        hard_constraint=True,
        extraction_status="CONFIRMED",
        source_excerpt="Experience with SQL query design is required.",
        extraction_confidence=0.96,
        extraction_version="job-requirement-extraction-v3",
    ))
    db.flush()

    async def forbidden_semantic_call(**_kwargs):
        raise AssertionError("Semantic matching must not run for a closed or stale job.")

    monkeypatch.setattr(
        "app.application.opportunities.evaluation_service.match_requirements_to_evidence",
        forbidden_semantic_call,
    )
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
            reason_fragment in reason.lower()
            for reason in data["explanation"]["reasons"]
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()


def test_evaluation_without_approved_evidence_remains_reviewable(db):
    user, _profile = make_user(db, "No Approved Evidence Candidate")
    now = datetime.now(timezone.utc)
    job = Job(
        canonical_title="Technical Product Manager",
        lifecycle_status="ACTIVE",
        freshness_status="CURRENT",
        application_url="https://example.com/jobs/no-evidence",
        last_verified_at=now,
        metadata_json={},
        location_restrictions={},
    )
    db.add(job)
    db.flush()
    jd = JobDescriptionVersion(
        job_id=job.id,
        version_number=1,
        content=(
            "Responsibilities include managing product delivery and coordinating teams. "
            "Requirements include relevant experience, technical planning, and stakeholder management. "
        ) * 4,
        content_hash="no-approved-evidence-description",
        completeness_status="COMPLETE",
        retrieved_at=now,
        requirement_extraction_status="COMPLETE",
    )
    route_attempt = JobVerificationAttempt(
        job_id=job.id,
        checked_url=job.application_url,
        final_url=job.application_url,
        status="ACTIVE",
        http_status_code=200,
        reason_code="JOB_PAGE_AND_APPLICATION_SIGNAL_PRESENT",
        evidence={"apply_marker_detected": True},
        verifier_version="test-verifier-v1",
        checked_at=now,
        created_at=now,
    )
    db.add_all([jd, route_attempt])
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
        assert data["outcome"] == "REVIEW"
        assert data["score"] is None
        assert data["evidence_summary"]["approved_evidence_count"] == 0
        assert data["input_snapshot"]["approved_evidence"] == []
        assert data["input_snapshot"]["approved_claims"] == []
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()


@pytest.mark.parametrize("lifecycle_status", ["ACTIVE", "OPEN"])
def test_reviewed_requirements_and_governed_matches_can_qualify_a_candidate_punt(
    db, monkeypatch, lifecycle_status,
):
    user, profile = make_user(db, "Persisted Requirements Candidate")
    now = datetime.now(timezone.utc)
    config_key = "decision.rules.candidate_evaluation"
    active_config = db.scalar(
        select(GovernanceConfigVersion)
        .where(
            GovernanceConfigVersion.config_key == config_key,
            GovernanceConfigVersion.status == "PUBLISHED",
        )
        .with_for_update()
    )
    if active_config is not None:
        active_config.status = "RETIRED"
        db.flush()
    latest_rule_version = db.scalar(
        select(func.max(GovernanceConfigVersion.version)).where(
            GovernanceConfigVersion.config_key == config_key
        )
    ) or 0
    rule_version = latest_rule_version + 1
    rules_payload = {"apply_min_score": 80, "review_min_score": 35}
    db.add(GovernanceConfigVersion(
        config_key=config_key,
        version=rule_version,
        status="PUBLISHED",
        payload=rules_payload,
        payload_fingerprint="f" * 64,
        created_by_user_id=user.id,
        created_at=now,
        published_by_user_id=user.id,
        published_at=now,
        supersedes_id=active_config.id if active_config is not None else None,
    ))
    db.flush()
    job = Job(
        canonical_title="Technical Product Manager",
        lifecycle_status=lifecycle_status,
        freshness_status="CURRENT",
        application_url="https://example.com/jobs/persisted-requirements",
        last_verified_at=now,
        metadata_json={"application_route_type": "DIRECT_EMPLOYER"},
        location_restrictions={},
    )
    db.add(job)
    db.flush()
    jd_text = (
        "Responsibilities include product delivery and API integration. "
        "Experience with SQL query design is required. "
        "Coordinate stakeholders and engineering teams. "
    ) * 5
    jd = JobDescriptionVersion(
        job_id=job.id,
        version_number=1,
        content=jd_text,
        content_hash="persisted-requirements-description",
        completeness_status="COMPLETE",
        retrieved_at=now,
        requirement_extraction_status="NEEDS_REVIEW",
        requirement_extraction_version="job-requirement-extraction-v3",
    )
    db.add(jd)
    db.flush()
    requirement = JobRequirement(
        job_description_version_id=jd.id,
        requirement_type="SKILL",
        description="Experience with SQL query design",
        importance="HIGH",
        severity="MUST_HAVE",
        dimension="TECHNICAL",
        hard_constraint=True,
        extraction_status="NEEDS_REVIEW",
        source_excerpt="Experience with SQL query design is required.",
        extraction_confidence=0.96,
        extraction_version="job-requirement-extraction-v3",
    )
    db.add(requirement)
    db.add(JobVerificationAttempt(
        job_id=job.id,
        checked_url=job.application_url,
        final_url=job.application_url,
        status="ACTIVE",
        http_status_code=200,
        reason_code="JOB_PAGE_AND_APPLICATION_SIGNAL_PRESENT",
        evidence={"apply_marker_detected": True},
        verifier_version="test-verifier-v1",
        checked_at=now,
        created_at=now,
    ))
    db.flush()
    review_requirement(
        db,
        requirement_id=requirement.id,
        reviewer_user_id=user.id,
        decision="CONFIRM",
        severity="MUST_HAVE",
        dimension="TECHNICAL",
    )

    evidence_service = CandidateService(db)
    evidence = evidence_service.create_evidence(user.id, EvidenceCreate(
        evidence_type="project",
        content={"summary": "Delivered SQL query design and API integration for a product workflow."},
        source_type="candidate_upload",
        source_reference="test://approved-sql-evidence",
    ))
    evidence_service.transition_evidence(user.id, evidence.id, "PENDING_REVIEW", expected_revision=1)
    evidence_service.transition_evidence(user.id, evidence.id, "APPROVED", expected_revision=2)

    async def governed_matcher(**kwargs):
        assert kwargs["description_version_id"] == jd.id
        assert kwargs["requirement_extraction_status"] == "NEEDS_REVIEW"
        assert kwargs["approved_evidence"]
        return [{
            "requirement_id": str(requirement.id),
            "requirement_text": requirement.description,
            "status": "DIRECT",
            "rationale": "The approved project evidence supports the confirmed SQL requirement.",
            "evidence_refs": [str(evidence.id)],
            "mandatory": True,
            "dimension": "technical_fit",
            "job_requirement_id": str(requirement.id),
            "requirement_type": requirement.requirement_type,
            "importance": requirement.importance,
            "hard_constraint": True,
            "source_excerpt": requirement.source_excerpt,
            "extraction_version": requirement.extraction_version,
        }], [{
            "task": "evidence_matching",
            "execution_id": str(uuid4()),
            "provider": "test",
            "model": "test-model",
            "task_policy_version": "v1",
            "system_policy_version": "v1",
            "output_contract_version": "v4",
            "config_version_id": None,
            "config_fingerprint": "test-governed-config-fingerprint",
        }], None

    monkeypatch.setattr(
        "app.application.opportunities.evaluation_service.match_requirements_to_evidence",
        governed_matcher,
    )
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
        assert data["outcome"] == "APPLY"
        assert data["score"] == 100
        assert data["policy_version"] == f"candidate-evaluation-v20+rules-v{rule_version}"
        assert data["input_snapshot"]["job_requirements"][0]["extraction_status"] == "CONFIRMED"
        assert data["input_snapshot"]["job_requirements"][0]["latest_review"] is not None
        assert data["input_snapshot"]["ai_trace"][0]["output_contract_version"] == "v4"

        punt = OpportunityService(db).create_punt(user.id, UUID(data["id"]))
        assert punt.candidate_profile_id == profile.id
        assert punt.job_id == job.id
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()



def test_semantic_failure_stays_review_and_cannot_create_punt(db, monkeypatch):
    user, profile = make_user(db, "Semantic Failure Candidate")
    now = datetime.now(timezone.utc)
    job = Job(
        canonical_title="Technical Product Manager",
        company_name="Example Employer",
        lifecycle_status="ACTIVE",
        freshness_status="CURRENT",
        application_url="https://example.com/jobs/semantic-failure",
        metadata_json={},
        location_restrictions={},
    )
    source = EvidenceSource(
        source_type="TEST",
        source_reference=f"test://semantic-failure/{uuid4()}",
    )
    db.add_all([job, source])
    db.flush()
    jd_text = (
        "Responsibilities include product delivery and API integration. "
        "Experience with SQL query design is required. "
        "Coordinate stakeholders and engineering teams. "
    ) * 5
    jd = JobDescriptionVersion(
        job_id=job.id,
        version_number=1,
        content=jd_text,
        content_hash=f"semantic-failure-{uuid4().hex}",
        completeness_status="COMPLETE",
        retrieved_at=now,
        requirement_extraction_status="NEEDS_REVIEW",
        requirement_extraction_version="job-requirement-extraction-v3",
    )
    db.add(jd)
    db.flush()
    requirement = JobRequirement(
        job_description_version_id=jd.id,
        requirement_type="SKILL",
        description="Experience with SQL query design",
        importance="HIGH",
        severity="MUST_HAVE",
        dimension="TECHNICAL",
        hard_constraint=True,
        extraction_status="NEEDS_REVIEW",
        source_excerpt="Experience with SQL query design is required.",
        extraction_confidence=0.96,
        extraction_version="job-requirement-extraction-v3",
    )
    evidence = Evidence(
        candidate_profile_id=profile.id,
        evidence_source_id=source.id,
        status="APPROVED",
        evidence_type="PROJECT",
        content={"summary": "Delivered SQL query design for a product workflow."},
    )
    route_attempt = JobVerificationAttempt(
        job_id=job.id,
        checked_url=job.application_url,
        final_url=job.application_url,
        status="ACTIVE",
        http_status_code=200,
        reason_code="JOB_PAGE_AND_APPLICATION_SIGNAL_PRESENT",
        evidence={"apply_marker_detected": True},
        verifier_version="test-verifier-v1",
        checked_at=now,
        created_at=now,
    )
    db.add_all([jd, requirement, evidence, route_attempt])
    db.flush()
    review_requirement(
        db,
        requirement_id=requirement.id,
        reviewer_user_id=user.id,
        decision="CONFIRM",
        severity="MUST_HAVE",
        dimension="TECHNICAL",
    )

    async def failed_semantic_match(**_kwargs):
        return None, [], "PROVIDER_UNAVAILABLE"

    monkeypatch.setattr(
        "app.application.opportunities.evaluation_service.match_requirements_to_evidence",
        failed_semantic_match,
    )
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
        assert data["outcome"] == "REVIEW"
        assert data["score"] is None
        assert data["explanation"]["confidence"] == "LOW"
        assert any(
            "no application recommendation was inferred" in reason.lower()
            for reason in data["explanation"]["reasons"]
        )

        punt_response = client.post(
            "/api/v1/candidate/punts",
            json={"evaluation_id": data["id"]},
        )
        assert punt_response.status_code == 409, punt_response.text
        assert db.scalar(select(Punt).where(
            Punt.candidate_profile_id == profile.id,
            Punt.job_id == job.id,
        )) is None

        async def unversioned_semantic_match(**_kwargs):
            return [{
                "requirement_id": str(requirement.id),
                "requirement_text": requirement.description,
                "status": "DIRECT",
                "rationale": "Test result without governed provenance.",
                "evidence_refs": [str(evidence.id)],
                "mandatory": True,
                "dimension": "technical_fit",
                "job_requirement_id": str(requirement.id),
                "requirement_type": requirement.requirement_type,
                "importance": requirement.importance,
                "hard_constraint": True,
                "source_excerpt": requirement.source_excerpt,
                "extraction_version": requirement.extraction_version,
            }], [{
                "task": "evidence_matching",
                "execution_id": str(uuid4()),
                "provider": "test",
                "model": "test-model",
                "output_contract_version": "v4",
                "config_fingerprint": "unversioned",
                "task_policy_version": "v1",
                "system_policy_version": "v1",
            }], None

        monkeypatch.setattr(
            "app.application.opportunities.evaluation_service.match_requirements_to_evidence",
            unversioned_semantic_match,
        )
        unversioned_response = client.post(
            "/api/v1/candidate/evaluations",
            json={"job_id": str(job.id), "target_role_key": "technical-product-manager"},
        )
        assert unversioned_response.status_code == 201, unversioned_response.text
        unversioned = unversioned_response.json()["data"]
        assert unversioned["outcome"] == "REVIEW"
        assert unversioned["score"] is None
        assert any(
            "no application recommendation was inferred" in reason.lower()
            for reason in unversioned["explanation"]["reasons"]
        )
        rejected_unversioned_punt = client.post(
            "/api/v1/candidate/punts",
            json={"evaluation_id": unversioned["id"]},
        )
        assert rejected_unversioned_punt.status_code == 409, rejected_unversioned_punt.text
        assert db.scalar(select(Punt).where(
            Punt.candidate_profile_id == profile.id,
            Punt.job_id == job.id,
        )) is None
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()




def test_contradicted_semantic_requirement_remains_review_and_cannot_qualify_punt(db, monkeypatch):
    from app.application.ai.output_contracts import get_task_output_contract_version
    from app.application.ai.system_policy import SYSTEM_POLICY_VERSION
    from app.application.ai.task_registry import get_ai_task_definition

    user, profile = make_user(db, "Contradicted Requirement Candidate")
    now = datetime.now(timezone.utc)
    job = Job(
        canonical_title="Technical Product Manager",
        company_name="Example Employer",
        lifecycle_status="ACTIVE",
        freshness_status="CURRENT",
        application_url="https://example.com/jobs/contradicted-requirement",
        metadata_json={},
        location_restrictions={},
    )
    source = EvidenceSource(
        source_type="TEST",
        source_reference=f"test://contradicted-requirement/{uuid4()}",
    )
    db.add_all([job, source])
    db.flush()
    description = JobDescriptionVersion(
        job_id=job.id,
        version_number=1,
        content=(
            "Responsibilities include product delivery and API integration. "
            "Experience with SQL query design is required. "
            "Coordinate stakeholders and engineering teams. "
        ) * 5,
        content_hash=f"contradicted-requirement-{uuid4().hex}",
        completeness_status="COMPLETE",
        retrieved_at=now,
        requirement_extraction_status="NEEDS_REVIEW",
        requirement_extraction_version="job-requirement-extraction-v3",
    )
    db.add(description)
    db.flush()
    requirement = JobRequirement(
        job_description_version_id=description.id,
        requirement_type="SKILL",
        description="Experience with SQL query design",
        importance="HIGH",
        severity="MUST_HAVE",
        dimension="TECHNICAL",
        hard_constraint=True,
        extraction_status="NEEDS_REVIEW",
        source_excerpt="Experience with SQL query design is required.",
        extraction_confidence=0.96,
        extraction_version="job-requirement-extraction-v3",
    )
    route_attempt = JobVerificationAttempt(
        job_id=job.id,
        checked_url=job.application_url,
        final_url=job.application_url,
        status="ACTIVE",
        http_status_code=200,
        reason_code="JOB_PAGE_AND_APPLICATION_SIGNAL_PRESENT",
        evidence={"apply_marker_detected": True},
        verifier_version="test-verifier-v1",
        checked_at=now,
        created_at=now,
    )
    evidence = Evidence(
        candidate_profile_id=profile.id,
        evidence_source_id=source.id,
        status="APPROVED",
        evidence_type="PROJECT",
        content={"summary": "Delivered a project without SQL query design responsibility."},
    )
    db.add_all([requirement, route_attempt, evidence])
    db.flush()
    review_requirement(
        db,
        requirement_id=requirement.id,
        reviewer_user_id=user.id,
        decision="CONFIRM",
        severity="MUST_HAVE",
        dimension="TECHNICAL",
    )

    async def contradicted_semantic_match(**_kwargs):
        return [{
            "requirement_id": str(requirement.id),
            "requirement_text": requirement.description,
            "status": "CONTRADICTED",
            "rationale": "The approved evidence conflicts with the asserted requirement match.",
            "evidence_refs": [str(evidence.id)],
            "mandatory": True,
            "dimension": "technical_fit",
            "job_requirement_id": str(requirement.id),
            "requirement_type": requirement.requirement_type,
            "importance": requirement.importance,
            "severity": requirement.severity,
            "hard_constraint": requirement.hard_constraint,
            "source_excerpt": requirement.source_excerpt,
            "extraction_version": requirement.extraction_version,
        }], [{
            "task": "evidence_matching",
            "execution_id": str(uuid4()),
            "provider": "test",
            "model": "test-model",
            "output_contract_version": get_task_output_contract_version("evidence_matching"),
            "config_fingerprint": "test-fingerprint",
            "task_policy_version": get_ai_task_definition("evidence_matching").policy_version,
            "system_policy_version": SYSTEM_POLICY_VERSION,
            "config_version_id": None,
        }], None

    monkeypatch.setattr(
        "app.application.opportunities.evaluation_service.match_requirements_to_evidence",
        contradicted_semantic_match,
    )
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
        assert data["outcome"] == "REVIEW"
        assert data["evidence_summary"]["requirement_matches"][0]["status"] == "CONTRADICTED"
        assert data["evidence_summary"]["unmet_mandatory_requirement_count"] == 1

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



def test_evaluation_uses_published_decision_rules_and_stales_after_rule_change(db, monkeypatch):
    user, profile = make_user(db, "Decision Rule Candidate")
    now = datetime.now(timezone.utc)
    latest_rule_version = db.scalar(select(func.max(GovernanceConfigVersion.version)).where(
        GovernanceConfigVersion.config_key == "decision.rules.candidate_evaluation"
    )) or 0
    config = db.scalar(
        select(GovernanceConfigVersion)
        .where(
            GovernanceConfigVersion.config_key == "decision.rules.candidate_evaluation",
            GovernanceConfigVersion.status == "PUBLISHED",
        )
        .order_by(GovernanceConfigVersion.version.desc())
    )
    created_config = config is None
    if config is None:
        config = GovernanceConfigVersion(
            config_key="decision.rules.candidate_evaluation",
            version=latest_rule_version + 1,
            status="PUBLISHED",
            payload={"apply_min_score": 90, "review_min_score": 35},
            payload_fingerprint="a" * 64,
            created_by_user_id=user.id,
            created_at=now,
            published_by_user_id=user.id,
            published_at=now,
        )
    next_version_number = latest_rule_version + (2 if created_config else 1)
    job = Job(
        canonical_title="Technical Product Manager",
        company_name="Example Employer",
        lifecycle_status="ACTIVE",
        freshness_status="CURRENT",
        application_url="https://example.com/jobs/decision-rules",
        last_verified_at=now,
        metadata_json={},
        location_restrictions={},
    )
    source = EvidenceSource(
        source_type="TEST",
        source_reference=f"test://decision-rules/{uuid4()}",
    )
    if created_config:
        db.add(config)
    db.add_all([job, source])
    db.flush()
    jd = JobDescriptionVersion(
        job_id=job.id,
        version_number=1,
        content=(
            "Responsibilities include product delivery and API integration. "
            "Experience with SQL query design is required. Coordinate stakeholders and teams. "
        ) * 5,
        content_hash=f"decision-rules-jd-{uuid4().hex}",
        completeness_status="COMPLETE",
        retrieved_at=now,
        requirement_extraction_status="NEEDS_REVIEW",
        requirement_extraction_version="job-requirement-extraction-v3",
    )
    evidence = Evidence(
        candidate_profile_id=profile.id,
        evidence_source_id=source.id,
        status="APPROVED",
        evidence_type="PROJECT",
        content={"summary": "Delivered product workflows and SQL query design."},
    )
    route_attempt = JobVerificationAttempt(
        job_id=job.id,
        checked_url=job.application_url,
        final_url=job.application_url,
        status="ACTIVE",
        http_status_code=200,
        reason_code="JOB_PAGE_AND_APPLICATION_SIGNAL_PRESENT",
        evidence={"apply_marker_detected": True},
        verifier_version="test-verifier-v1",
        checked_at=now,
        created_at=now,
    )
    db.add_all([jd, evidence, route_attempt])
    db.flush()

    async def governed_matches(**_kwargs):
        matches = []
        for index in range(1, 6):
            supported = index < 5
            matches.append({
                "requirement_id": f"REQ-{index}",
                "requirement_text": f"Requirement {index}",
                "status": "DIRECT" if supported else "MISSING",
                "rationale": "Grounded test match.",
                "evidence_refs": [str(evidence.id)] if supported else [],
                "mandatory": supported,
            })
        return matches, [{
            "task": "evidence_matching",
            "execution_id": str(uuid4()),
            "provider": "test",
            "model": "test-model",
            "task_policy_version": "v1",
            "system_policy_version": "v1",
            "output_contract_version": "v4",
            "config_version_id": (
                str(task_config.id) if (task_config := db.scalar(select(GovernanceConfigVersion).where(
                    GovernanceConfigVersion.config_key == "ai.task.evidence_matching",
                    GovernanceConfigVersion.status == "PUBLISHED",
                ))) else None
            ),
            "config_fingerprint": "test-governed-config-fingerprint",
        }], None

    monkeypatch.setattr(
        "app.application.opportunities.evaluation_service.match_requirements_to_evidence",
        governed_matches,
    )
    evaluation = asyncio.run(CandidateEvaluationService(db).evaluate(
        user.id, job.id, "technical-product-manager"
    ))
    assert evaluation.outcome == "REVIEW"
    assert evaluation.score == 80
    assert evaluation.policy_version == f"candidate-evaluation-v20+rules-v{config.version}"
    assert evaluation.input_snapshot["decision_rule_config_version_id"] == str(config.id)
    assert evaluation.input_snapshot["decision_rule_config_version"] == config.version
    assert evaluation.input_snapshot["decision_rule_config_fingerprint"] == config.payload_fingerprint

    config.status = "RETIRED"
    db.flush()
    next_config = GovernanceConfigVersion(
        config_key="decision.rules.candidate_evaluation",
        version=next_version_number,
        status="PUBLISHED",
        payload={"apply_min_score": 70, "review_min_score": 35},
        payload_fingerprint="b" * 64,
        created_by_user_id=user.id,
        created_at=now + timedelta(minutes=1),
        published_by_user_id=user.id,
        published_at=now + timedelta(minutes=1),
        supersedes_id=config.id,
    )
    db.add(next_config)
    db.commit()

    listed = OpportunityService(db).list_evaluations(user.id)
    assert len(listed) == 1
    assert listed[0].id == evaluation.id
    assert listed[0].validity_status == "STALE"

    # A fresh evaluation records the then-current task configuration version.
    reevaluated = asyncio.run(CandidateEvaluationService(db).evaluate(
        user.id, job.id, "technical-product-manager"
    ))
    assert reevaluated.validity_status == "CURRENT"

    latest_task_version = db.scalar(select(func.max(GovernanceConfigVersion.version)).where(
        GovernanceConfigVersion.config_key == "ai.task.evidence_matching"
    )) or 0
    published_task_config = db.scalar(
        select(GovernanceConfigVersion).where(
            GovernanceConfigVersion.config_key == "ai.task.evidence_matching",
            GovernanceConfigVersion.status == "PUBLISHED",
        )
    )
    if published_task_config is not None:
        published_task_config.status = "RETIRED"
        db.flush()
    task_config_time = now + timedelta(minutes=2)
    db.add(GovernanceConfigVersion(
        config_key="ai.task.evidence_matching",
        version=latest_task_version + 1,
        status="PUBLISHED",
        payload={"model": "gemini-2.5-flash"},
        payload_fingerprint="c" * 64,
        created_by_user_id=user.id,
        created_at=task_config_time,
        published_by_user_id=user.id,
        published_at=task_config_time,
        supersedes_id=published_task_config.id if published_task_config else None,
    ))
    db.commit()

    listed_after_task_change = OpportunityService(db).list_evaluations(user.id)
    reevaluated_row = next(row for row in listed_after_task_change if row.id == reevaluated.id)
    assert reevaluated_row.validity_status == "STALE"



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
        last_verified_at=now,
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
    db.add_all([
        jd,
        JobVerificationAttempt(
            job_id=job.id,
            checked_url=job.application_url,
            final_url=job.application_url,
            status="ACTIVE",
            http_status_code=200,
            reason_code="JOB_PAGE_AND_APPLICATION_SIGNAL_PRESENT",
            evidence={"apply_marker_detected": True},
            verifier_version="test-verifier-v1",
            checked_at=now,
            created_at=now,
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



def test_deterministic_hard_blocker_is_persisted_and_prevents_punt_creation(db, monkeypatch):
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
    db.add(JobRequirement(
        job_description_version_id=jd.id,
        requirement_type="SKILL",
        description="Experience with product roadmaps",
        importance="HIGH",
        severity="MUST_HAVE",
        dimension="TECHNICAL",
        hard_constraint=True,
        extraction_status="CONFIRMED",
        source_excerpt="Required experience with product roadmaps",
        extraction_confidence=0.99,
        extraction_version="test-requirements-v1",
    ))
    db.flush()

    async def forbidden_semantic_call(**_kwargs):
        raise AssertionError("Semantic matching must not run after a deterministic hard rejection.")

    monkeypatch.setattr(
        "app.application.opportunities.evaluation_service.match_requirements_to_evidence",
        forbidden_semantic_call,
    )
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



def test_evaluation_becomes_stale_when_a_new_job_description_version_arrives(db):
    user, profile = make_user(db, "JD Version Candidate")
    now = datetime.now(timezone.utc)
    job = Job(
        canonical_title="Technical Product Manager",
        company_name="Example Employer",
        lifecycle_status="ACTIVE",
        freshness_status="CURRENT",
        application_url="https://example.com/jobs/technical-product-manager",
        metadata_json={},
        location_restrictions={},
    )
    source = EvidenceSource(
        source_type="TEST",
        source_reference=f"test://jd-version-stale/{uuid4()}",
    )
    db.add_all([job, source])
    db.flush()
    first_description = JobDescriptionVersion(
        job_id=job.id,
        version_number=1,
        content=(
            "Lead technical product delivery, define requirements, coordinate engineering teams, "
            "manage release planning and measure outcomes. "
        ) * 3,
        content_hash=f"jd-v1-{uuid4().hex}",
        completeness_status="COMPLETE",
        retrieved_at=now,
        requirement_extraction_status="COMPLETE",
    )
    evidence = Evidence(
        candidate_profile_id=profile.id,
        evidence_source_id=source.id,
        status="APPROVED",
        evidence_type="PROJECT",
        content={"summary": "Led technical product delivery and coordinated engineering teams."},
        revision=1,
    )
    db.add_all([first_description, evidence])
    db.commit()

    evaluation = asyncio.run(CandidateEvaluationService(db).evaluate(
        user.id, job.id, "technical-product-manager"
    ))
    assert evaluation.validity_status == "CURRENT"
    assert evaluation.job_description_version_id == first_description.id

    second_description = JobDescriptionVersion(
        job_id=job.id,
        version_number=2,
        content=(
            "Lead technical product delivery, own API integration strategy, coordinate engineering "
            "teams, define requirements, and measure cross-functional delivery outcomes. "
        ) * 3,
        content_hash=f"jd-v2-{uuid4().hex}",
        completeness_status="COMPLETE",
        retrieved_at=now + timedelta(minutes=1),
        requirement_extraction_status="PENDING",
    )
    db.add(second_description)
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
    db.add(job)
    db.flush()
    jd = JobDescriptionVersion(
        job_id=job.id,
        version_number=1,
        content=("Product delivery, stakeholder management, technical planning and roadmap ownership. " * 4),
        content_hash="expired-claim-support-description",
        completeness_status="COMPLETE",
        retrieved_at=now,
        requirement_extraction_status="COMPLETE",
    )
    db.add(jd)
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
        "type": "employment_experience",
        "revision": claim.revision,
        "content": {"employer": "Example Ltd", "role": "Product Manager", "years": 3},
        "reason": "NO_CURRENT_APPROVED_SUPPORTING_EVIDENCE",
    }]


def test_candidate_evaluation_override_is_append_only_candidate_owned_and_does_not_rewrite_decision(db):
    user, profile = make_user(db, "Override history candidate")
    other_user, other_profile = make_user(db, "Override history other candidate")
    now = datetime.now(timezone.utc)
    db.add(CandidatePreferences(
        candidate_profile_id=profile.id,
        preferences={"excludedIndustries": ["insurance"]},
        revision=1,
    ))
    job = Job(
        canonical_title="Operations Manager",
        company_name="Insurance Example",
        lifecycle_status="ACTIVE",
        freshness_status="CURRENT",
        application_url="https://example.com/jobs/operations-manager",
        metadata_json={"industry": "Insurance"},
        location_restrictions={},
    )
    db.add(job)
    db.flush()
    jd = JobDescriptionVersion(
        job_id=job.id,
        version_number=1,
        content=("Lead operations, coordinate teams, improve service quality, and manage delivery. " * 4),
        content_hash=f"override-history-{uuid4()}",
        completeness_status="COMPLETE",
        retrieved_at=now,
        requirement_extraction_status="COMPLETE",
    )
    db.add(jd)
    db.flush()

    evaluation = asyncio.run(CandidateEvaluationService(db).evaluate(
        user.id, job.id, "operations-manager",
    ))
    original_decision = evaluation.outcome

    first = OpportunityService(db).override_evaluation(
        user.id, evaluation.id, "WATCH", 0, "I want to monitor this role", "Review again next week",
    )
    assert first.revision == 1
    assert first.original_decision == original_decision
    assert first.new_disposition == "WATCH"
    assert original_decision == "REJECT"
    assert evaluation.hard_blockers
    assert evaluation.outcome == original_decision

    second = OpportunityService(db).override_evaluation(
        user.id, evaluation.id, "REVIEW", 1, "Need to verify role requirements",
    )
    assert second.revision == 2
    assert second.original_decision == original_decision
    assert [item.revision for item in OpportunityService(db).list_evaluation_overrides(user.id, evaluation.id)] == [1, 2]

    with pytest.raises(Exception) as error:
        OpportunityService(db).override_evaluation(user.id, evaluation.id, "APPLY", 0, "Stale override revision")
    assert getattr(error.value, "code", None) == "REVISION_CONFLICT"

    with pytest.raises(Exception) as error:
        OpportunityService(db).override_evaluation(
            user.id, evaluation.id, "APPLY", 2, "Cannot bypass a hard blocker",
        )
    assert getattr(error.value, "code", None) == "HARD_BLOCKER_OVERRIDE_FORBIDDEN"

    with pytest.raises(Exception) as error:
        OpportunityService(db).list_evaluation_overrides(other_user.id, evaluation.id)
    assert getattr(error.value, "code", None) == "EVALUATION_NOT_FOUND"


def test_evaluation_becomes_stale_when_its_approved_pathway_changes(db):
    user, profile = make_user(db, "Pathway evaluation candidate")
    now = datetime.now(timezone.utc)
    pathway = CareerPathway(
        candidate_profile_id=profile.id,
        title="Technical Product Management",
        role_family="Product Management",
        target_roles=["Technical Product Manager"],
        target_seniority=["Senior"],
        domains=["B2B SaaS"],
        supporting_evidence_refs=[],
        gaps=[],
        rationale="Candidate-defined target.",
        confidence=None,
        source_type="USER_DEFINED",
        approval_status="APPROVED",
        approved_at=now,
        approved_by_user_id=user.id,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    job = Job(
        canonical_title="Technical Product Manager",
        lifecycle_status="ACTIVE",
        freshness_status="CURRENT",
        application_url="https://example.com/jobs/pathway-stale-check",
        last_verified_at=now,
        metadata_json={},
        location_restrictions={},
    )
    db.add_all([pathway, job])
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
        content_hash="pathway-stale-job-description",
        completeness_status="COMPLETE",
        retrieved_at=now,
        requirement_extraction_status="COMPLETE",
    )
    source = EvidenceSource(source_type="TEST", source_reference="test://pathway-evidence")
    db.add(source)
    db.flush()
    route_attempt = JobVerificationAttempt(
        job_id=job.id,
        checked_url=job.application_url,
        final_url=job.application_url,
        status="ACTIVE",
        http_status_code=200,
        reason_code="JOB_PAGE_AND_APPLICATION_SIGNAL_PRESENT",
        evidence={"apply_marker_detected": True},
        verifier_version="test-verifier-v1",
        checked_at=now,
        created_at=now,
    )
    evidence = Evidence(
        candidate_profile_id=profile.id,
        evidence_source_id=source.id,
        status="APPROVED",
        evidence_type="PROJECT",
        content={"summary": "Led product delivery and API integrations."},
    )
    db.add_all([jd, route_attempt, evidence])
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
        assert data["input_snapshot"]["career_pathway_snapshot"][0]["id"] == str(pathway.id)
        assert data["input_snapshot"]["career_pathway_snapshot"][0]["revision"] == 1

        pathway.target_roles = ["Product Operations"]
        pathway.revision = 2
        pathway.updated_at = datetime.now(timezone.utc)
        db.flush()

        listed = client.get("/api/v1/candidate/evaluations")
        assert listed.status_code == 200
        row = next(item for item in listed.json()["data"] if item["id"] == data["id"])
        assert row["validity_status"] == "STALE"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()


def test_evaluation_becomes_stale_when_application_route_verification_changes(db):
    user, _profile = make_user(db, "Route Verification Candidate")
    now = datetime.now(timezone.utc)
    job = Job(
        canonical_title="Technical Product Manager",
        company_name="Example Employer",
        lifecycle_status="ACTIVE",
        freshness_status="CURRENT",
        application_url="https://example.com/jobs/route-verification",
        last_verified_at=now,
        metadata_json={},
        location_restrictions={},
    )
    db.add(job)
    db.flush()
    description = JobDescriptionVersion(
        job_id=job.id,
        version_number=1,
        content=(
            "Lead technical product delivery, define requirements, coordinate engineering teams, "
            "manage release planning and measure outcomes. "
        ) * 3,
        content_hash=f"route-verification-{uuid4().hex}",
        completeness_status="COMPLETE",
        retrieved_at=now,
        requirement_extraction_status="COMPLETE",
    )
    successful_attempt = JobVerificationAttempt(
        job_id=job.id,
        checked_url=job.application_url,
        final_url=job.application_url,
        status="ACTIVE",
        http_status_code=200,
        reason_code="JOB_PAGE_AND_APPLICATION_SIGNAL_PRESENT",
        evidence={"apply_marker_detected": True},
        verifier_version="test-verifier-v1",
        checked_at=now,
        created_at=now,
    )
    db.add_all([description, successful_attempt])
    db.commit()

    evaluation = asyncio.run(CandidateEvaluationService(db).evaluate(
        user.id, job.id, "technical-product-manager"
    ))
    assert evaluation.validity_status == "CURRENT"

    changed_attempt_at = datetime.now(timezone.utc)
    db.add(JobVerificationAttempt(
        job_id=job.id,
        checked_url=job.application_url,
        final_url=None,
        status="UNKNOWN",
        http_status_code=None,
        reason_code="VERIFICATION_TEMPORARILY_UNAVAILABLE",
        evidence={},
        verifier_version="test-verifier-v1",
        checked_at=changed_attempt_at,
        created_at=changed_attempt_at,
    ))
    db.commit()

    listed = OpportunityService(db).list_evaluations(user.id)
    assert len(listed) == 1
    assert listed[0].id == evaluation.id
    assert listed[0].validity_status == "STALE"


def test_punt_rejects_incomplete_job_description_even_when_evaluation_is_persisted(db):
    user, _profile = make_user(db, "Incomplete Description Candidate")
    now = datetime.now(timezone.utc)
    job = Job(
        canonical_title="Technical Product Manager",
        company_name="Example Employer",
        lifecycle_status="ACTIVE",
        freshness_status="CURRENT",
        application_url="https://example.com/jobs/incomplete-description",
        last_verified_at=now,
        metadata_json={},
        location_restrictions={},
    )
    db.add(job)
    db.flush()
    description = JobDescriptionVersion(
        job_id=job.id,
        version_number=1,
        content=(
            "Lead technical product delivery, define requirements, coordinate engineering teams, "
            "manage release planning and measure outcomes. "
        ) * 3,
        content_hash=f"incomplete-description-{uuid4().hex}",
        completeness_status="INCOMPLETE",
        retrieved_at=now,
        requirement_extraction_status="COMPLETE",
    )
    route_attempt = JobVerificationAttempt(
        job_id=job.id,
        checked_url=job.application_url,
        final_url=job.application_url,
        status="ACTIVE",
        http_status_code=200,
        reason_code="JOB_PAGE_AND_APPLICATION_SIGNAL_PRESENT",
        evidence={"apply_marker_detected": True},
        verifier_version="test-verifier-v1",
        checked_at=now,
        created_at=now,
    )
    db.add_all([description, route_attempt])
    db.commit()

    evaluation = asyncio.run(CandidateEvaluationService(db).evaluate(
        user.id, job.id, "technical-product-manager"
    ))
    assert evaluation.validity_status == "CURRENT"

    with pytest.raises(DomainError) as raised:
        OpportunityService(db).create_punt(user.id, evaluation.id)

    assert raised.value.code == "JOB_DESCRIPTION_NOT_EVALUABLE"
    assert raised.value.status_code == 409
    assert db.scalar(select(func.count(Punt.id))) == 0
