"""Regression tests for application-package readiness and stale validation."""
import hashlib
import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import DBAPIError
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


@pytest.fixture
def committed_ready_package():
    """Persist a package so independent PostgreSQL sessions can contend for its rows."""
    engine = create_engine(os.environ["DATABASE_URL"])
    setup = Session(engine)
    try:
        user, profile, app_record, evidence, package, items, validation = _ready_package(setup)
        setup.commit()
        ids = {
            "user_id": user.id,
            "profile_id": profile.id,
            "application_id": app_record.id,
            "job_id": app_record.job_id,
            "evidence_id": evidence.id,
            "evidence_source_id": evidence.evidence_source_id,
            "package_id": package.id,
            "item_ids": [item.id for item in items],
            "validation_id": validation.id,
            "execution_id": validation.ai_execution_id,
        }
    except Exception:
        setup.rollback()
        setup.close()
        engine.dispose()
        raise
    setup.close()
    try:
        yield engine, ids
    finally:
        cleanup = Session(engine)
        try:
            package_ids = list(cleanup.scalars(select(ApplicationPackage.id).where(
                ApplicationPackage.application_id == ids["application_id"]
            )).all())
            if package_ids:
                cleanup.execute(delete(ApplicationPackageClaimValidation).where(
                    ApplicationPackageClaimValidation.package_id.in_(package_ids)
                ))
                cleanup.execute(delete(ApplicationPackageItem).where(
                    ApplicationPackageItem.package_id.in_(package_ids)
                ))
            cleanup.execute(delete(ApplicationPackage).where(
                ApplicationPackage.application_id == ids["application_id"]
            ))
            cleanup.execute(delete(Application).where(Application.id == ids["application_id"]))
            cleanup.execute(delete(Evidence).where(Evidence.id == ids["evidence_id"]))
            cleanup.execute(delete(EvidenceSource).where(
                EvidenceSource.id == ids["evidence_source_id"]
            ))
            cleanup.execute(delete(AIExecution).where(AIExecution.id == ids["execution_id"]))
            cleanup.execute(delete(CandidateProfile).where(
                CandidateProfile.id == ids["profile_id"]
            ))
            cleanup.execute(delete(Job).where(Job.id == ids["job_id"]))
            cleanup.execute(delete(User).where(User.id == ids["user_id"]))
            cleanup.commit()
        finally:
            cleanup.close()
            engine.dispose()


def _assert_row_locked_by_other_transaction(engine, statement):
    contender = Session(engine)
    try:
        with pytest.raises(DBAPIError):
            contender.execute(statement.with_for_update(nowait=True)).all()
        contender.rollback()
    finally:
        contender.close()


def test_review_holds_package_lock_until_readiness_transaction_finishes(
    committed_ready_package,
):
    engine, ids = committed_ready_package
    reviewer = Session(engine)
    try:
        review = ApplicationPackageReviewService(reviewer)
        review._owned_package(ids["user_id"], ids["application_id"], ids["package_id"])
        _assert_row_locked_by_other_transaction(
            engine,
            select(ApplicationPackage).where(
                ApplicationPackage.id == ids["package_id"]
            ),
        )
    finally:
        reviewer.rollback()
        reviewer.close()

    contender = Session(engine)
    try:
        contender.execute(select(ApplicationPackage).where(
            ApplicationPackage.id == ids["package_id"]
        ).with_for_update(nowait=True)).all()
    finally:
        contender.rollback()
        contender.close()


def test_review_holds_artifact_locks_until_readiness_transaction_finishes(
    committed_ready_package,
):
    engine, ids = committed_ready_package
    reviewer = Session(engine)
    try:
        ApplicationPackageReviewService(reviewer)._owned_package(
            ids["user_id"], ids["application_id"], ids["package_id"]
        )
        _assert_row_locked_by_other_transaction(
            engine,
            select(ApplicationPackageItem).where(
                ApplicationPackageItem.id == ids["item_ids"][0]
            ),
        )
    finally:
        reviewer.rollback()
        reviewer.close()


def test_review_holds_evidence_locks_until_readiness_transaction_finishes(
    committed_ready_package,
):
    engine, ids = committed_ready_package
    reviewer = Session(engine)
    try:
        service = ApplicationPackageReviewService(reviewer)
        profile, package, items = service._owned_package(
            ids["user_id"], ids["application_id"], ids["package_id"]
        )
        assert service.readiness(profile.id, package.id, items, ids["user_id"]) == []
        _assert_row_locked_by_other_transaction(
            engine,
            select(Evidence).where(Evidence.id == ids["evidence_id"]),
        )
    finally:
        reviewer.rollback()
        reviewer.close()

    contender = Session(engine)
    try:
        evidence = contender.scalar(select(Evidence).where(
            Evidence.id == ids["evidence_id"]
        ).with_for_update(nowait=True))
        assert evidence is not None
    finally:
        contender.rollback()
        contender.close()


def test_evidence_update_cannot_race_through_readiness_lock(committed_ready_package):
    from sqlalchemy import update, text

    engine, ids = committed_ready_package
    reviewer = Session(engine)
    try:
        service = ApplicationPackageReviewService(reviewer)
        profile, package, items = service._owned_package(
            ids["user_id"], ids["application_id"], ids["package_id"]
        )
        assert service.readiness(profile.id, package.id, items, ids["user_id"]) == []

        writer = Session(engine)
        try:
            writer.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError):
                writer.execute(
                    update(Evidence)
                    .where(Evidence.id == ids["evidence_id"])
                    .values(status="DRAFT")
                )
            writer.rollback()
        finally:
            writer.close()
    finally:
        reviewer.rollback()
        reviewer.close()

    writer = Session(engine)
    try:
        writer.execute(
            update(Evidence)
            .where(Evidence.id == ids["evidence_id"])
            .values(status="DRAFT")
        )
        writer.commit()
    finally:
        writer.close()

    check = Session(engine)
    try:
        evidence = check.get(Evidence, ids["evidence_id"])
        assert evidence.status == "DRAFT"
    finally:
        check.close()


def test_artifact_update_cannot_race_through_readiness_lock(committed_ready_package):
    from sqlalchemy import text, update

    engine, ids = committed_ready_package
    artifact_id = ids["item_ids"][0]
    reviewer = Session(engine)
    try:
        ApplicationPackageReviewService(reviewer)._owned_package(
            ids["user_id"], ids["application_id"], ids["package_id"]
        )
        writer = Session(engine)
        try:
            writer.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError):
                writer.execute(
                    update(ApplicationPackageItem)
                    .where(ApplicationPackageItem.id == artifact_id)
                    .values(content={"content": "concurrent edit"})
                )
            writer.rollback()
        finally:
            writer.close()
    finally:
        reviewer.rollback()
        reviewer.close()

    writer = Session(engine)
    try:
        writer.execute(
            update(ApplicationPackageItem)
            .where(ApplicationPackageItem.id == artifact_id)
            .values(content={"content": "concurrent edit"})
        )
        writer.commit()
    finally:
        writer.close()

    check = Session(engine)
    try:
        artifact = check.get(ApplicationPackageItem, artifact_id)
        assert artifact.content == {"content": "concurrent edit"}
    finally:
        check.close()


def test_prepare_rechecks_evidence_changed_by_an_independent_committed_session(
    committed_ready_package,
):
    from sqlalchemy import update

    engine, ids = committed_ready_package
    writer = Session(engine)
    try:
        writer.execute(
            update(Evidence)
            .where(Evidence.id == ids["evidence_id"])
            .values(status="DRAFT")
        )
        writer.commit()
    finally:
        writer.close()

    reviewer = Session(engine)
    try:
        result = ApplicationPackageReviewService(reviewer).prepare(
            ids["user_id"], ids["application_id"], ids["package_id"]
        )
        assert result["ready"] is False
        assert "EVIDENCE_NOT_APPROVED" in {
            blocker["code"] for blocker in result["blockers"]
        }
        assert result["package"].status == "DRAFT"
    finally:
        reviewer.rollback()
        reviewer.close()


def test_approval_rechecks_evidence_changed_by_an_independent_committed_session(
    committed_ready_package,
):
    from sqlalchemy import update

    engine, ids = committed_ready_package
    reviewer = Session(engine)
    try:
        prepared = ApplicationPackageReviewService(reviewer).prepare(
            ids["user_id"], ids["application_id"], ids["package_id"]
        )
        assert prepared["ready"] is True
    finally:
        reviewer.close()

    writer = Session(engine)
    try:
        writer.execute(
            update(Evidence)
            .where(Evidence.id == ids["evidence_id"])
            .values(status="DRAFT")
        )
        writer.commit()
    finally:
        writer.close()

    decision_session = Session(engine)
    try:
        with pytest.raises(DomainError) as error:
            ApplicationPackageReviewService(decision_session).decide(
                ids["user_id"], ids["application_id"], ids["package_id"], "approve"
            )
        assert error.value.code == "APPLICATION_PACKAGE_NOT_READY"
        decision_session.rollback()
    finally:
        decision_session.close()

    check = Session(engine)
    try:
        package = check.get(ApplicationPackage, ids["package_id"])
        assert package.status == "DRAFT"
    finally:
        check.close()


def test_competing_review_decision_must_wait_for_package_review_lock(
    committed_ready_package,
):
    from sqlalchemy import text

    engine, ids = committed_ready_package
    reviewer = Session(engine)
    try:
        ApplicationPackageReviewService(reviewer)._owned_package(
            ids["user_id"], ids["application_id"], ids["package_id"]
        )
        contender = Session(engine)
        try:
            contender.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError):
                contender.execute(
                    select(ApplicationPackage)
                    .where(ApplicationPackage.id == ids["package_id"])
                    .with_for_update()
                ).scalar_one()
            contender.rollback()
        finally:
            contender.close()
    finally:
        reviewer.rollback()
        reviewer.close()

    contender = Session(engine)
    try:
        package = contender.scalar(
            select(ApplicationPackage)
            .where(ApplicationPackage.id == ids["package_id"])
            .with_for_update(nowait=True)
        )
        assert package is not None
    finally:
        contender.rollback()
        contender.close()


def _creation_payload(evidence_ids):
    refs = [str(value) for value in evidence_ids]
    return ApplicationPackageCreate.model_validate({
        "items": [
            {
                "artifact_type": "tailored_cv",
                "content": {"content": "Tailored CV for the next package version"},
                "evidence_refs": refs,
                "confidence": "medium",
                "unsupported_claims": [],
            },
            {
                "artifact_type": "cover_letter",
                "content": {"content": "Cover letter for the next package version"},
                "evidence_refs": refs,
                "confidence": "medium",
                "unsupported_claims": [],
            },
            {
                "artifact_type": "application_q_and_a",
                "content": {"answers": [{
                    "question": "Why this role?",
                    "answer": "A draft grounded in candidate evidence.",
                    "evidence_basis": refs,
                    "confidence": "medium",
                    "unsupported_claims": [],
                    "missing_information": [],
                }]},
                "evidence_refs": refs,
                "confidence": "medium",
                "unsupported_claims": [],
            },
        ]
    })


def test_package_creation_locks_evidence_until_snapshot_commit(committed_ready_package):
    from sqlalchemy import text, update

    engine, ids = committed_ready_package
    creator = Session(engine)
    lock_probe = {"blocked": False}
    service = ApplicationPackageService(creator)
    original_snapshot = service._source_snapshot

    def probe_snapshot(profile, application, job, evidence_ids):
        writer = Session(engine)
        try:
            writer.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError):
                writer.execute(
                    update(Evidence)
                    .where(Evidence.id == ids["evidence_id"])
                    .values(status="DRAFT")
                )
            writer.rollback()
            lock_probe["blocked"] = True
        finally:
            writer.close()
        return original_snapshot(profile, application, job, evidence_ids)

    service._source_snapshot = probe_snapshot
    try:
        package = service.create(
            ids["user_id"], ids["application_id"],
            _creation_payload([ids["evidence_id"]]),
        )
        assert package.version_number == 2
        assert lock_probe["blocked"] is True
        assert package.source_snapshot_hash == hash_source_snapshot(package.source_snapshot)
    finally:
        creator.close()


def test_package_creation_holds_application_lock_during_version_allocation(
    committed_ready_package,
):
    from sqlalchemy import text, update

    engine, ids = committed_ready_package
    creator = Session(engine)
    lock_probe = {"blocked": False}
    service = ApplicationPackageService(creator)
    original_snapshot = service._source_snapshot

    def probe_snapshot(profile, application, job, evidence_ids):
        writer = Session(engine)
        try:
            writer.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError):
                writer.execute(
                    update(Application)
                    .where(Application.id == ids["application_id"])
                    .values(status="IN_PROGRESS")
                )
            writer.rollback()
            lock_probe["blocked"] = True
        finally:
            writer.close()
        return original_snapshot(profile, application, job, evidence_ids)

    service._source_snapshot = probe_snapshot
    try:
        package = service.create(
            ids["user_id"], ids["application_id"],
            _creation_payload([ids["evidence_id"]]),
        )
        assert package.version_number == 2
        assert lock_probe["blocked"] is True
    finally:
        creator.close()


def test_new_package_version_captures_latest_committed_evidence_snapshot(
    committed_ready_package,
):
    from sqlalchemy import update

    engine, ids = committed_ready_package
    writer = Session(engine)
    try:
        writer.execute(
            update(Evidence)
            .where(Evidence.id == ids["evidence_id"])
            .values(
                content={"role": "Senior Product Manager", "organization": "Updated Co"},
                revision=2,
            )
        )
        writer.commit()
    finally:
        writer.close()

    creator = Session(engine)
    try:
        package = ApplicationPackageService(creator).create(
            ids["user_id"], ids["application_id"],
            _creation_payload([ids["evidence_id"]]),
        )
        assert package.version_number == 2
        evidence_snapshot = package.source_snapshot["evidence"]
        assert len(evidence_snapshot) == 1
        assert evidence_snapshot[0]["revision"] == 2
        assert evidence_snapshot[0]["content"] == {
            "role": "Senior Product Manager", "organization": "Updated Co"
        }
        assert package.source_snapshot_hash == hash_source_snapshot(package.source_snapshot)
    finally:
        creator.close()


def test_existing_package_snapshot_remains_immutable_after_evidence_changes(
    committed_ready_package,
):
    from sqlalchemy import update

    engine, ids = committed_ready_package
    before = Session(engine)
    try:
        original_package = before.get(ApplicationPackage, ids["package_id"])
        original_snapshot = original_package.source_snapshot
        original_hash = original_package.source_snapshot_hash
        original_snapshot_copy = json.loads(json.dumps(original_snapshot))
    finally:
        before.close()

    writer = Session(engine)
    try:
        writer.execute(
            update(Evidence)
            .where(Evidence.id == ids["evidence_id"])
            .values(
                content={"role": "Changed after package creation"},
                revision=2,
            )
        )
        writer.commit()
    finally:
        writer.close()

    check = Session(engine)
    try:
        package = check.get(ApplicationPackage, ids["package_id"])
        assert package.source_snapshot == original_snapshot_copy
        assert package.source_snapshot_hash == original_hash
        assert package.source_snapshot_hash == hash_source_snapshot(package.source_snapshot)
    finally:
        check.close()


def test_package_creation_rejects_evidence_owned_by_another_candidate(
    committed_ready_package,
):
    engine, ids = committed_ready_package
    outsider = Session(engine)
    try:
        user, profile = _candidate(outsider)
        outsider_source = EvidenceSource(
            source_type="candidate_upload",
            source_reference=f"test://outsider-evidence/{uuid4()}",
            metadata_json={"test": True},
        )
        outsider.add(outsider_source)
        outsider.flush()
        outsider_evidence = Evidence(
            candidate_profile_id=profile.id,
            evidence_source_id=outsider_source.id,
            status="APPROVED",
            evidence_type="employment_record",
            content={"role": "Not the package owner"},
        )
        outsider.add(outsider_evidence)
        outsider.commit()
        outsider_user_id = user.id
        outsider_profile_id = profile.id
        outsider_evidence_id = outsider_evidence.id
        outsider_source_id = outsider_source.id
    finally:
        outsider.close()

    creator = Session(engine)
    try:
        with pytest.raises(DomainError) as error:
            ApplicationPackageService(creator).create(
                ids["user_id"], ids["application_id"],
                _creation_payload([outsider_evidence_id]),
            )
        assert error.value.code == "EVIDENCE_NOT_FOUND"
        creator.rollback()
    finally:
        creator.close()

    cleanup = Session(engine)
    try:
        cleanup.execute(delete(Evidence).where(Evidence.id == outsider_evidence_id))
        cleanup.execute(delete(EvidenceSource).where(EvidenceSource.id == outsider_source_id))
        cleanup.execute(delete(CandidateProfile).where(CandidateProfile.id == outsider_profile_id))
        cleanup.execute(delete(User).where(User.id == outsider_user_id))
        cleanup.commit()
    finally:
        cleanup.close()


def test_concurrent_package_creations_allocate_distinct_sequential_versions(
    committed_ready_package,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    engine, ids = committed_ready_package
    start_together = Barrier(2)

    def create_version():
        session = Session(engine)
        try:
            start_together.wait(timeout=5)
            package = ApplicationPackageService(session).create(
                ids["user_id"], ids["application_id"],
                _creation_payload([ids["evidence_id"]]),
            )
            return package.version_number
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        versions = sorted(pool.map(lambda _: create_version(), range(2)))

    assert versions == [2, 3]


def test_package_snapshot_orders_multiple_evidence_rows_deterministically(
    committed_ready_package,
):
    engine, ids = committed_ready_package
    setup = Session(engine)
    try:
        source = EvidenceSource(
            source_type="candidate_upload",
            source_reference=f"test://ordered-evidence/{uuid4()}",
            metadata_json={"test": True},
        )
        setup.add(source)
        setup.flush()
        second_evidence = Evidence(
            candidate_profile_id=ids["profile_id"],
            evidence_source_id=source.id,
            status="APPROVED",
            evidence_type="employment_record",
            content={"role": "Engineering Manager", "organization": "Second Co"},
        )
        setup.add(second_evidence)
        setup.commit()
        second_evidence_id = second_evidence.id
        source_id = source.id
    finally:
        setup.close()

    creator = Session(engine)
    try:
        package = ApplicationPackageService(creator).create(
            ids["user_id"], ids["application_id"],
            _creation_payload([second_evidence_id, ids["evidence_id"]]),
        )
        snapshot_ids = [
            row["id"] for row in package.source_snapshot["evidence"]
        ]
        assert snapshot_ids == sorted([
            str(second_evidence_id), str(ids["evidence_id"])
        ])
        assert package.source_snapshot_hash == hash_source_snapshot(package.source_snapshot)
    finally:
        creator.close()
        cleanup = Session(engine)
        try:
            cleanup.execute(delete(Evidence).where(Evidence.id == second_evidence_id))
            cleanup.execute(delete(EvidenceSource).where(EvidenceSource.id == source_id))
            cleanup.commit()
        finally:
            cleanup.close()
