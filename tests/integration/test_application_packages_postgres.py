import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy import create_engine

from app.api.errors import DomainError
from app.application.application_packages.schemas import ApplicationPackageCreate
from app.application.application_packages.service import ApplicationPackageService, artifact_metadata_hash, hash_source_snapshot
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
    assert first.source_snapshot_hash == hash_source_snapshot(first.source_snapshot)
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
    assert second.source_snapshot_hash == hash_source_snapshot(second.source_snapshot)
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
    evidence = db.get(Evidence, evidence_id)
    report = {
        "evidence_snapshot": [{
            "id": str(evidence.id),
            "revision": evidence.revision,
            "content_hash": hashlib.sha256(json.dumps(
                evidence.content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")).hexdigest(),
        }],
        "artifact_snapshot": [{
            "id": str(item.id),
            "artifact_type": item.artifact_type,
            "content_hash": item.content_hash,
            "metadata_hash": artifact_metadata_hash(item),
        } for item in package_items],
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
    report_hash = hashlib.sha256(json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    db.add(ApplicationPackageClaimValidation(
        package_id=package.id,
        ai_execution_id=execution.id,
        report=report,
        report_hash=report_hash,
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
    validation.report_hash = hashlib.sha256(json.dumps(
        validation.report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
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


def test_package_review_blocks_missing_evidence_provenance_snapshot(db):
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
    validation.report = {**validation.report, "evidence_snapshot": []}
    validation.report_hash = hashlib.sha256(json.dumps(
        validation.report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert "SEMANTIC_VALIDATION_EVIDENCE_COVERAGE_INCOMPLETE" in {
        blocker["code"] for blocker in result["blockers"]
    }


def test_package_review_blocks_tampered_claim_audit_report(db):
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
    validation.report = {**validation.report, "coverage_complete": False}
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert "SEMANTIC_CLAIM_REPORT_INTEGRITY_INVALID" in {
        blocker["code"] for blocker in result["blockers"]
    }


def test_package_review_blocks_report_with_unassessed_claims_even_if_coverage_flag_is_true(db):
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
        "unassessed_claims": ["One factual claim was not reviewed."],
        "coverage_complete": True,
    }
    validation.report_hash = hashlib.sha256(json.dumps(
        validation.report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert "SEMANTIC_CLAIM_COVERAGE_INCOMPLETE" in {
        blocker["code"] for blocker in result["blockers"]
    }


def test_package_review_blocks_duplicate_evidence_provenance_entries(db):
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
    duplicate_snapshot = validation.report["evidence_snapshot"][0]
    validation.report = {
        **validation.report,
        "evidence_snapshot": [duplicate_snapshot, duplicate_snapshot],
    }
    validation.report_hash = hashlib.sha256(json.dumps(
        validation.report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert "SEMANTIC_VALIDATION_EVIDENCE_COVERAGE_INCOMPLETE" in {
        blocker["code"] for blocker in result["blockers"]
    }


def test_package_review_fails_closed_on_non_object_claim_audit_report(db):
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
    validation.report = ["not", "an", "object"]
    validation.report_hash = hashlib.sha256(json.dumps(
        validation.report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert "SEMANTIC_CLAIM_REPORT_INTEGRITY_INVALID" in {
        blocker["code"] for blocker in result["blockers"]
    }


def test_package_review_blocks_duplicate_artifact_provenance_entries(db):
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
    duplicate_snapshot = validation.report["artifact_snapshot"][0]
    validation.report = {
        **validation.report,
        "artifact_snapshot": [*validation.report["artifact_snapshot"], duplicate_snapshot],
    }
    validation.report_hash = hashlib.sha256(json.dumps(
        validation.report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert "SEMANTIC_VALIDATION_ARTIFACT_CHANGED" in {
        blocker["code"] for blocker in result["blockers"]
    }


def test_package_claim_validation_rejects_evidence_not_linked_to_claim_artifact(db, monkeypatch):
    from fastapi import HTTPException

    user, profile = candidate(db)
    app_record = application(db, profile)
    evidence_ids = []
    for index in range(2):
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
            content={"role": "Product Manager", "organization": f"Example {index} Ltd"},
        )
        db.add(evidence)
        db.flush()
        evidence_ids.append(evidence.id)

    package = ApplicationPackageService(db).create(
        user.id,
        app_record.id,
        ApplicationPackageCreate.model_validate({
            "items": [
                {
                    "artifact_type": "tailored_cv",
                    "content": {"content": "CV fact supported by evidence one."},
                    "evidence_refs": [str(evidence_ids[0])],
                },
                {
                    "artifact_type": "cover_letter",
                    "content": {"content": "Cover letter fact supported by evidence two."},
                    "evidence_refs": [str(evidence_ids[1])],
                },
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
        return SimpleNamespace(
            execution_id=execution.id,
            parsed_data={
                "checks": [{
                    "artifact_type": "cover_letter",
                    "claim_text": "Cover letter fact.",
                    "status": "unsupported",
                    "evidence_refs": [str(evidence_ids[0])],
                    "rationale": "This reference belongs to another artifact.",
                    "confidence": "low",
                }],
                "audited_artifact_types": ["tailored_cv", "cover_letter"],
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
    assert error.value.detail["code"] == "AI_INVALID_EVIDENCE_REFERENCE"


@pytest.mark.parametrize("coverage_value", ["false", 0, None])
def test_package_review_requires_boolean_true_for_claim_coverage(db, coverage_value):
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
        "coverage_complete": coverage_value,
    }
    validation.report_hash = hashlib.sha256(json.dumps(
        validation.report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert "SEMANTIC_CLAIM_COVERAGE_INCOMPLETE" in {
        blocker["code"] for blocker in result["blockers"]
    }


def test_package_review_requires_boolean_false_for_candidate_review_flag(db):
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
        "candidate_review_required": "false",
    }
    validation.report_hash = hashlib.sha256(json.dumps(
        validation.report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert "SEMANTIC_CLAIM_REVIEW_REQUIRED" in {
        blocker["code"] for blocker in result["blockers"]
    }


def test_package_review_blocks_supported_claim_without_evidence_refs(db):
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
    checks = list(validation.report["checks"])
    checks[0] = {**checks[0], "evidence_refs": []}
    validation.report = {**validation.report, "checks": checks}
    validation.report_hash = hashlib.sha256(json.dumps(
        validation.report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert "SEMANTIC_CLAIM_EVIDENCE_REQUIRED" in {
        blocker["code"] for blocker in result["blockers"]
    }


def test_package_review_blocks_claim_evidence_unlinked_from_its_artifact(db):
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
    cover_letter_item = db.scalar(
        select(ApplicationPackageItem).where(
            ApplicationPackageItem.package_id == package.id,
            ApplicationPackageItem.artifact_type == "cover_letter",
        )
    )
    cover_letter_item.evidence_refs = []
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert "SEMANTIC_CLAIM_EVIDENCE_NOT_LINKED_TO_ARTIFACT" in {
        blocker["code"] for blocker in result["blockers"]
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("artifact_type", ["not", "a", "string"]),
        ("content_hash", {"malformed": True}),
        ("metadata_hash", {"malformed": True}),
    ],
)
def test_package_review_fails_closed_on_malformed_artifact_snapshot_fields(db, field, value):
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
    snapshot = [dict(row) for row in validation.report["artifact_snapshot"]]
    snapshot[0][field] = value
    validation.report = {**validation.report, "artifact_snapshot": snapshot}
    validation.report_hash = hashlib.sha256(json.dumps(
        validation.report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert "SEMANTIC_VALIDATION_ARTIFACT_CHANGED" in {
        blocker["code"] for blocker in result["blockers"]
    }


def test_package_review_fails_closed_on_non_string_claim_artifact_type(db):
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
    checks = [dict(row) for row in validation.report["checks"]]
    checks[0]["artifact_type"] = ["not", "a", "string"]
    validation.report = {**validation.report, "checks": checks}
    validation.report_hash = hashlib.sha256(json.dumps(
        validation.report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert "SEMANTIC_CLAIM_ARTIFACT_REFERENCE_INVALID" in {
        blocker["code"] for blocker in result["blockers"]
    }


def test_package_review_blocks_claim_audit_report_hash_mismatch(db):
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
        "unassessed_claims": ["A newly inserted unassessed claim."],
    }
    # Intentionally preserve the original report_hash to simulate undetected tampering.
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    codes = {blocker["code"] for blocker in result["blockers"]}
    assert "SEMANTIC_CLAIM_REPORT_INTEGRITY_INVALID" in codes
    assert "SEMANTIC_CLAIM_COVERAGE_INCOMPLETE" in codes


def test_package_review_recomputes_artifact_content_hash_before_approval(db):
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
    item = db.scalar(
        select(ApplicationPackageItem).where(
            ApplicationPackageItem.package_id == package.id,
            ApplicationPackageItem.artifact_type == "cover_letter",
        )
    )
    item.content = {
        **item.content,
        "content": "Tampered content introduces a factual claim not covered by the audit.",
    }
    # Intentionally leave content_hash and the validation report unchanged.
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert "ARTIFACT_CONTENT_HASH_INVALID" in {
        blocker["code"] for blocker in result["blockers"]
    }


def test_package_claim_validation_rejects_tampered_artifact_before_ai_call(db, monkeypatch):
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
    item = db.scalar(
        select(ApplicationPackageItem).where(
            ApplicationPackageItem.package_id == package.id,
            ApplicationPackageItem.artifact_type == "cover_letter",
        )
    )
    item.content = {
        **item.content,
        "content": "Tampered text must not be sent for validation.",
    }
    db.flush()

    async def unexpected_ai_call(*args, **kwargs):
        pytest.fail("AI validation must not run for tampered artifact content")

    monkeypatch.setattr(
        "app.application.application_packages.claim_validation.execute_ai_task",
        unexpected_ai_call,
    )
    with pytest.raises(DomainError) as error:
        asyncio.run(
            PackageClaimValidationService(db).validate(user.id, app_record.id, package.id)
        )

    assert error.value.code == "ARTIFACT_CONTENT_HASH_INVALID"
    assert error.value.status_code == 409


@pytest.mark.parametrize("effective_field", ["effective_from", "effective_to"])
def test_package_claim_validation_rejects_ineffective_evidence_as_support(
    db, monkeypatch, effective_field
):
    from fastapi import HTTPException

    user, profile = candidate(db)
    app_record = application(db, profile)
    source = EvidenceSource(
        source_type="candidate_upload",
        source_reference=f"test://evidence/{uuid4()}",
        metadata_json={"test": True},
    )
    db.add(source)
    db.flush()
    validity = (
        {effective_field: datetime.now(timezone.utc) + timedelta(days=2)}
        if effective_field == "effective_from"
        else {effective_field: datetime.now(timezone.utc) - timedelta(days=2)}
    )
    evidence = Evidence(
        candidate_profile_id=profile.id,
        evidence_source_id=source.id,
        status="APPROVED",
        evidence_type="employment_record",
        content={"role": "Product Manager", "organization": "Example Ltd"},
        **validity,
    )
    db.add(evidence)
    db.flush()
    package = ApplicationPackageService(db).create(
        user.id, app_record.id, package_payload(evidence.id)
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
                    "claim_text": "Product Manager at Example Ltd.",
                    "status": "supported",
                    "evidence_refs": [str(evidence.id)],
                    "rationale": "The claim cites this evidence.",
                    "confidence": "high",
                }],
                "audited_artifact_types": [
                    "tailored_cv", "cover_letter", "application_q_and_a"
                ],
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
    assert error.value.detail["code"] == "AI_INVALID_EVIDENCE_REFERENCE"


@pytest.mark.parametrize(
    ("malformed_content", "expected_blocker"),
    [
        (["not", "an", "object"], "ARTIFACT_CONTENT_INVALID"),
        ({"answers": "not-a-list"}, "APPLICATION_Q_AND_A_CONTENT_INVALID"),
    ],
)
def test_package_review_fails_closed_on_malformed_q_and_a_content(
    db, malformed_content, expected_blocker
):
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
    q_and_a_item = db.scalar(
        select(ApplicationPackageItem).where(
            ApplicationPackageItem.package_id == package.id,
            ApplicationPackageItem.artifact_type == "application_q_and_a",
        )
    )
    q_and_a_item.content = malformed_content
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert expected_blocker in {blocker["code"] for blocker in result["blockers"]}


@pytest.mark.parametrize(
    ("target", "expected_blocker"),
    [
        ("artifact", "ARTIFACT_CONFIDENCE_INVALID"),
        ("answer", "ANSWER_CONFIDENCE_INVALID"),
    ],
)
def test_package_review_fails_closed_on_invalid_confidence_metadata(
    db, target, expected_blocker
):
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

    if target == "artifact":
        item = db.scalar(
            select(ApplicationPackageItem).where(
                ApplicationPackageItem.package_id == package.id,
                ApplicationPackageItem.artifact_type == "cover_letter",
            )
        )
        item.confidence = "certain"
    else:
        item = db.scalar(
            select(ApplicationPackageItem).where(
                ApplicationPackageItem.package_id == package.id,
                ApplicationPackageItem.artifact_type == "application_q_and_a",
            )
        )
        content = dict(item.content)
        answers = [dict(answer) for answer in content["answers"]]
        answers[0]["confidence"] = "certain"
        content["answers"] = answers
        item.content = content
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert expected_blocker in {blocker["code"] for blocker in result["blockers"]}


def test_package_review_rejects_claim_audit_execution_owned_by_another_candidate(db):
    user, profile = candidate(db)
    foreign_user, _ = candidate(db)
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
    foreign_execution = AIExecution(
        user_id=foreign_user.id,
        task="claim_evidence_validation",
        provider="google",
        model="gemini-test",
        status="SUCCEEDED",
    )
    db.add(foreign_execution)
    db.flush()
    validation.ai_execution_id = foreign_execution.id
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert "SEMANTIC_CLAIM_VALIDATION_INVALID" in {
        blocker["code"] for blocker in result["blockers"]
    }


@pytest.mark.parametrize(
    ("target", "expected_blocker"),
    [
        ("artifact", "ARTIFACT_UNSUPPORTED_CLAIMS_INVALID"),
        ("answer", "ANSWER_UNSUPPORTED_CLAIMS_INVALID"),
    ],
)
def test_package_review_fails_closed_on_malformed_unsupported_claim_metadata(
    db, target, expected_blocker
):
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

    if target == "artifact":
        item = db.scalar(
            select(ApplicationPackageItem).where(
                ApplicationPackageItem.package_id == package.id,
                ApplicationPackageItem.artifact_type == "cover_letter",
            )
        )
        item.unsupported_claims = 3
    else:
        item = db.scalar(
            select(ApplicationPackageItem).where(
                ApplicationPackageItem.package_id == package.id,
                ApplicationPackageItem.artifact_type == "application_q_and_a",
            )
        )
        content = dict(item.content)
        answers = [dict(answer) for answer in content["answers"]]
        answers[0]["unsupported_claims"] = 3
        content["answers"] = answers
        item.content = content
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert expected_blocker in {blocker["code"] for blocker in result["blockers"]}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("claim_text", None),
        ("rationale", "   "),
        ("confidence", "certain"),
        ("evidence_refs", "not-a-list"),
    ],
)
def test_package_review_rejects_malformed_persisted_claim_checks(db, field, value):
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
    checks = [dict(check) for check in validation.report["checks"]]
    if value is None:
        checks[0].pop(field)
    else:
        checks[0][field] = value
    validation.report = {**validation.report, "checks": checks}
    validation.report_hash = hashlib.sha256(json.dumps(
        validation.report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert "SEMANTIC_CLAIM_CHECK_INVALID" in {
        blocker["code"] for blocker in result["blockers"]
    }


@pytest.mark.parametrize("metadata_field", ["confidence", "unsupported_claims"])
def test_package_review_blocks_post_validation_artifact_metadata_changes(db, metadata_field):
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

    payload = package_payload(evidence.id)
    if metadata_field == "unsupported_claims":
        payload.items[1].unsupported_claims = ["Candidate confirmation required."]
    package = ApplicationPackageService(db).create(user.id, app_record.id, payload)
    attach_supported_validation_report(db, user, package, evidence.id)
    cover_letter_item = db.scalar(
        select(ApplicationPackageItem).where(
            ApplicationPackageItem.package_id == package.id,
            ApplicationPackageItem.artifact_type == "cover_letter",
        )
    )
    if metadata_field == "confidence":
        cover_letter_item.confidence = "high"
    else:
        cover_letter_item.unsupported_claims = []
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert "SEMANTIC_VALIDATION_ARTIFACT_CHANGED" in {
        blocker["code"] for blocker in result["blockers"]
    }


def test_package_review_blocks_tampered_source_snapshot(db):
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
    package.source_snapshot = {"tampered": True}
    db.flush()

    result = ApplicationPackageReviewService(db).prepare(
        user.id, app_record.id, package.id
    )

    assert result["ready"] is False
    assert "PACKAGE_SOURCE_SNAPSHOT_INTEGRITY_INVALID" in {
        blocker["code"] for blocker in result["blockers"]
    }


def test_package_claim_validation_rejects_tampered_source_snapshot_before_ai_call(
    db, monkeypatch
):
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
    package.source_snapshot = {"tampered": True}
    db.flush()

    async def unexpected_ai_call(*args, **kwargs):
        raise AssertionError("AI must not run for a tampered source snapshot")

    monkeypatch.setattr(
        "app.application.application_packages.claim_validation.execute_ai_task",
        unexpected_ai_call,
    )
    with pytest.raises(DomainError) as error:
        asyncio.run(
            PackageClaimValidationService(db).validate(user.id, app_record.id, package.id)
        )

    assert error.value.status_code == 409
    assert error.value.code == "PACKAGE_SOURCE_SNAPSHOT_INTEGRITY_INVALID"


@pytest.mark.parametrize("operation", ["get", "list"])
def test_package_reads_reject_tampered_source_snapshot(db, operation):
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
    package = service.create(user.id, app_record.id, package_payload(evidence.id))
    package.source_snapshot = {"tampered": True}
    db.flush()

    with pytest.raises(DomainError) as error:
        if operation == "get":
            service.get(user.id, app_record.id, package.id)
        else:
            service.list_for_application(user.id, app_record.id)

    assert error.value.status_code == 409
    assert error.value.code == "PACKAGE_SOURCE_SNAPSHOT_INTEGRITY_INVALID"


def test_package_reprepare_invalidates_stale_ready_status_when_evidence_becomes_unapproved(db):
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
    assert prepared["package"].status == "READY_FOR_REVIEW"

    evidence.status = "DRAFT"
    db.flush()
    rechecked = review.prepare(user.id, app_record.id, package.id)

    assert rechecked["ready"] is False
    assert rechecked["package"].status == "DRAFT"
    assert "EVIDENCE_NOT_APPROVED" in {
        blocker["code"] for blocker in rechecked["blockers"]
    }


def test_package_decision_invalidates_ready_status_when_evidence_is_no_longer_approved(db):
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

    evidence.status = "DRAFT"
    db.flush()
    with pytest.raises(DomainError) as error:
        review.decide(user.id, app_record.id, package.id, "approve")

    assert error.value.code == "APPLICATION_PACKAGE_NOT_READY"
    assert package.status == "DRAFT"
    assert "EVIDENCE_NOT_APPROVED" in {
        blocker["code"] for blocker in error.value.details["blockers"]
    }


@pytest.mark.parametrize("target_status", ["READY_FOR_REVIEW", "APPROVED", "NEEDS_REVISION"])
def test_package_claim_validation_rejects_non_draft_versions(db, monkeypatch, target_status):
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

    if target_status == "APPROVED":
        review.decide(user.id, app_record.id, package.id, "approve")
    elif target_status == "NEEDS_REVISION":
        review.decide(user.id, app_record.id, package.id, "request_revision")
    assert package.status == target_status

    async def unexpected_ai_call(*args, **kwargs):
        raise AssertionError("AI must not revalidate a package outside DRAFT state")

    monkeypatch.setattr(
        "app.application.application_packages.claim_validation.execute_ai_task",
        unexpected_ai_call,
    )
    with pytest.raises(DomainError) as error:
        asyncio.run(
            PackageClaimValidationService(db).validate(user.id, app_record.id, package.id)
        )

    assert error.value.status_code == 409
    assert error.value.code == "APPLICATION_PACKAGE_STATE_CONFLICT"
    assert package.status == target_status


@pytest.mark.parametrize("invalid_status", ["APPROVED", "NEEDS_REVISION", "CORRUPT_STATE"])
def test_package_prepare_rejects_non_preparable_states(db, invalid_status):
    user, profile = candidate(db)
    app_record = application(db, profile)
    package = ApplicationPackageService(db).create(
        user.id,
        app_record.id,
        ApplicationPackageCreate.model_validate({
            "items": [{
                "artifact_type": "cover_letter",
                "content": {"content": "Draft cover letter."},
            }]
        }),
    )
    package.status = invalid_status
    db.flush()

    with pytest.raises(DomainError) as error:
        ApplicationPackageReviewService(db).prepare(user.id, app_record.id, package.id)

    assert error.value.status_code == 409
    assert error.value.code == "APPLICATION_PACKAGE_STATE_CONFLICT"
    assert package.status == invalid_status


def test_invalid_package_decision_does_not_mutate_stale_ready_state(db):
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
    assert review.prepare(user.id, app_record.id, package.id)["ready"] is True

    evidence.status = "DRAFT"
    db.flush()
    with pytest.raises(DomainError) as error:
        review.decide(user.id, app_record.id, package.id, "invalid")

    assert error.value.status_code == 422
    assert error.value.code == "INVALID_REVIEW_DECISION"
    assert package.status == "READY_FOR_REVIEW"
