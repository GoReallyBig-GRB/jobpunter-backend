"""Deterministic PostgreSQL-backed candidate journey across core user workflows.

External providers are replaced at their boundaries; this test verifies persisted
cross-domain ownership, provenance and workflow links rather than production provider
availability. Live-provider gates remain covered by the runtime checklist.
"""
import hashlib
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_principal
from app.application.candidates.service import CandidateService
from app.application.documents.imports import DocumentImportService
from app.application.cv_studio.service import CVStudioService
from app.application.application_packages.schemas import ApplicationPackageCreate
from app.application.application_packages.service import ApplicationPackageService
from app.application.opportunities.service import OpportunityService
from app.application.jobs.requirement_review import review_requirement
from app.application.jobs.requirement_review import review_requirement
from app.application.notifications.service import punt_notification_handler
from app.application.support.service import create_ticket
from app.application.support.notifications import support_ticket_notification_handler
from app.infrastructure.db.models import (
    Application, CandidateEvaluation, CandidatePreferences, CandidateProfile,
    CareerPathway, CareerPathwayEvent, Evidence, GovernanceConfigVersion, Job,
    JobDescriptionVersion, JobProvenance, JobRequirement, JobVerificationAttempt,
    Notification, Punt, Source, User,
)
from app.infrastructure.db.session import get_db
from app.main import app
from app.ports.identity import AuthenticatedPrincipal
from app.ports.object_storage import StoredObject


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


class MemoryObjectStorage:
    def __init__(self):
        self.objects = {}

    def put(self, storage_key, content, content_type):
        self.objects[storage_key] = content
        return StoredObject(storage_key, hashlib.sha256(content).hexdigest(), len(content))

    def get(self, storage_key):
        return self.objects[storage_key]

    def delete(self, storage_key):
        self.objects.pop(storage_key, None)

    def create_download_url(self, storage_key, expires_in_seconds):
        return f"https://private-storage.example.test/{storage_key}?ttl={expires_in_seconds}"


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


def test_candidate_journey_profile_to_job_package_notifications_and_support(db, monkeypatch):
    now = datetime.now(timezone.utc)
    user = User(display_name=f"End-to-end candidate {uuid4()}", status="ACTIVE")
    db.add(user)
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
    session_factory = sessionmaker(
        bind=connection, class_=Session, join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    try:
        # 1. Candidate profile and preferences are created through their authenticated APIs.
        profile_response = client.get("/api/v1/candidate/profile")
        assert profile_response.status_code == 200, profile_response.text
        profile_id = UUID(profile_response.json()["data"]["id"])
        profile_update = client.patch(
            "/api/v1/candidate/profile",
            json={
                "expected_revision": 1,
                "sections": {
                    "identity": {"full_name": "Journey Candidate"},
                    "professional_identity": {"current_role": "Product Manager"},
                    "career_history": [{"role": "Product Manager", "employer": "Example Co"}],
                },
            },
        )
        assert profile_update.status_code == 200, profile_update.text
        assert profile_update.json()["data"]["revision"] == 2

        preferences = client.put(
            "/api/v1/candidate/preferences",
            json={
                "expectedRevision": 0,
                "preferences": {
                    "targetRoles": ["Technical Product Manager"],
                    "preferredLocations": ["Nigeria"],
                    "remotePreference": "REMOTE_PREFERRED",
                    "maxApplicationFee": 0,
                    "transferabilityPolicy": "STRICT",
                },
            },
        )
        assert preferences.status_code == 200, preferences.text
        assert preferences.json()["revision"] == 1
        persisted_profile = db.get(CandidateProfile, profile_id)
        assert persisted_profile is not None and persisted_profile.user_id == user.id
        preference_row = db.scalar(select(CandidatePreferences).where(
            CandidatePreferences.candidate_profile_id == profile_id
        ))
        assert preference_row is not None and preference_row.revision == 1

        # 2. A candidate-approved pathway is persisted with an auditable event.
        pathway = CareerPathway(
            candidate_profile_id=profile_id,
            title="Technical Product Management",
            role_family="Product Management",
            target_roles=["Technical Product Manager"],
            target_seniority=["Senior"],
            domains=["B2B SaaS"],
            supporting_evidence_refs=[],
            gaps=[],
            rationale="Candidate-defined pathway for this deterministic journey.",
            confidence=None,
            source_type="USER_DEFINED",
            approval_status="APPROVED",
            approved_at=now,
            approved_by_user_id=user.id,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        db.add(pathway)
        db.flush()
        db.add(CareerPathwayEvent(
            career_pathway_id=pathway.id,
            candidate_profile_id=profile_id,
            actor_user_id=user.id,
            event_type="CREATED",
            previous_status=None,
            new_status="APPROVED",
            pathway_revision=1,
            reason="Candidate created pathway",
            details={"source_type": "USER_DEFINED"},
            created_at=now,
        ))
        db.flush()

        # 3. Import a document using deterministic in-memory storage, then explicitly
        # confirm it and approve the resulting evidence before it can support evaluation.
        import_service = DocumentImportService(db, storage=MemoryObjectStorage())
        import_record = import_service.upload(
            user.id, "resume.txt", "text/plain",
            b"Technical Product Manager at Example Co. Delivered SQL query design and API integration.",
        )
        import_service.parse(user.id, import_record.id)
        import_service.preview(user.id, import_record.id)
        conflict_check = import_service.identify_conflicts(user.id, import_record.id)
        confirmed = import_service.confirm(
            user.id, import_record.id, expected_revision=conflict_check.revision,
        )
        saved_import, evidence = import_service.save(user.id, import_record.id)
        assert saved_import.status == "SAVED"
        assert evidence.status == "DRAFT"
        evidence_service = CandidateService(db)
        evidence_service.transition_evidence(
            user.id, evidence.id, "PENDING_REVIEW", expected_revision=1,
        )
        evidence_service.transition_evidence(
            user.id, evidence.id, "APPROVED", expected_revision=2,
        )
        db.flush()
        evidence = db.get(Evidence, evidence.id)
        assert evidence.status == "APPROVED"
        assert evidence.content["document_import_id"] == str(import_record.id)

        # 4. Seed a public, verified canonical job and prove it is discoverable in Job Pool.
        source = Source(
            name=f"Journey source {uuid4()}",
            source_type="GREENHOUSE_JOB_BOARD",
            status="ACTIVE",
            permission_review_status="APPROVED",
            permission_review_reference="test-policy://candidate-journey",
            permission_reviewed_at=now,
            operational_state="AVAILABLE",
        )
        db.add(source)
        db.flush()
        job = Job(
            identity_status="CANONICAL",
            lifecycle_status="ACTIVE",
            freshness_status="FRESH",
            canonical_title="Technical Product Manager",
            company_name="Example Employer",
            application_url="https://jobs.example.test/technical-product-manager",
            location_country="Nigeria",
            work_mode="REMOTE",
            location_restrictions={},
            metadata_json={"application_route_type": "DIRECT_EMPLOYER"},
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
            submitted_by_user_id=None,
            source_url=job.application_url,
            normalized_source_url=job.application_url,
            observation_type="ACQUIRED",
            asserted_by="SOURCE",
            observed_at=now,
            confidence=1.0,
            status="OBSERVED",
            metadata_json={},
        ))
        jd = JobDescriptionVersion(
            job_id=job.id,
            version_number=1,
            content=("Responsibilities include product delivery and API integration. "
                     "Experience with SQL query design is required. Coordinate stakeholders. ") * 5,
            content_hash=f"journey-jd-{uuid4().hex}",
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
        pool = client.get("/api/v1/candidate/jobs/pool")
        assert pool.status_code == 200, pool.text
        assert any(item["id"] == str(job.id) for item in pool.json()["data"])

        # 5. Review the extracted requirement, evaluate with a deterministic matcher,
        # and create a Punt only from the persisted APPLY decision.
        active_config = db.scalar(select(GovernanceConfigVersion).where(
            GovernanceConfigVersion.config_key == "decision.rules.candidate_evaluation",
            GovernanceConfigVersion.status == "PUBLISHED",
        ).with_for_update())
        if active_config is not None:
            active_config.status = "RETIRED"
            db.flush()
        latest_rule_version = db.scalar(select(func.max(GovernanceConfigVersion.version)).where(
            GovernanceConfigVersion.config_key == "decision.rules.candidate_evaluation"
        )) or 0
        rule_version = latest_rule_version + 1
        db.add(GovernanceConfigVersion(
            config_key="decision.rules.candidate_evaluation",
            version=rule_version,
            status="PUBLISHED",
            payload={"apply_min_score": 80, "review_min_score": 35},
            payload_fingerprint=hashlib.sha256(f"journey-rules-{rule_version}".encode()).hexdigest(),
            created_by_user_id=user.id,
            created_at=now,
            published_by_user_id=user.id,
            published_at=now,
            supersedes_id=active_config.id if active_config else None,
        ))
        db.flush()
        review_requirement(
            db, requirement_id=requirement.id, reviewer_user_id=user.id,
            decision="CONFIRM", severity="MUST_HAVE", dimension="TECHNICAL",
        )

        async def deterministic_matcher(**kwargs):
            assert kwargs["description_version_id"] == jd.id
            assert kwargs["approved_evidence"]
            return [{
                "requirement_id": str(requirement.id),
                "requirement_text": requirement.description,
                "status": "DIRECT",
                "rationale": "Approved imported evidence supports the confirmed SQL requirement.",
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
                "config_fingerprint": "journey-governed-config",
            }], None

        monkeypatch.setattr(
            "app.application.opportunities.evaluation_service.match_requirements_to_evidence",
            deterministic_matcher,
        )
        evaluated = client.post(
            "/api/v1/candidate/evaluations",
            json={"job_id": str(job.id), "target_role_key": "technical-product-manager"},
        )
        assert evaluated.status_code == 201, evaluated.text
        evaluation_data = evaluated.json()["data"]
        assert evaluation_data["outcome"] == "APPLY"
        evaluation = db.get(CandidateEvaluation, UUID(evaluation_data["id"]))
        assert evaluation is not None and evaluation.candidate_profile_id == profile_id
        punt = OpportunityService(db).create_punt(user.id, evaluation.id)
        assert punt.candidate_profile_id == profile_id and punt.job_id == job.id

        # 6. Generate and approve a versioned Master CV, then create an application
        # package tied to that exact CV and the same approved imported evidence.
        cv_service = CVStudioService(db)
        master_cv = cv_service.regenerate_master(user.id, expected_profile_revision=2)
        approval = cv_service.approve_version(user.id, "master", master_cv.version_number)
        assert approval.cv_version_id == master_cv.id
        application = OpportunityService(db).create_application(user.id, punt.id)
        assert application.candidate_profile_id == profile_id
        package_payload = ApplicationPackageCreate.model_validate({
            "items": [
                {
                    "artifact_type": "tailored_cv",
                    "content": {"content": "Tailored CV based on the approved Master CV."},
                    "evidence_refs": [str(evidence.id)],
                    "confidence": "medium",
                    "unsupported_claims": [],
                    "source_cv_version_id": str(master_cv.id),
                },
                {
                    "artifact_type": "cover_letter",
                    "content": {"content": "I am applying for the Technical Product Manager role."},
                    "evidence_refs": [str(evidence.id)],
                    "confidence": "medium",
                    "unsupported_claims": [],
                },
                {
                    "artifact_type": "application_q_and_a",
                    "content": {"answers": [{
                        "question": "Why this role?",
                        "answer": "My approved evidence demonstrates product delivery and SQL query design.",
                        "evidence_basis": [str(evidence.id)],
                        "confidence": "medium",
                        "unsupported_claims": [],
                        "missing_information": [],
                    }]},
                    "evidence_refs": [str(evidence.id)],
                    "confidence": "medium",
                    "unsupported_claims": [],
                },
            ]
        })
        package = ApplicationPackageService(db).create(user.id, application.id, package_payload)
        assert package.status == "DRAFT"
        assert package.source_snapshot["application"]["id"] == str(application.id)
        assert any(row["id"] == str(master_cv.id) for row in package.source_snapshot["cv_versions"])
        assert package.source_snapshot["evidence"][0]["id"] == str(evidence.id)

        # 7. Exercise persisted notification fanout and the candidate support workflow.
        punt_notification_handler(session_factory)({"punt_id": str(punt.id)})
        notification = db.scalar(select(Notification).where(
            Notification.user_id == user.id,
            Notification.resource_id == punt.id,
        ))
        assert notification is not None and notification.event_type == "PUNT_CREATED"

        ticket, created = create_ticket(
            db, user.id,
            idempotency_key=f"journey-support-{uuid4()}",
            title="Application package question",
            issue_details=f"Please review package {package.id}.",
        )
        assert created is True and ticket.candidate_user_id == user.id
        support_ticket_notification_handler(session_factory)({
            "ticket_id": str(ticket.id), "kind": "CREATED",
        })
        support_notifications = db.scalars(select(Notification).where(
            Notification.resource_id == ticket.id,
        )).all()
        assert any(row.user_id == user.id for row in support_notifications)

        # Final cross-domain invariant: all user-owned records resolve to the same
        # candidate profile, and the package remains linked to the application/job.
        db.flush()
        assert db.get(CandidateProfile, profile_id).user_id == user.id
        assert db.get(Punt, punt.id).candidate_profile_id == profile_id
        assert db.get(Application, application.id).candidate_profile_id == profile_id
        assert db.get(Application, application.id).job_id == job.id
        assert package.application_id == application.id
        assert db.scalar(select(CandidateEvaluation).where(
            CandidateEvaluation.id == evaluation.id,
            CandidateEvaluation.candidate_profile_id == profile_id,
        )) is not None
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()
