import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_principal
from app.api.errors import DomainError
from app.infrastructure.db.session import get_db
from app.main import app
from app.ports.identity import AuthenticatedPrincipal
from app.application.opportunities.service import OpportunityService
from app.application.opportunities.evaluation_policy import POLICY_VERSION
from app.application.jobs.job_verification import current_application_route_verification
from app.application.jobs.requirement_snapshot import (
    snapshot_job_requirements,
    snapshot_requirement_extraction_trace,
)
from app.application.jobs.requirement_review import review_requirement
from app.domain.opportunities.service import transition_application
from app.infrastructure.db.models import (
    Application,
    ApplicationEvent,
    AuditEvent,
    AcquisitionRun,
    CandidateEvaluation,
    CandidatePreferences,
    CandidateProfile,
    GovernanceConfigVersion,
    Job,
    Punt,
    PuntEvaluation,
    PuntQualificationHistory,
    OutboxEvent,
    AIExecution,
    JobDescriptionVersion,
    JobDiscoveryEvent,
    JobProvenance,
    JobRequirement,
    JobVerificationAttempt,
    RawSourceRecord,
    Source,
    SourceConnector,
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


def candidate(db):
    user = User(display_name=f"Candidate {uuid4()}")
    db.add(user)
    db.flush()
    profile = CandidateProfile(user_id=user.id, status="ACTIVE")
    db.add(profile)
    db.flush()
    return user, profile


def job(db, title, *, include_public_provenance=True):
    record = Job(
        canonical_title=title,
        company_name="Example Employer",
        lifecycle_status="ACTIVE",
        freshness_status="FRESH",
        application_url="https://example.test/apply",
        metadata_json={},
        location_restrictions={},
    )
    db.add(record)
    db.flush()
    now = datetime.now(timezone.utc)
    if include_public_provenance:
        source = Source(
            name=f"Opportunity fixture source {uuid4()}",
            source_type="GREENHOUSE_JOB_BOARD",
            status="ACTIVE",
            permission_review_status="APPROVED",
            permission_review_reference="test-policy://opportunity-fixture",
            permission_reviewed_at=now,
            operational_state="AVAILABLE",
        )
        db.add(source)
        db.flush()
        db.add(JobProvenance(
            job_id=record.id,
            source_id=source.id,
            raw_source_record_id=None,
            submitted_by_user_id=None,
            source_url=record.application_url,
            normalized_source_url=record.application_url,
            observation_type="ACQUIRED",
            asserted_by="SOURCE",
            observed_at=now,
            confidence=1.0,
            status="OBSERVED",
            metadata_json={},
        ))
        db.flush()
    record.last_verified_at = now
    db.add(JobVerificationAttempt(
        job_id=record.id,
        checked_url=record.application_url,
        final_url=record.application_url,
        status="ACTIVE",
        http_status_code=200,
        reason_code="JOB_PAGE_AND_APPLICATION_SIGNAL_PRESENT",
        evidence={"apply_marker_detected": True},
        verifier_version="test-verifier-v1",
        checked_at=now,
        created_at=now,
    ))
    db.add(JobDescriptionVersion(
        job_id=record.id,
        version_number=1,
        content=(
            "Responsibilities include managing product delivery and coordinating teams. "
            "Requirements include relevant experience, analytical skills, and stakeholder management. "
        ) * 4,
        content_hash=f"opportunity-test-{uuid4()}",
        completeness_status="COMPLETE",
        retrieved_at=datetime.now(timezone.utc),
        requirement_extraction_status="COMPLETE",
    ))
    db.flush()
    return record


def evaluation(db, profile, job_record, *, outcome="APPLY", validity="CURRENT", blockers=None, version=1):
    jd = db.scalar(
        select(JobDescriptionVersion)
        .where(JobDescriptionVersion.job_id == job_record.id)
        .order_by(JobDescriptionVersion.version_number.desc())
        .limit(1)
    )
    rule_config = db.scalar(
        select(GovernanceConfigVersion).where(
            GovernanceConfigVersion.config_key == "decision.rules.candidate_evaluation",
            GovernanceConfigVersion.status == "PUBLISHED",
        )
    )
    rule_config_id = str(rule_config.id) if rule_config else None
    rule_config_version = rule_config.version if rule_config else None
    rule_config_fingerprint = rule_config.payload_fingerprint if rule_config else "builtin"
    current_policy_version = (
        f"{POLICY_VERSION}+rules-v{rule_config.version}"
        if rule_config else POLICY_VERSION
    )
    preferences_row = db.scalar(
        select(CandidatePreferences).where(
            CandidatePreferences.candidate_profile_id == profile.id,
        )
    )
    preferences = preferences_row.preferences if preferences_row else {}
    preferences_revision = preferences_row.revision if preferences_row else 0

    record = CandidateEvaluation(
        candidate_profile_id=profile.id,
        job_id=job_record.id,
        target_role_key="technical-product-manager",
        evaluation_version=version,
        validity_status=validity,
        outcome=outcome,
        score=85,
        hard_blockers=blockers or [],
        evidence_summary={"dimensions": {
            "deterministic_eligibility": "ELIGIBLE",
            "mandatory_requirements": "MET",
            "application_accessibility": "VERIFIED_ACTIVE",
            "job_freshness": job_record.freshness_status,
        }},
        explanation={},
        policy_version=current_policy_version,
        candidate_profile_revision=profile.revision,
        preferences_revision=preferences_revision,
        job_description_version_id=jd.id if jd else None,
        input_snapshot={
            "candidate_profile_revision": profile.revision,
            "candidate_profile_data": profile.profile_data or {},
            "approved_evidence": [],
            "approved_claims": [],
            "excluded_claims": [],
            "preferences_revision": preferences_revision,
            "preferences": preferences,
            "job_id": str(job_record.id),
            "job_revision": job_record.revision,
            "job_snapshot": {
                "id": str(job_record.id),
                "canonical_title": job_record.canonical_title,
                "company_name": job_record.company_name,
                "employment_type": job_record.employment_type,
                "lifecycle_status": job_record.lifecycle_status,
                "salary_min": float(job_record.salary_min) if job_record.salary_min is not None else None,
                "salary_max": float(job_record.salary_max) if job_record.salary_max is not None else None,
                "salary_currency": job_record.salary_currency,
                "salary_period": job_record.salary_period,
                "location_country": job_record.location_country,
                "work_mode": job_record.work_mode,
                "location_restrictions": job_record.location_restrictions,
                "metadata": job_record.metadata_json or {},
                "freshness_status": job_record.freshness_status,
                "application_url": job_record.application_url,
                "application_route_verification": current_application_route_verification(
                    db, job_record, now=datetime.now(timezone.utc),
                ),
            },
            "job_description_version_id": str(jd.id) if jd else None,
            "job_description_version_number": jd.version_number if jd else None,
            "job_description_content_hash": jd.content_hash if jd else None,
            "job_description_completeness_status": jd.completeness_status if jd else "MISSING",
            "job_description_requirement_extraction_status": jd.requirement_extraction_status if jd else "MISSING",
            "job_description_requirement_extraction_version": jd.requirement_extraction_version if jd else None,
            "job_requirements": snapshot_job_requirements(db, jd.id if jd else None),
            "job_requirement_extraction_trace": snapshot_requirement_extraction_trace(
                db, jd.id if jd else None,
            ),
            "evaluation_target_role_key": "technical-product-manager",
            "decision_rule_config_version_id": rule_config_id,
            "decision_rule_config_version": rule_config_version,
            "decision_rule_config_fingerprint": rule_config_fingerprint,
            "evaluation_policy_version": current_policy_version,
            "ai_trace": [],
        },
        evaluated_at=datetime.now(timezone.utc),
    )
    db.add(record)
    db.flush()
    return record

def assert_evaluation_current(db, profile, evaluation_record):
    assert evaluation_record.validity_status == "CURRENT"
    assert OpportunityService(db)._evaluation_inputs_are_current(
        profile, evaluation_record, datetime.now(timezone.utc),
    )


def test_candidate_cannot_create_punt_from_another_candidates_evaluation(db):
    user_a, _ = candidate(db)
    _, profile_b = candidate(db)
    job_record = job(db, "Technical Product Manager")
    foreign_evaluation = evaluation(db, profile_b, job_record)

    with pytest.raises(DomainError) as error:
        OpportunityService(db).create_punt(user_a.id, foreign_evaluation.id)

    assert error.value.status_code == 404
    assert db.scalar(select(Punt)) is None


def test_punt_rejects_and_marks_evaluation_stale_when_job_changes(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record)
    assert_evaluation_current(db, profile, ev)

    job_record.revision += 1
    db.flush()

    with pytest.raises(DomainError) as error:
        OpportunityService(db).create_punt(user.id, ev.id)

    assert error.value.code == "EVALUATION_STALE"
    db.refresh(ev)
    assert ev.validity_status == "STALE"
    assert db.scalar(select(Punt)) is None


def test_punt_rejects_job_field_changes_even_without_revision_increment(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record)
    assert_evaluation_current(db, profile, ev)

    # RevisionMixin is not an automatic SQLAlchemy version counter.
    job_record.canonical_title = "Senior Product Manager"
    db.flush()

    with pytest.raises(DomainError) as error:
        OpportunityService(db).create_punt(user.id, ev.id)

    assert error.value.code == "EVALUATION_STALE"
    db.refresh(ev)
    assert ev.validity_status == "STALE"
    assert db.scalar(select(Punt)) is None


def test_punt_rejects_and_marks_evaluation_stale_when_preferences_change(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record)
    assert_evaluation_current(db, profile, ev)
    db.add(CandidatePreferences(
        candidate_profile_id=profile.id,
        preferences={"remotePreference": "REMOTE_ONLY"},
    ))
    db.flush()

    with pytest.raises(DomainError) as error:
        OpportunityService(db).create_punt(user.id, ev.id)

    assert error.value.code == "EVALUATION_STALE"
    db.refresh(ev)
    assert ev.validity_status == "STALE"
    assert db.scalar(select(Punt)) is None


def test_unified_audit_history_rejects_update_delete_and_truncate(db):
    row = AuditEvent(
        event_type="TEST_AUDIT_IMMUTABILITY",
        resource_type="test",
        resource_id=uuid4(),
        actor_type="USER",
        actor_id=None,
        details={"source": "postgres-trigger-regression"},
        occurred_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()

    statements = [
        "UPDATE audit_events SET event_type = 'TAMPERED' WHERE id = :event_id",
        "DELETE FROM audit_events WHERE id = :event_id",
        "TRUNCATE TABLE audit_events",
    ]
    for statement in statements:
        with pytest.raises(DBAPIError, match="audit history is append-only"):
            with db.begin_nested():
                parameters = {"event_id": row.id} if ":event_id" in statement else {}
                db.execute(text(statement), parameters)

    persisted = db.scalar(select(AuditEvent).where(AuditEvent.id == row.id))
    assert persisted is not None
    assert persisted.event_type == "TEST_AUDIT_IMMUTABILITY"



def test_application_lifecycle_events_are_projected_to_unified_audit(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record)
    assert_evaluation_current(db, profile, ev)
    service = OpportunityService(db)

    punt = service.create_punt(user.id, ev.id)
    application = service.create_application(user.id, punt.id)
    withdrawn = service.transition_application(
        user.id,
        application.id,
        "WITHDRAWN",
        expected_revision=application.revision,
        reason="Candidate chose not to proceed",
        notes="Role priorities changed",
    )

    events = list(db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.resource_type == "application",
            AuditEvent.resource_id == application.id,
        )
        .order_by(AuditEvent.occurred_at, AuditEvent.id)
    ).all())

    assert withdrawn.status == "WITHDRAWN"
    assert [event.event_type for event in events] == [
        "APPLICATION_CREATED",
        "APPLICATION_STATUS_CHANGED",
    ]
    created, transition = events
    assert created.actor_type == "USER"
    assert created.actor_id == user.id
    assert created.from_status is None
    assert created.to_status == "PLANNED"
    assert created.reason == "Application created from a qualified Punt"
    assert created.details == {"punt_id": str(punt.id)}

    assert transition.actor_type == "USER"
    assert transition.actor_id == user.id
    assert transition.from_status == "PLANNED"
    assert transition.to_status == "WITHDRAWN"
    assert transition.reason == "Candidate chose not to proceed"
    assert transition.notes == "Role priorities changed"
    assert transition.occurred_at == withdrawn.updated_at
    persisted_transition = db.scalar(select(ApplicationEvent).where(
        ApplicationEvent.application_id == application.id,
        ApplicationEvent.event_type == "APPLICATION_STATUS_CHANGED",
    ))
    notification_events = list(db.scalars(select(OutboxEvent).where(
        OutboxEvent.event_type == "APPLICATION_STATUS_CHANGED",
        OutboxEvent.aggregate_type == "Application",
        OutboxEvent.aggregate_id == application.id,
    )).all())
    assert persisted_transition is not None
    assert len(notification_events) == 1
    assert notification_events[0].payload == {"application_event_id": str(persisted_transition.id)}


def test_candidate_cannot_access_another_candidates_application(db):
    user_a, _ = candidate(db)
    user_b, profile_b = candidate(db)
    job_record = job(db, "Product Manager")
    own_evaluation = evaluation(db, profile_b, job_record)
    service_b = OpportunityService(db)
    punt = service_b.create_punt(user_b.id, own_evaluation.id)
    application = service_b.create_application(user_b.id, punt.id)

    with pytest.raises(DomainError) as error:
        OpportunityService(db).transition_application(
            user_a.id, application.id, "APPROVED", expected_revision=application.revision, reason="Candidate approval"
        )

    assert error.value.status_code == 404
    db.refresh(application)
    assert application.status == "PLANNED"
    assert db.scalar(select(Application).where(Application.id == application.id)) is not None


def test_application_approval_requires_current_job_and_approved_package(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record)
    assert_evaluation_current(db, profile, ev)
    service = OpportunityService(db)
    punt = service.create_punt(user.id, ev.id)
    application = service.create_application(user.id, punt.id)

    with pytest.raises(DomainError) as error:
        service.transition_application(
            user.id, application.id, "APPROVED", expected_revision=1,
            reason="Candidate approves application",
        )
    assert error.value.code == "APPLICATION_PACKAGE_NOT_APPROVED"
    assert application.status == "PLANNED"
    assert application.revision == 1

    job_record.freshness_status = "STALE"
    db.flush()
    with pytest.raises(DomainError) as error:
        service.transition_application(
            user.id, application.id, "APPROVED", expected_revision=1,
            reason="Candidate approves application",
        )
    assert error.value.code == "JOB_NOT_CURRENT"
    assert application.status == "PLANNED"
    assert application.revision == 1


def test_repeated_application_creation_returns_single_candidate_job_application(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record)
    assert_evaluation_current(db, profile, ev)
    service = OpportunityService(db)
    punt = service.create_punt(user.id, ev.id)

    first = service.create_application(user.id, punt.id)
    second = service.create_application(user.id, punt.id)

    assert first.id == second.id
    assert len(db.scalars(select(Application).where(
        Application.candidate_profile_id == profile.id,
        Application.job_id == job_record.id,
    )).all()) == 1



def test_opportunity_api_denies_cross_candidate_ids_and_lists_only_owned_records(db):
    from fastapi.testclient import TestClient

    from app.api.deps import get_principal
    from app.infrastructure.db.session import get_db
    from app.main import app
    from app.ports.identity import AuthenticatedPrincipal

    user_a, _profile_a = candidate(db)
    user_b, profile_b = candidate(db)
    job_record = job(db, "Technical Product Manager")
    foreign_evaluation = evaluation(db, profile_b, job_record)
    service_b = OpportunityService(db)
    foreign_punt = service_b.create_punt(user_b.id, foreign_evaluation.id)
    foreign_application = service_b.create_application(user_b.id, foreign_punt.id)

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
        own_punts = client.get("/api/v1/candidate/punts")
        own_applications = client.get("/api/v1/candidate/applications")
        assert own_punts.status_code == 200
        assert own_punts.json()["data"] == []
        assert own_applications.status_code == 200
        assert own_applications.json()["data"] == []

        create_foreign_punt = client.post(
            "/api/v1/candidate/punts",
            json={"evaluation_id": str(foreign_evaluation.id)},
        )
        assert create_foreign_punt.status_code == 404

        create_foreign_application = client.post(
            "/api/v1/candidate/applications",
            json={"punt_id": str(foreign_punt.id)},
        )
        assert create_foreign_application.status_code == 404

        transition_foreign_application = client.post(
            f"/api/v1/candidate/applications/{foreign_application.id}/transition",
            json={"target_status": "APPROVED", "expected_revision": foreign_application.revision, "reason": "Candidate wants to apply"},
        )
        assert transition_foreign_application.status_code == 404
        foreign_application_events = client.get(
            f"/api/v1/candidate/applications/{foreign_application.id}/events"
        )
        assert foreign_application_events.status_code == 404

        db.refresh(foreign_application)
        assert foreign_application.status == "PLANNED"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()



def test_concurrent_distinct_evaluations_create_one_punt_and_link_both():
    """Concurrent first-time qualification for one candidate/job must converge safely."""
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=5)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    user_id = profile_id = job_id = evaluation_a_id = evaluation_b_id = None
    try:
        with SessionLocal() as seed_db:
            user, profile = candidate(seed_db)
            job_record = job(seed_db, "Concurrent Technical Product Manager")
            ev_a = evaluation(seed_db, profile, job_record)
            ev_b = evaluation(seed_db, profile, job_record, version=2)
            seed_db.commit()
            user_id, profile_id, job_id = user.id, profile.id, job_record.id
            evaluation_a_id, evaluation_b_id = ev_a.id, ev_b.id

        barrier = Barrier(2)

        def qualify(evaluation_id):
            with SessionLocal() as session:
                barrier.wait(timeout=10)
                punt = OpportunityService(session).create_punt(user_id, evaluation_id)
                return punt.id

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(qualify, evaluation_a_id),
                executor.submit(qualify, evaluation_b_id),
            ]
            punt_ids = [future.result(timeout=20) for future in futures]

        assert punt_ids[0] == punt_ids[1]
        with SessionLocal() as verify_db:
            punts = verify_db.scalars(select(Punt).where(
                Punt.candidate_profile_id == profile_id,
                Punt.job_id == job_id,
            )).all()
            assert len(punts) == 1
            links = verify_db.scalars(select(PuntEvaluation).where(
                PuntEvaluation.punt_id == punts[0].id,
            )).all()
            assert {link.evaluation_id for link in links} == {evaluation_a_id, evaluation_b_id}
            history = verify_db.scalars(select(PuntQualificationHistory).where(
                PuntQualificationHistory.punt_id == punts[0].id,
            )).all()
            assert {row.evaluation_id for row in history} == {evaluation_a_id, evaluation_b_id}
            assert all(row.actor_user_id == user_id for row in history)
            assert all(row.outcome == "APPLY" for row in history)
            assert all(row.policy_version.startswith(POLICY_VERSION) for row in history)
            events = verify_db.scalars(select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "Punt",
                OutboxEvent.aggregate_id == punts[0].id,
                OutboxEvent.event_type == "PUNT_QUALIFIED",
            )).all()
            # Concurrent first-time qualification emits one idempotent notification signal.
            assert len(events) == 1
    finally:
        if profile_id is not None:
            with SessionLocal() as cleanup:
                punt_ids = list(cleanup.scalars(select(Punt.id).where(
                    Punt.candidate_profile_id == profile_id,
                )).all())
                if punt_ids:
                    cleanup.execute(delete(OutboxEvent).where(
                        OutboxEvent.aggregate_type == "Punt",
                        OutboxEvent.aggregate_id.in_(punt_ids),
                    ))
                    cleanup.execute(delete(PuntQualificationHistory).where(
                        PuntQualificationHistory.punt_id.in_(punt_ids),
                    ))
                    cleanup.execute(delete(PuntEvaluation).where(
                        PuntEvaluation.punt_id.in_(punt_ids),
                    ))
                    cleanup.execute(delete(Punt).where(Punt.id.in_(punt_ids)))
                cleanup.execute(delete(CandidateEvaluation).where(
                    CandidateEvaluation.candidate_profile_id == profile_id,
                ))
                cleanup.execute(delete(CandidateProfile).where(CandidateProfile.id == profile_id))
                cleanup.execute(delete(User).where(User.id == user_id))
                cleanup.execute(delete(JobDescriptionVersion).where(JobDescriptionVersion.job_id == job_id))
                cleanup.execute(delete(JobVerificationAttempt).where(JobVerificationAttempt.job_id == job_id))
                cleanup.execute(delete(JobProvenance).where(JobProvenance.job_id == job_id))
                cleanup.execute(delete(Job).where(Job.id == job_id))
                cleanup.commit()
        engine.dispose()



def test_invalid_application_transition_leaves_state_events_and_outbox_unchanged(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record)
    assert_evaluation_current(db, profile, ev)
    service = OpportunityService(db)
    punt = service.create_punt(user.id, ev.id)
    application = service.create_application(user.id, punt.id)
    event_count_before = len(db.scalars(select(ApplicationEvent).where(
        ApplicationEvent.application_id == application.id,
    )).all())
    outbox_count_before = len(db.scalars(select(OutboxEvent).where(
        OutboxEvent.aggregate_type == "Application",
        OutboxEvent.aggregate_id == application.id,
    )).all())

    with pytest.raises(DomainError) as error:
        service.transition_application(
            user.id, application.id, "SUBMITTED", expected_revision=application.revision, reason="Candidate recorded submission",
        )

    assert error.value.code == "INVALID_STATE_TRANSITION"
    db.refresh(application)
    assert application.status == "PLANNED"
    assert application.revision == 1
    assert len(db.scalars(select(ApplicationEvent).where(
        ApplicationEvent.application_id == application.id,
    )).all()) == event_count_before
    assert len(db.scalars(select(OutboxEvent).where(
        OutboxEvent.aggregate_type == "Application",
        OutboxEvent.aggregate_id == application.id,
    )).all()) == outbox_count_before


def test_stale_application_revision_cannot_append_duplicate_lifecycle_events(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record)
    assert_evaluation_current(db, profile, ev)
    service = OpportunityService(db)
    punt = service.create_punt(user.id, ev.id)
    application = service.create_application(user.id, punt.id)

    withdrawn = service.transition_application(
        user.id, application.id, "WITHDRAWN", expected_revision=1,
        reason="Candidate withdrew before preparing a package",
    )
    assert withdrawn.status == "WITHDRAWN"
    assert withdrawn.revision == 2

    with pytest.raises(DomainError) as error:
        service.transition_application(
            user.id, application.id, "CLOSED", expected_revision=1,
            reason="Candidate tries a stale transition",
        )

    assert error.value.code == "REVISION_CONFLICT"
    db.refresh(application)
    assert application.status == "WITHDRAWN"
    assert application.revision == 2
    events = db.scalars(select(ApplicationEvent).where(
        ApplicationEvent.application_id == application.id,
    )).all()
    outbox_events = db.scalars(select(OutboxEvent).where(
        OutboxEvent.aggregate_type == "Application",
        OutboxEvent.aggregate_id == application.id,
    )).all()
    assert len(events) == 2
    assert len(outbox_events) == 1
    assert outbox_events[0].event_type == "APPLICATION_STATUS_CHANGED"
    assert outbox_events[0].payload == {
        "application_event_id": str(next(
            event.id for event in events if event.event_type == "APPLICATION_STATUS_CHANGED"
        ))
    }
    assert [event.to_status for event in events] == ["PLANNED", "WITHDRAWN"]


def test_domain_submission_transition_requires_user_actor_and_sets_timestamp():
    application = SimpleNamespace(
        status="APPROVED",
        revision=2,
        updated_at=None,
        submitted_at=None,
    )

    with pytest.raises(ValueError, match="authenticated user action"):
        transition_application(application, "SUBMITTED", actor_type="SYSTEM", reason="System cannot submit")

    assert application.status == "APPROVED"
    assert application.revision == 2
    assert application.submitted_at is None

    event = transition_application(
        application, "SUBMITTED", actor_type="USER", reason="Candidate recorded submission",
        now=datetime(2026, 10, 11, tzinfo=timezone.utc),
    )
    assert application.status == "SUBMITTED"
    assert application.revision == 3
    assert application.submitted_at == datetime(2026, 10, 11, tzinfo=timezone.utc)
    assert event["actor_type"] == "USER"
    assert event["from_status"] == "APPROVED"
    assert event["to_status"] == "SUBMITTED"

def test_concurrent_application_transitions_allow_only_one_revision_winner():
    """Two state changes against one revision must serialize and emit one transition event."""
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=6)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    user_id = profile_id = job_id = application_id = punt_id = None
    try:
        with SessionLocal() as seed_db:
            user, profile = candidate(seed_db)
            job_record = job(seed_db, "Concurrent Application Lifecycle")
            ev = evaluation(seed_db, profile, job_record)
            service = OpportunityService(seed_db)
            punt = service.create_punt(user.id, ev.id)
            application = service.create_application(user.id, punt.id)
            seed_db.commit()
            user_id, profile_id, job_id = user.id, profile.id, job_record.id
            application_id, punt_id = application.id, punt.id

        barrier = Barrier(2)

        def transition(target_status):
            with SessionLocal() as session:
                barrier.wait(timeout=10)
                try:
                    result = OpportunityService(session).transition_application(
                        user_id, application_id, target_status, expected_revision=1, reason="Candidate lifecycle update",
                    )
                    return ("WON", result.status, result.revision)
                except DomainError as error:
                    if error.code == "REVISION_CONFLICT":
                        return ("CONFLICT", None, None)
                    raise

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(transition, "CLOSED"),
                executor.submit(transition, "WITHDRAWN"),
            ]
            results = [future.result(timeout=20) for future in futures]

        assert sum(result[0] == "WON" for result in results) == 1
        assert sum(result[0] == "CONFLICT" for result in results) == 1

        with SessionLocal() as verify_db:
            persisted = verify_db.get(Application, application_id)
            assert persisted is not None
            assert persisted.status in {"CLOSED", "WITHDRAWN"}
            assert persisted.revision == 2
            events = verify_db.scalars(select(ApplicationEvent).where(
                ApplicationEvent.application_id == application_id,
            )).all()
            assert len(events) == 2
            assert sum(event.event_type == "APPLICATION_STATUS_CHANGED" for event in events) == 1
            outbox_events = verify_db.scalars(select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "Application",
                OutboxEvent.aggregate_id == application_id,
                OutboxEvent.event_type == "APPLICATION_STATUS_CHANGED",
            )).all()
            assert len(outbox_events) == 1
    finally:
        if application_id is not None:
            with SessionLocal() as cleanup:
                cleanup.execute(delete(OutboxEvent).where(
                    OutboxEvent.aggregate_type == "Application",
                    OutboxEvent.aggregate_id == application_id,
                ))
                cleanup.execute(delete(ApplicationEvent).where(
                    ApplicationEvent.application_id == application_id,
                ))
                cleanup.execute(delete(Application).where(Application.id == application_id))
                if punt_id is not None:
                    cleanup.execute(delete(OutboxEvent).where(
                        OutboxEvent.aggregate_type == "Punt",
                        OutboxEvent.aggregate_id == punt_id,
                    ))
                    cleanup.execute(delete(PuntQualificationHistory).where(PuntQualificationHistory.punt_id == punt_id))
                    cleanup.execute(delete(PuntEvaluation).where(PuntEvaluation.punt_id == punt_id))
                    cleanup.execute(delete(Punt).where(Punt.id == punt_id))
                cleanup.execute(delete(CandidateEvaluation).where(
                    CandidateEvaluation.candidate_profile_id == profile_id,
                ))
                cleanup.execute(delete(CandidateProfile).where(CandidateProfile.id == profile_id))
                cleanup.execute(delete(User).where(User.id == user_id))
                cleanup.execute(delete(JobDescriptionVersion).where(JobDescriptionVersion.job_id == job_id))
                cleanup.execute(delete(JobVerificationAttempt).where(JobVerificationAttempt.job_id == job_id))
                cleanup.execute(delete(JobProvenance).where(JobProvenance.job_id == job_id))
                cleanup.execute(delete(Job).where(Job.id == job_id))
                cleanup.commit()
        engine.dispose()

def test_submission_requires_latest_application_package_to_be_approved(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record)
    assert_evaluation_current(db, profile, ev)
    service = OpportunityService(db)
    punt = service.create_punt(user.id, ev.id)
    application = service.create_application(user.id, punt.id)
    application.status = "APPROVED"
    application.revision = 2
    db.flush()
    approved = application
    event_count_before = len(db.scalars(select(ApplicationEvent).where(
        ApplicationEvent.application_id == application.id,
    )).all())
    outbox_count_before = len(db.scalars(select(OutboxEvent).where(
        OutboxEvent.aggregate_type == "Application",
        OutboxEvent.aggregate_id == application.id,
    )).all())

    with pytest.raises(DomainError) as error:
        service.transition_application(
            user.id, application.id, "SUBMITTED", expected_revision=approved.revision, reason="Candidate recorded submission",
        )

    assert error.value.code == "APPLICATION_PACKAGE_NOT_APPROVED"
    db.refresh(application)
    assert application.status == "APPROVED"
    assert application.revision == approved.revision
    assert application.submitted_at is None
    assert len(db.scalars(select(ApplicationEvent).where(
        ApplicationEvent.application_id == application.id,
    )).all()) == event_count_before
    assert len(db.scalars(select(OutboxEvent).where(
        OutboxEvent.aggregate_type == "Application",
        OutboxEvent.aggregate_id == application.id,
    )).all()) == outbox_count_before



def test_evaluation_stales_when_salary_changes_without_revision_increment(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record)
    assert_evaluation_current(db, profile, ev)

    job_record.salary_max = 50000
    job_record.salary_currency = "USD"
    job_record.salary_period = "YEAR"
    db.flush()

    listed = OpportunityService(db).list_evaluations(user.id)
    assert len(listed) == 1
    assert listed[0].id == ev.id
    assert listed[0].validity_status == "STALE"


def test_evaluation_stales_when_profile_facts_change_without_revision_increment(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record)
    assert_evaluation_current(db, profile, ev)

    profile.profile_data = {"professional_identity": {"current_role": "Product Director"}}
    db.flush()

    listed = OpportunityService(db).list_evaluations(user.id)
    assert len(listed) == 1
    assert listed[0].id == ev.id
    assert listed[0].validity_status == "STALE"


def test_evaluation_stales_when_requirement_extraction_execution_changes(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record)
    assert_evaluation_current(db, profile, ev)
    jd = db.scalar(
        select(JobDescriptionVersion)
        .where(JobDescriptionVersion.job_id == job_record.id)
        .order_by(JobDescriptionVersion.version_number.desc())
        .limit(1)
    )
    now = datetime.now(timezone.utc)
    db.add(AIExecution(
        user_id=None,
        task="job_requirement_extraction",
        task_policy_version="v1",
        system_policy_version="v1",
        output_contract_version="v3",
        config_fingerprint="test-extraction-config",
        provider="google",
        model="gemini-2.5-flash",
        status="SUCCEEDED",
        request_id=f"job-requirement-extraction:{jd.id}",
        created_at=now,
        finished_at=now,
    ))
    db.flush()

    listed = OpportunityService(db).list_evaluations(user.id)
    assert len(listed) == 1
    assert listed[0].id == ev.id
    assert listed[0].validity_status == "STALE"


def test_evaluation_stales_when_versioned_requirement_review_changes(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    jd = db.scalar(
        select(JobDescriptionVersion)
        .where(JobDescriptionVersion.job_id == job_record.id)
        .order_by(JobDescriptionVersion.version_number.desc())
        .limit(1)
    )
    jd.requirement_extraction_status = "NEEDS_REVIEW"
    requirement = JobRequirement(
        job_description_version_id=jd.id,
        requirement_type="SKILL",
        description="Experience with SQL query design",
        importance="HIGH",
        severity="MUST_HAVE",
        dimension="TECHNICAL",
        hard_constraint=True,
        extraction_status="NEEDS_REVIEW",
        source_excerpt="Experience with SQL query design.",
        extraction_confidence=0.93,
        extraction_version="job-requirement-extraction-v3",
    )
    db.add(requirement)
    db.flush()
    ev = evaluation(db, profile, job_record)
    assert_evaluation_current(db, profile, ev)

    review_requirement(
        db,
        requirement_id=requirement.id,
        reviewer_user_id=user.id,
        decision="CONFIRM",
        severity="MUST_HAVE",
        dimension="TECHNICAL",
    )

    listed = OpportunityService(db).list_evaluations(user.id)
    assert len(listed) == 1
    assert listed[0].id == ev.id
    assert listed[0].validity_status == "STALE"


@pytest.mark.parametrize(
    ("lifecycle_status", "freshness_status", "expected_code"),
    [
        ("CLOSED", "FRESH", "JOB_NOT_OPEN"),
        ("ACTIVE", "STALE", "JOB_FRESHNESS_UNVERIFIED"),
        ("ACTIVE", "UNKNOWN", "JOB_FRESHNESS_UNVERIFIED"),
    ],
)
def test_punt_rechecks_current_job_lifecycle_and_freshness(
    db, lifecycle_status, freshness_status, expected_code
):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    job_record.lifecycle_status = lifecycle_status
    job_record.freshness_status = freshness_status
    db.flush()
    ev = evaluation(db, profile, job_record)
    assert_evaluation_current(db, profile, ev)

    with pytest.raises(DomainError) as error:
        OpportunityService(db).create_punt(user.id, ev.id)

    assert error.value.code == expected_code
    assert db.scalar(select(Punt).where(
        Punt.candidate_profile_id == profile.id,
        Punt.job_id == job_record.id,
    )) is None


def test_evaluation_stales_when_route_verification_changes(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record)
    assert_evaluation_current(db, profile, ev)
    attempt = db.scalar(
        select(JobVerificationAttempt)
        .where(JobVerificationAttempt.job_id == job_record.id)
        .order_by(JobVerificationAttempt.checked_at.desc())
        .limit(1)
    )
    attempt.status = "UNKNOWN"
    db.flush()

    listed = OpportunityService(db).list_evaluations(user.id)
    assert len(listed) == 1
    assert listed[0].id == ev.id
    assert listed[0].validity_status == "STALE"


def test_punt_rejects_missing_route_verification_even_for_apply_evaluation(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    db.execute(delete(JobVerificationAttempt).where(
        JobVerificationAttempt.job_id == job_record.id,
    ))
    job_record.last_verified_at = None
    db.flush()
    ev = evaluation(db, profile, job_record)
    assert_evaluation_current(db, profile, ev)

    with pytest.raises(DomainError) as error:
        OpportunityService(db).create_punt(user.id, ev.id)

    assert error.value.code == "JOB_APPLICATION_ROUTE_UNVERIFIED"
    assert db.scalar(select(Punt).where(
        Punt.candidate_profile_id == profile.id,
        Punt.job_id == job_record.id,
    )) is None


def test_punt_rechecks_latest_job_description_completeness(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    description = db.scalar(
        select(JobDescriptionVersion)
        .where(JobDescriptionVersion.job_id == job_record.id)
        .order_by(JobDescriptionVersion.version_number.desc())
        .limit(1)
    )
    description.completeness_status = "INCOMPLETE"
    db.flush()
    ev = evaluation(db, profile, job_record)
    assert_evaluation_current(db, profile, ev)

    with pytest.raises(DomainError) as error:
        OpportunityService(db).create_punt(user.id, ev.id)

    assert error.value.code == "JOB_DESCRIPTION_NOT_EVALUABLE"
    assert db.scalar(select(Punt).where(
        Punt.candidate_profile_id == profile.id,
        Punt.job_id == job_record.id,
    )) is None

def test_preference_update_stales_evaluation_without_mutating_profile_facts(db):
    from app.application.preferences.schemas import CandidatePreferencesData
    from app.application.preferences.service import CandidatePreferencesService

    user, profile = candidate(db)
    profile.profile_data = {"professional_identity": {"current_role": "Product Director"}}
    db.flush()
    factual_data_before = dict(profile.profile_data)

    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record)
    assert_evaluation_current(db, profile, ev)

    revision, _ = CandidatePreferencesService(db).put(
        user.id,
        0,
        CandidatePreferencesData.model_validate({
            "remotePreference": "REMOTE_ONLY",
            "targetRoleFamilies": ["Product Management"],
        }),
    )
    assert revision == 1

    db.refresh(profile)
    assert profile.profile_data == factual_data_before
    assert profile.revision == ev.candidate_profile_revision

    listed = OpportunityService(db).list_evaluations(user.id)
    assert len(listed) == 1
    assert listed[0].id == ev.id
    assert listed[0].validity_status == "STALE"
    assert ev.preferences_revision == 0

def test_candidate_opportunity_feed_includes_current_job_details_and_source_provenance(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager", include_public_provenance=False)
    ev = evaluation(db, profile, job_record, outcome="APPLY")
    assert_evaluation_current(db, profile, ev)
    now = datetime.now(timezone.utc)

    source = Source(
        name=f"Official employer feed {uuid4()}",
        source_type="EMPLOYER",
        status="ACTIVE",
        permission_review_status="APPROVED",
        permission_review_reference="test-policy://official-employer-feed",
        permission_reviewed_at=now,
        operational_state="AVAILABLE",
    )
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id,
        connector_type="JSON_API",
        connector_version="1.0.0",
        status="ACTIVE",
        configuration={"endpoint_url": "https://api.example.test/jobs"},
        capabilities={"published_jobs": True},
        limits={},
    )
    db.add(connector)
    db.flush()
    run = AcquisitionRun(
        source_id=source.id,
        source_connector_id=connector.id,
        run_key=f"opportunity-feed-{uuid4()}",
        status="SUCCEEDED",
        started_at=now,
        completed_at=now,
        metrics={"acquired_count": 1, "persisted_count": 1},
    )
    db.add(run)
    db.flush()
    raw = RawSourceRecord(
        acquisition_run_id=run.id,
        source_id=source.id,
        external_record_id="employer-role-77",
        deduplication_key="id:employer-role-77",
        source_url="https://jobs.example.test/role-77",
        raw_content={"title": job_record.canonical_title, "company": job_record.company_name},
        content_hash=f"feed-content-{uuid4()}",
        status="ACQUIRED",
        acquired_at=now,
    )
    db.add(raw)
    db.flush()
    db.add(JobProvenance(
        job_id=job_record.id,
        source_id=source.id,
        raw_source_record_id=raw.id,
        source_url=raw.source_url,
        observation_type="ACQUIRED",
        asserted_by="SOURCE",
        observed_at=now,
        confidence=1,
        status="OBSERVED",
        metadata_json={},
    ))
    db.add(JobDiscoveryEvent(
        source_id=source.id,
        acquisition_run_id=run.id,
        raw_source_record_id=raw.id,
        canonical_job_id=job_record.id,
        external_record_id=raw.external_record_id,
        source_url=raw.source_url,
        content_hash=raw.content_hash,
        observed_at=now,
        observation_outcome="NEW",
        processing_status="CANONICALIZED",
        metadata_json={},
    ))
    db.flush()

    feed = OpportunityService(db).list_opportunity_feed(user.id, limit=10, outcome="APPLY")

    assert feed.pagination.total == 1
    assert len(feed.data) == 1
    item = feed.data[0]
    assert item.job.id == job_record.id
    assert item.job.title == "Technical Product Manager"
    assert item.job.company_name == "Example Employer"
    assert item.job.application_url == job_record.application_url
    assert item.job.application_route_verification["status"] == "ACTIVE"
    assert item.job.description is not None
    assert "Responsibilities include managing product delivery" in item.job.description.content
    assert item.evaluation.id == ev.id
    assert item.evaluation.outcome == "APPLY"
    assert len(item.job.provenance) == 1
    provenance = item.job.provenance[0]
    assert provenance.source_id == source.id
    assert provenance.source_name == source.name
    assert provenance.source_type == "EMPLOYER"
    assert provenance.source_url == raw.source_url
    assert provenance.external_job_id == "employer-role-77"
    assert provenance.observation_count == 1

    assert OpportunityService(db).list_opportunity_feed(
        user.id, limit=10, outcome="REJECT",
    ).pagination.total == 0


def test_candidate_opportunity_feed_never_returns_another_candidates_evaluation(db):
    user_a, profile_a = candidate(db)
    user_b, profile_b = candidate(db)
    job_a = job(db, "Product Manager A")
    job_b = job(db, "Product Manager B")
    evaluation(db, profile_a, job_a, outcome="APPLY")
    evaluation(db, profile_b, job_b, outcome="APPLY")

    feed = OpportunityService(db).list_opportunity_feed(user_a.id, limit=10)

    assert feed.pagination.total == 1
    assert [item.job.id for item in feed.data] == [job_a.id]
    assert all(item.evaluation.id != db.scalar(
        select(CandidateEvaluation.id).where(CandidateEvaluation.candidate_profile_id == profile_b.id)
    ) for item in feed.data)

def test_candidate_opportunity_feed_api_returns_integratable_contract(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    evaluation(db, profile, job_record, outcome="APPLY")

    connection = db.connection()

    def override_db():
        request_session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=user.id, auth_provider="test",
    )
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        response = client.get("/api/v1/candidate/opportunities?outcome=APPLY&limit=10&offset=0")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["pagination"] == {"limit": 10, "offset": 0, "total": 1}
        assert body["data"][0]["job"]["id"] == str(job_record.id)
        assert body["data"][0]["job"]["title"] == "Technical Product Manager"
        assert body["data"][0]["evaluation"]["id"] == str(
            db.scalar(select(CandidateEvaluation.id).where(
                CandidateEvaluation.candidate_profile_id == profile.id,
            ))
        )
        assert body["data"][0]["evaluation"]["outcome"] == "APPLY"
        assert "description" in body["data"][0]["job"]
    finally:
        client.close()
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)




def test_opportunity_feed_is_candidate_scoped_and_returns_latest_evaluation(db):
    """A shared job must expose only the caller's latest evaluation in the feed."""
    from app.application.opportunities.service import OpportunityService

    user_a, profile_a = candidate(db)
    user_b, profile_b = candidate(db)
    shared_job = job(db, "Shared Technical Product Manager")

    older_a = evaluation(db, profile_a, shared_job, version=1)
    latest_a = evaluation(db, profile_a, shared_job, version=2)
    foreign_b = evaluation(db, profile_b, shared_job, version=1)
    db.flush()

    feed = OpportunityService(db).list_opportunity_feed(user_a.id)

    assert feed.pagination.total == 1
    assert len(feed.data) == 1
    item = feed.data[0]
    assert item.job.id == shared_job.id
    assert item.evaluation.id == latest_a.id
    assert item.evaluation.id != older_a.id
    assert item.evaluation.id != foreign_b.id
    assert item.evaluation.evaluation_version == 2
    assert item.evaluation.job_description_version_id == latest_a.job_description_version_id
    assert item.evaluation.target_role_key == "technical-product-manager"


def test_candidate_opportunity_feed_filters_and_paginates_only_owned_evaluations(db):
    from app.application.opportunities.service import OpportunityService

    user_a, profile_a = candidate(db)
    user_b, profile_b = candidate(db)
    job_a = job(db, "Feed Pagination A")
    job_b = job(db, "Feed Pagination B")
    job_review = job(db, "Feed Pagination Review")
    own_a = evaluation(db, profile_a, job_a, outcome="APPLY")
    own_b = evaluation(db, profile_a, job_b, outcome="APPLY")
    own_review = evaluation(db, profile_a, job_review, outcome="REVIEW")
    foreign = evaluation(db, profile_b, job_a, outcome="APPLY")
    db.flush()

    service = OpportunityService(db)
    page_one = service.list_opportunity_feed(user_a.id, limit=1, offset=0, outcome="APPLY")
    page_two = service.list_opportunity_feed(user_a.id, limit=1, offset=1, outcome="APPLY")
    review_feed = service.list_opportunity_feed(user_a.id, limit=10, offset=0, outcome="REVIEW")
    watch_feed = service.list_opportunity_feed(user_a.id, limit=10, offset=0, outcome="WATCH")

    assert page_one.pagination.total == page_two.pagination.total == 2
    assert len(page_one.data) == len(page_two.data) == 1
    page_one_ids = {page_one.data[0].evaluation.id}
    page_two_ids = {page_two.data[0].evaluation.id}
    assert page_one_ids.isdisjoint(page_two_ids)
    assert page_one_ids | page_two_ids == {own_a.id, own_b.id}
    assert foreign.id not in page_one_ids | page_two_ids
    assert review_feed.pagination.total == 1
    assert review_feed.data[0].evaluation.id == own_review.id
    assert watch_feed.pagination.total == 0


def test_opportunity_feed_handles_empty_results_and_invalid_pagination(db):
    user, _profile = candidate(db)
    service = OpportunityService(db)

    empty = service.list_opportunity_feed(user.id, limit=10, offset=0)
    assert empty.data == []
    assert empty.pagination.limit == 10
    assert empty.pagination.offset == 0
    assert empty.pagination.total == 0

    with pytest.raises(DomainError) as error:
        service.list_opportunity_feed(user.id, limit=0, offset=0)
    assert error.value.code == "INVALID_PAGINATION"
    assert error.value.status_code == 422


def test_opportunity_feed_marks_evaluation_stale_when_job_description_changes(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record, outcome="APPLY")
    assert_evaluation_current(db, profile, ev)
    original_description = db.scalar(
        select(JobDescriptionVersion)
        .where(
            JobDescriptionVersion.job_id == job_record.id,
            JobDescriptionVersion.version_number == 1,
        )
    )
    latest_description = JobDescriptionVersion(
        job_id=job_record.id,
        version_number=2,
        content=original_description.content + " Updated mandatory qualification.",
        content_hash=f"opportunity-test-v2-{uuid4()}",
        completeness_status="COMPLETE",
        retrieved_at=datetime.now(timezone.utc),
        requirement_extraction_status="COMPLETE",
        requirement_extraction_version=original_description.requirement_extraction_version,
    )
    db.add(latest_description)
    db.flush()

    feed = OpportunityService(db).list_opportunity_feed(user.id, limit=10)

    assert feed.pagination.total == 1
    item = feed.data[0]
    assert item.evaluation.id == ev.id
    assert item.evaluation.validity_status == "STALE"
    assert item.evaluation.job_description_version_id == original_description.id
    assert item.job.description.version_number == 2


def test_legacy_punt_and_application_cannot_progress_for_another_candidates_private_job(db):
    owner, owner_profile = candidate(db)
    submitter, _ = candidate(db)
    job_record = job(db, "Legacy private role")
    shared_source = db.scalar(
        select(Source).join(JobProvenance, JobProvenance.source_id == Source.id)
        .where(JobProvenance.job_id == job_record.id, Source.source_type != "MANUAL_SUBMISSION")
    )
    shared_source.status = "RESTRICTED"
    private_source = Source(
        name=f"private application source {uuid4()}",
        source_type="MANUAL_SUBMISSION",
        status="ACTIVE",
        permission_review_status="PENDING",
        operational_state="AVAILABLE",
    )
    db.add(private_source)
    db.flush()
    now = datetime.now(timezone.utc)
    db.add(JobProvenance(
        job_id=job_record.id, source_id=private_source.id, raw_source_record_id=None,
        submitted_by_user_id=submitter.id, source_url=job_record.application_url,
        normalized_source_url=job_record.application_url, observation_type="MANUAL_SUBMITTED",
        asserted_by="CANDIDATE", observed_at=now, confidence=1.0, status="OBSERVED",
        metadata_json={},
    ))
    punt = Punt(
        candidate_profile_id=owner_profile.id,
        job_id=job_record.id,
        status="ACTIVE",
        qualified_at=now,
        created_at=now,
        updated_at=now,
    )
    application_record = Application(
        candidate_profile_id=owner_profile.id,
        job_id=job_record.id,
        status="PLANNED",
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add_all([punt, application_record])
    db.commit()

    with pytest.raises(DomainError) as create_error:
        OpportunityService(db).create_application(owner.id, punt.id)
    assert create_error.value.code == "JOB_NOT_FOUND"

    with pytest.raises(DomainError) as approval_error:
        OpportunityService(db).transition_application(
            owner.id, application_record.id, "APPROVED", expected_revision=1,
            reason="Attempt to progress an inaccessible legacy job",
        )
    assert approval_error.value.code == "JOB_NOT_FOUND"
