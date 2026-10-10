import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy import create_engine

from app.api.errors import DomainError
from app.application.application_packages.schemas import ApplicationPackageCreate
from app.application.application_packages.service import ApplicationPackageService
from app.application.application_packages.review import ApplicationPackageReviewService
from app.application.application_packages.claim_validation import PackageClaimValidationService
from app.infrastructure.db.models import (
    Application, ApplicationPackage, ApplicationPackageItem, CandidateProfile,
    AIExecution, ApplicationPackageClaimValidation, Evidence, EvidenceSource, Job, User,
)

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


def candidate(db):
    user = User(display_name=f"Candidate {uuid4()}")
    db.add(user)
    db.flush()
    profile = CandidateProfile(user_id=user.id, status="ACTIVE")
    db.add(profile)
    db.flush()
    return user, profile


def application(db, profile):
    job = Job(canonical_title="Technical Product Manager", company_name="Example Co")
    db.add(job)
    db.flush()
    app_record = Application(candidate_profile_id=profile.id, job_id=job.id, status="PLANNED")
    db.add(app_record)
    db.flush()
    return app_record


def package_payload(evidence_id, cover_letter="First cover letter"):
    return ApplicationPackageCreate.model_validate({
        "items": [
            {
                "artifact_type": "tailored_cv",
                "content": {"content": "Tailored CV v1"},
                "evidence_refs": [str(evidence_id)],
                "confidence": "medium",
            },
            {
                "artifact_type": "cover_letter",
                "content": {"content": cover_letter},
                "evidence_refs": [str(evidence_id)],
                "confidence": "medium",
                "unsupported_claims": [],
            },
            {
                "artifact_type": "application_q_and_a",
                "confidence": "medium",
                "content": {"answers": [{
                    "question": "Why this role?",
                    "answer": "An editable, evidence-based draft.",
                    "evidence_basis": [str(evidence_id)],
                    "confidence": "medium",
                    "unsupported_claims": [],
                    "missing_information": [],
                }]},
                "evidence_refs": [str(evidence_id)],
            },
        ]
    })


def test_package_versions_preserve_artifacts_and_snapshot_source_evidence(db):
    user, profile = candidate(db)
    app_record = application(db, profile)
    source = EvidenceSource(
        source_type="candidate_upload",
        source_reference=f"test://evidence/{uuid4()}",
        metadata_json={"test": True},
    )
    db.add(source)
    db.flush()
    evidence = Evidence(
        candidate_profile_id=profile.id,
        evidence_source_id=source.id,
        status="APPROVED",
        evidence_type="employment_record",
        content={"role": "Product Manager", "organization": "Example Ltd"},
    )
    db.add(evidence)
    db.flush()

    service = ApplicationPackageService(db)
    first = service.create(user.id, app_record.id, package_payload(evidence.id))
    first_items = {item.artifact_type: item for item in db.scalars(
        select(ApplicationPackageItem).where(ApplicationPackageItem.package_id == first.id)
    ).all()}

    assert first.version_number == 1
    assert set(first_items) == {"tailored_cv", "cover_letter", "application_q_and_a"}
    assert first.source_snapshot["candidate_profile"]["revision"] == profile.revision
    assert first.source_snapshot["evidence"][0]["content"]["organization"] == "Example Ltd"
    original_cover_hash = first_items["cover_letter"].content_hash

    second = service.create(
        user.id, app_record.id,
        ApplicationPackageCreate.model_validate({
            "items": [{
                "artifact_type": "cover_letter",
                "content": {"content": "Revised cover letter"},
                "evidence_refs": [str(evidence.id)],
            }]
        }),
    )
    assert second.version_number == 2
    assert first_items["cover_letter"].content_hash == original_cover_hash
    assert first_items["cover_letter"].content["content"] == "First cover letter"
    assert second.id != first.id


def test_package_service_denies_cross_candidate_application_access(db):
    user, profile = candidate(db)
    _, foreign_profile = candidate(db)
    foreign_application = application(db, foreign_profile)
    service = ApplicationPackageService(db)

    with pytest.raises(DomainError) as error:
        service.create(user.id, foreign_application.id, package_payload(uuid4()))

    assert error.value.status_code == 404
    assert db.scalar(select(ApplicationPackage)) is None



def attach_supported_validation_report(db, user, package, evidence_id):
    execution = AIExecution(
        user_id=user.id,
        task="claim_evidence_validation",
        provider="google",
        model="gemini-test",
        status="SUCCEEDED",
    )
    db.add(execution)
    db.flush()
    package_items = db.scalars(select(ApplicationPackageItem).where(ApplicationPackageItem.package_id == package.id)).all()
    report = {
        "artifact_snapshot": [{"id": str(item.id), "artifact_type": item.artifact_type, "content_hash": item.content_hash} for item in package_items],
        "audited_artifact_types": ["tailored_cv", "cover_letter", "application_q_and_a"],
        "checks": [
            {
                "artifact_type": artifact_type,
                "claim_text": "Documented candidate fact",
                "status": "supported",
                "evidence_refs": [str(evidence_id)],
                "rationale": "Supported by approved evidence.",
                "confidence": "high",
            }
            for artifact_type in ("tailored_cv", "cover_letter", "application_q_and_a")
        ],
        "coverage_complete": True,
        "unassessed_claims": [],
        "candidate_review_required": False,
    }
    db.add(ApplicationPackageClaimValidation(
        package_id=package.id,
        ai_execution_id=execution.id,
        report=report,
        report_hash="a" * 64,
    ))
    db.flush()


def test_package_requires_complete_traceable_evidence_before_candidate_approval(db):
    user, profile = candidate(db)
    app_record = application(db, profile)
    source = EvidenceSource(
        source_type="candidate_upload",
        source_reference=f"test://evidence/{uuid4()}",
        metadata_json={"test": True},
    )
    db.add(source)
    db.flush()
    evidence = Evidence(
        candidate_profile_id=profile.id,
        evidence_source_id=source.id,
        status="APPROVED",
        evidence_type="employment_record",
        content={"role": "Product Manager", "organization": "Example Ltd"},
    )
    db.add(evidence)
    db.flush()

    package = ApplicationPackageService(db).create(
        user.id, app_record.id, package_payload(evidence.id)
    )
    attach_supported_validation_report(db, user, package, evidence.id)
    review = ApplicationPackageReviewService(db)
    prepared = review.prepare(user.id, app_record.id, package.id)

    assert prepared["ready"] is True
    assert prepared["blockers"] == []
    assert prepared["package"].status == "READY_FOR_REVIEW"

    approved = review.decide(user.id, app_record.id, package.id, "approve")
    assert approved.status == "APPROVED"


def test_package_review_blocks_unsupported_claims_and_unapproved_evidence(db):
    user, profile = candidate(db)
    app_record = application(db, profile)
    source = EvidenceSource(
        source_type="candidate_upload",
        source_reference=f"test://evidence/{uuid4()}",
        metadata_json={"test": True},
    )
    db.add(source)
    db.flush()
    evidence = Evidence(
        candidate_profile_id=profile.id,
        evidence_source_id=source.id,
        status="DRAFT",
        evidence_type="employment_record",
        content={"role": "Product Manager"},
    )
    db.add(evidence)
    db.flush()

    payload = package_payload(evidence.id)
    payload.items[1].unsupported_claims.append("Claim requires candidate confirmation")
    package = ApplicationPackageService(db).create(user.id, app_record.id, payload)
    result = ApplicationPackageReviewService(db).prepare(user.id, app_record.id, package.id)

    assert result["ready"] is False
    codes = {blocker["code"] for blocker in result["blockers"]}
    assert "UNSUPPORTED_CLAIMS_REMAIN" in codes
    assert "EVIDENCE_NOT_APPROVED" in codes
    assert package.status == "DRAFT"


def test_package_cannot_receive_review_decision_before_readiness_gate(db):
    user, profile = candidate(db)
    app_record = application(db, profile)
    package = ApplicationPackageService(db).create(
        user.id, app_record.id,
        ApplicationPackageCreate.model_validate({
            "items": [{"artifact_type": "cover_letter", "content": {"content": "Draft"}}]
        }),
    )

    with pytest.raises(DomainError) as error:
        ApplicationPackageReviewService(db).decide(
            user.id, app_record.id, package.id, "approve"
        )

    assert error.value.status_code == 409
    assert error.value.code == "APPLICATION_PACKAGE_NOT_READY"


def test_package_list_and_get_deny_cross_candidate_resource_access(db):
    owner, owner_profile = candidate(db)
    foreign_owner, foreign_profile = candidate(db)
    owner_application = application(db, owner_profile)
    foreign_application = application(db, foreign_profile)

    service = ApplicationPackageService(db)
    foreign_package = service.create(
        foreign_owner.id,
        foreign_application.id,
        ApplicationPackageCreate.model_validate({
            "items": [{
                "artifact_type": "cover_letter",
                "content": {"content": "Private foreign-candidate artifact"},
            }]
        }),
    )

    with pytest.raises(DomainError) as list_error:
        service.list_for_application(owner.id, foreign_application.id)
    assert list_error.value.status_code == 404

    # A valid application ID must not make another candidate's package ID readable.
    with pytest.raises(DomainError) as package_error:
        service.get(owner.id, owner_application.id, foreign_package.id)
    assert package_error.value.status_code == 404

    with pytest.raises(DomainError) as nested_error:
        service.get(owner.id, foreign_application.id, foreign_package.id)
    assert nested_error.value.status_code == 404


def test_package_review_prepare_and_decide_deny_cross_candidate_access(db):
    owner, owner_profile = candidate(db)
    foreign_owner, foreign_profile = candidate(db)
    owner_application = application(db, owner_profile)
    foreign_application = application(db, foreign_profile)
    foreign_package = ApplicationPackageService(db).create(
        foreign_owner.id,
        foreign_application.id,
        ApplicationPackageCreate.model_validate({
            "items": [{
                "artifact_type": "cover_letter",
                "content": {"content": "Private foreign-candidate artifact"},
            }]
        }),
    )
    review = ApplicationPackageReviewService(db)

    with pytest.raises(DomainError) as prepare_error:
        review.prepare(owner.id, foreign_application.id, foreign_package.id)
    assert prepare_error.value.status_code == 404

    with pytest.raises(DomainError) as decision_error:
        review.decide(owner.id, owner_application.id, foreign_package.id, "approve")
    assert decision_error.value.status_code == 404


def test_package_claim_validation_denies_cross_candidate_package_access(db):
    owner, owner_profile = candidate(db)
    foreign_owner, foreign_profile = candidate(db)
    owner_application = application(db, owner_profile)
    foreign_application = application(db, foreign_profile)
    foreign_package = ApplicationPackageService(db).create(
        foreign_owner.id,
        foreign_application.id,
        ApplicationPackageCreate.model_validate({
            "items": [{
                "artifact_type": "cover_letter",
                "content": {"content": "Private foreign-candidate artifact"},
            }]
        }),
    )

    with pytest.raises(DomainError) as error:
        asyncio.run(PackageClaimValidationService(db).validate(
            owner.id, owner_application.id, foreign_package.id
        ))

    assert error.value.status_code == 404
    assert error.value.code == "APPLICATION_PACKAGE_NOT_FOUND"


def test_package_claim_validation_downgrades_incomplete_artifact_coverage(db, monkeypatch):
    user, profile = candidate(db)
    app_record = application(db, profile)
    package = ApplicationPackageService(db).create(
        user.id,
        app_record.id,
        ApplicationPackageCreate.model_validate({
            "items": [
                {"artifact_type": "tailored_cv", "content": {"content": "Tailored CV text"}},
                {"artifact_type": "cover_letter", "content": {"content": "A factual cover letter claim."}},
            ]
        }),
    )
    execution = AIExecution(
        user_id=user.id,
        task="claim_evidence_validation",
        provider="google",
        model="gemini-test",
        status="SUCCEEDED",
    )
    db.add(execution)
    db.flush()

    async def fake_execute_ai_task(_request, *, db, user_id, request_id=None):
        assert user_id == user.id
        return SimpleNamespace(
            execution_id=execution.id,
            parsed_data={
                "checks": [{
                    "artifact_type": "cover_letter",
                    "claim_text": "A factual cover letter claim.",
                    "status": "unsupported",
                    "evidence_refs": [],
                    "rationale": "No approved evidence supports the claim.",
                    "confidence": "low",
                }],
                "audited_artifact_types": ["cover_letter"],
                "unassessed_claims": [],
                "coverage_complete": True,
            },
        )

    monkeypatch.setattr(
        "app.application.application_packages.claim_validation.execute_ai_task",
        fake_execute_ai_task,
    )
    result = asyncio.run(
        PackageClaimValidationService(db).validate(user.id, app_record.id, package.id)
    )

    report = result["report"]
    assert report["coverage_complete"] is False
    assert report["candidate_review_required"] is True
    assert any("tailored_cv" in item for item in report["unassessed_claims"])


def test_package_review_blocks_claim_report_missing_audited_artifact_type(db):
    user, profile = candidate(db)
    app_record = application(db, profile)
    source = EvidenceSource(
        source_type="candidate_upload",
        source_reference=f"test://evidence/{uuid4()}",
        metadata_json={"test": True},
    )
    db.add(source)
    db.flush()
    evidence = Evidence(
        candidate_profile_id=profile.id,
        evidence_source_id=source.id,
        status="APPROVED",
        evidence_type="employment_record",
        content={"role": "Product Manager", "organization": "Example Ltd"},
    )
    db.add(evidence)
    db.flush()

    package = ApplicationPackageService(db).create(
        user.id, app_record.id, package_payload(evidence.id)
    )
    attach_supported_validation_report(db, user, package, evidence.id)
    validation = db.scalar(
        select(ApplicationPackageClaimValidation).where(
            ApplicationPackageClaimValidation.package_id == package.id
        )
    )
    validation.report = {
        **validation.report,
        "audited_artifact_types": ["tailored_cv", "cover_letter"],
    }
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert "SEMANTIC_CLAIM_ARTIFACT_COVERAGE_INCOMPLETE" in {
        blocker["code"] for blocker in result["blockers"]
    }


def test_package_claim_validation_rejects_audited_artifact_outside_package(db, monkeypatch):
    from fastapi import HTTPException

    user, profile = candidate(db)
    app_record = application(db, profile)
    package = ApplicationPackageService(db).create(
        user.id,
        app_record.id,
        ApplicationPackageCreate.model_validate({
            "items": [{
                "artifact_type": "cover_letter",
                "content": {"content": "A factual cover letter claim."},
            }]
        }),
    )
    execution = AIExecution(
        user_id=user.id,
        task="claim_evidence_validation",
        provider="google",
        model="gemini-test",
        status="SUCCEEDED",
    )
    db.add(execution)
    db.flush()

    async def fake_execute_ai_task(_request, *, db, user_id, request_id=None):
        return SimpleNamespace(
            execution_id=execution.id,
            parsed_data={
                "checks": [{
                    "artifact_type": "cover_letter",
                    "claim_text": "A factual cover letter claim.",
                    "status": "unsupported",
                    "evidence_refs": [],
                    "rationale": "No approved evidence supports the claim.",
                    "confidence": "low",
                }],
                "audited_artifact_types": ["cover_letter", "tailored_cv"],
                "unassessed_claims": [],
                "coverage_complete": True,
            },
        )

    monkeypatch.setattr(
        "app.application.application_packages.claim_validation.execute_ai_task",
        fake_execute_ai_task,
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            PackageClaimValidationService(db).validate(user.id, app_record.id, package.id)
        )

    assert error.value.status_code == 502
    assert error.value.detail["code"] == "AI_INVALID_OUTPUT"
