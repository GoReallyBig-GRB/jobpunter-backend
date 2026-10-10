"""Regression tests for application-package readiness and stale validation."""
import hashlib
import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.application_packages.review import ApplicationPackageReviewService
from app.application.application_packages.service import (
    ApplicationPackageService,
    artifact_metadata_hash,
    hash_source_snapshot,
)
from app.application.application_packages.schemas import ApplicationPackageCreate
from app.infrastructure.db.models import (
    AIExecution,
    Application,
    ApplicationPackage,
    ApplicationPackageClaimValidation,
    ApplicationPackageItem,
    CandidateProfile,
    Evidence,
    EvidenceSource,
    Job,
    User,
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


def _candidate(db):
    user = User(display_name=f"Candidate {uuid4()}")
    db.add(user)
    db.flush()
    profile = CandidateProfile(user_id=user.id, status="ACTIVE")
    db.add(profile)
    db.flush()
    return user, profile


def _application(db, profile):
    job = Job(canonical_title="Technical Product Manager", company_name="Example Co")
    db.add(job)
    db.flush()
    record = Application(
        candidate_profile_id=profile.id,
        job_id=job.id,
        status="PLANNED",
    )
    db.add(record)
    db.flush()
    return record


def _ready_package(db):
    user, profile = _candidate(db)
    app_record = _application(db, profile)
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

    evidence_ref = str(evidence.id)
    payload = ApplicationPackageCreate.model_validate({
        "items": [
            {
                "artifact_type": "tailored_cv",
                "content": {"content": "Tailored CV v1"},
                "evidence_refs": [evidence_ref],
                "confidence": "medium",
                "unsupported_claims": [],
            },
            {
                "artifact_type": "cover_letter",
                "content": {"content": "First cover letter"},
                "evidence_refs": [evidence_ref],
                "confidence": "medium",
                "unsupported_claims": [],
            },
            {
                "artifact_type": "application_q_and_a",
                "content": {"answers": [{
                    "question": "Why this role?",
                    "answer": "An editable, evidence-based draft.",
                    "evidence_basis": [evidence_ref],
                    "confidence": "medium",
                    "unsupported_claims": [],
                    "missing_information": [],
                }]},
                "evidence_refs": [evidence_ref],
                "confidence": "medium",
                "unsupported_claims": [],
            },
        ]
    })
    package = ApplicationPackageService(db).create(user.id, app_record.id, payload)
    items = list(db.scalars(
        select(ApplicationPackageItem).where(
            ApplicationPackageItem.package_id == package.id
        ).order_by(ApplicationPackageItem.artifact_type)
    ).all())

    execution = AIExecution(
        user_id=user.id,
        task="claim_evidence_validation",
        provider="google",
        model="gemini-test",
        status="SUCCEEDED",
    )
    db.add(execution)
    db.flush()
    evidence_hash = hashlib.sha256(json.dumps(
        evidence.content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    report = {
        "evidence_snapshot": [{
            "id": evidence_ref,
            "revision": evidence.revision,
            "content_hash": evidence_hash,
        }],
        "artifact_snapshot": [{
            "id": str(item.id),
            "artifact_type": item.artifact_type,
            "content_hash": item.content_hash,
            "metadata_hash": artifact_metadata_hash(item),
        } for item in items],
        "audited_artifact_types": [
            "tailored_cv", "cover_letter", "application_q_and_a"
        ],
        "checks": [{
            "artifact_type": artifact_type,
            "claim_text": "Documented candidate fact",
            "status": "supported",
            "evidence_refs": [evidence_ref],
            "rationale": "Supported by approved evidence.",
            "confidence": "high",
        } for artifact_type in (
            "tailored_cv", "cover_letter", "application_q_and_a"
        )],
        "coverage_complete": True,
        "unassessed_claims": [],
        "candidate_review_required": False,
    }
    report_hash = hashlib.sha256(json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    validation = ApplicationPackageClaimValidation(
        package_id=package.id,
        ai_execution_id=execution.id,
        report=report,
        report_hash=report_hash,
    )
    db.add(validation)
    db.flush()
    return user, profile, app_record, evidence, package, items, validation


def _rehash_report(validation, report):
    validation.report = report
    validation.report_hash = hashlib.sha256(json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()


def test_changed_artifact_content_blocks_package_readiness(db):
    user, _, app_record, _, package, items, _ = _ready_package(db)
    cv = next(item for item in items if item.artifact_type == "tailored_cv")
    cv.content = {"content": "Changed after semantic validation"}

    result = ApplicationPackageReviewService(db).prepare(user.id, app_record.id, package.id)

    assert result["ready"] is False
    assert "ARTIFACT_CONTENT_HASH_INVALID" in {b["code"] for b in result["blockers"]}
    assert package.status == "DRAFT"


def test_changed_artifact_evidence_links_invalidate_prior_audit(db):
    user, _, app_record, _, package, items, _ = _ready_package(db)
    cover_letter = next(item for item in items if item.artifact_type == "cover_letter")
    cover_letter.evidence_refs = []

    result = ApplicationPackageReviewService(db).prepare(user.id, app_record.id, package.id)

    codes = {b["code"] for b in result["blockers"]}
    assert result["ready"] is False
    assert "EVIDENCE_REQUIRED" in codes
    assert "SEMANTIC_VALIDATION_ARTIFACT_CHANGED" in codes


def test_changed_evidence_revision_and_content_invalidate_prior_audit(db):
    user, _, app_record, evidence, package, _, _ = _ready_package(db)
    evidence.content = {"role": "Different role after validation"}
    evidence.revision += 1

    result = ApplicationPackageReviewService(db).prepare(user.id, app_record.id, package.id)

    assert result["ready"] is False
    assert "SEMANTIC_VALIDATION_EVIDENCE_CHANGED" in {
        b["code"] for b in result["blockers"]
    }


def test_evidence_revoked_after_validation_blocks_readiness(db):
    user, _, app_record, evidence, package, _, _ = _ready_package(db)
    evidence.status = "DRAFT"

    result = ApplicationPackageReviewService(db).prepare(user.id, app_record.id, package.id)

    codes = {b["code"] for b in result["blockers"]}
    assert result["ready"] is False
    assert "EVIDENCE_NOT_APPROVED" in codes


@pytest.mark.parametrize(
    ("field", "malformed_value", "expected_code"),
    [
        ("coverage_complete", "true", "SEMANTIC_CLAIM_COVERAGE_INCOMPLETE"),
        ("candidate_review_required", "false", "SEMANTIC_CLAIM_REVIEW_REQUIRED"),
    ],
)
def test_malformed_review_flags_fail_closed(db, field, malformed_value, expected_code):
    user, _, app_record, _, package, _, validation = _ready_package(db)
    report = dict(validation.report)
    report[field] = malformed_value
    _rehash_report(validation, report)

    result = ApplicationPackageReviewService(db).prepare(user.id, app_record.id, package.id)

    assert result["ready"] is False
    assert expected_code in {b["code"] for b in result["blockers"]}


def test_approval_rechecks_evidence_after_package_was_marked_ready(db):
    user, _, app_record, evidence, package, _, _ = _ready_package(db)
    review = ApplicationPackageReviewService(db)
    prepared = review.prepare(user.id, app_record.id, package.id)
    assert prepared["ready"] is True

    evidence.status = "DRAFT"
    db.flush()
    with pytest.raises(DomainError) as error:
        review.decide(user.id, app_record.id, package.id, "approve")

    assert error.value.code == "APPLICATION_PACKAGE_NOT_READY"
    assert package.status == "DRAFT"


def test_tampered_source_snapshot_blocks_package_readiness(db):
    user, _, app_record, _, package, _, _ = _ready_package(db)
    package.source_snapshot = {**package.source_snapshot, "tampered": True}

    result = ApplicationPackageReviewService(db).prepare(user.id, app_record.id, package.id)

    assert result["ready"] is False
    assert "PACKAGE_SOURCE_SNAPSHOT_INTEGRITY_INVALID" in {
        b["code"] for b in result["blockers"]
    }
    assert package.source_snapshot_hash != hash_source_snapshot(package.source_snapshot)
