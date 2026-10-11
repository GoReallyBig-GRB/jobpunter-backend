import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import DomainError
from app.application.opportunities.service import OpportunityService
from app.domain.opportunities.service import transition_application
from app.infrastructure.db.models import (
    Application,
    ApplicationEvent,
    CandidateEvaluation,
    CandidatePreferences,
    CandidateProfile,
    Job,
    Punt,
    PuntEvaluation,
    OutboxEvent,
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


def job(db, title):
    record = Job(canonical_title=title, lifecycle_status="OPEN", freshness_status="FRESH")
    db.add(record)
    db.flush()
    return record


def evaluation(db, profile, job_record, *, outcome="APPLY", validity="CURRENT", blockers=None, version=1):
    record = CandidateEvaluation(
        candidate_profile_id=profile.id,
        job_id=job_record.id,
        target_role_key="technical-product-manager",
        evaluation_version=version,
        validity_status=validity,
        outcome=outcome,
        score=85,
        hard_blockers=blockers or [],
        evidence_summary={},
        explanation={},
        policy_version="test-v1",
        input_snapshot={
            "candidate_profile_revision": profile.revision,
            "approved_evidence": [],
            "approved_claims": [],
            "preferences_revision": 0,
            "preferences": {},
            "job_id": str(job_record.id),
            "job_revision": job_record.revision,
            "job_snapshot": {
                "id": str(job_record.id),
                "canonical_title": job_record.canonical_title,
                "company_name": job_record.company_name,
                "employment_type": job_record.employment_type,
                "lifecycle_status": job_record.lifecycle_status,
                "location_country": job_record.location_country,
                "work_mode": job_record.work_mode,
                "location_restrictions": job_record.location_restrictions,
                "metadata": job_record.metadata_json or {},
                "freshness_status": job_record.freshness_status,
                "application_url": job_record.application_url,
            },
            "job_description_version_id": None,
            "job_description_version_number": None,
            "job_description_content_hash": None,
        },
        evaluated_at=datetime.now(timezone.utc),
    )
    db.add(record)
    db.flush()
    return record


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
            user_a.id, application.id, "APPROVED", expected_revision=application.revision
        )

    assert error.value.status_code == 404
    db.refresh(application)
    assert application.status == "PLANNED"
    assert db.scalar(select(Application).where(Application.id == application.id)) is not None


def test_repeated_application_creation_returns_single_candidate_job_application(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record)
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
            json={"target_status": "APPROVED", "expected_revision": foreign_application.revision},
        )
        assert transition_foreign_application.status_code == 404

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
            events = verify_db.scalars(select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "Punt",
                OutboxEvent.aggregate_id == punts[0].id,
                OutboxEvent.event_type == "PUNT_QUALIFIED",
            )).all()
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
                    cleanup.execute(delete(PuntEvaluation).where(
                        PuntEvaluation.punt_id.in_(punt_ids),
                    ))
                    cleanup.execute(delete(Punt).where(Punt.id.in_(punt_ids)))
                cleanup.execute(delete(CandidateEvaluation).where(
                    CandidateEvaluation.candidate_profile_id == profile_id,
                ))
                cleanup.execute(delete(CandidateProfile).where(CandidateProfile.id == profile_id))
                cleanup.execute(delete(User).where(User.id == user_id))
                cleanup.execute(delete(Job).where(Job.id == job_id))
                cleanup.commit()
        engine.dispose()



def test_invalid_application_transition_leaves_state_events_and_outbox_unchanged(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record)
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
            user.id, application.id, "SUBMITTED", expected_revision=application.revision,
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
    service = OpportunityService(db)
    punt = service.create_punt(user.id, ev.id)
    application = service.create_application(user.id, punt.id)

    approved = service.transition_application(
        user.id, application.id, "APPROVED", expected_revision=1,
    )
    assert approved.status == "APPROVED"
    assert approved.revision == 2

    with pytest.raises(DomainError) as error:
        service.transition_application(
            user.id, application.id, "SUBMITTED", expected_revision=1,
        )

    assert error.value.code == "REVISION_CONFLICT"
    db.refresh(application)
    assert application.status == "APPROVED"
    assert application.revision == 2
    events = db.scalars(select(ApplicationEvent).where(
        ApplicationEvent.application_id == application.id,
    )).all()
    outbox_events = db.scalars(select(OutboxEvent).where(
        OutboxEvent.aggregate_type == "Application",
        OutboxEvent.aggregate_id == application.id,
    )).all()
    assert len(events) == 2
    assert len(outbox_events) == 2
    assert [event.to_status for event in events] == ["PLANNED", "APPROVED"]


def test_domain_submission_transition_requires_candidate_actor_and_sets_timestamp():
    application = SimpleNamespace(
        status="APPROVED",
        revision=2,
        updated_at=None,
        submitted_at=None,
    )

    with pytest.raises(ValueError, match="only be recorded by the candidate"):
        transition_application(application, "SUBMITTED", actor_type="ADMIN")

    assert application.status == "APPROVED"
    assert application.revision == 2
    assert application.submitted_at is None

    event = transition_application(
        application, "SUBMITTED", actor_type="CANDIDATE",
        now=datetime(2026, 10, 11, tzinfo=timezone.utc),
    )
    assert application.status == "SUBMITTED"
    assert application.revision == 3
    assert application.submitted_at == datetime(2026, 10, 11, tzinfo=timezone.utc)
    assert event["actor_type"] == "CANDIDATE"
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
                        user_id, application_id, target_status, expected_revision=1,
                    )
                    return ("WON", result.status, result.revision)
                except DomainError as error:
                    if error.code == "REVISION_CONFLICT":
                        return ("CONFLICT", None, None)
                    raise

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(transition, "APPROVED"),
                executor.submit(transition, "WITHDRAWN"),
            ]
            results = [future.result(timeout=20) for future in futures]

        assert sum(result[0] == "WON" for result in results) == 1
        assert sum(result[0] == "CONFLICT" for result in results) == 1

        with SessionLocal() as verify_db:
            persisted = verify_db.get(Application, application_id)
            assert persisted is not None
            assert persisted.status in {"APPROVED", "WITHDRAWN"}
            assert persisted.revision == 2
            events = verify_db.scalars(select(ApplicationEvent).where(
                ApplicationEvent.application_id == application_id,
            )).all()
            assert len(events) == 2
            assert sum(event.event_type == "APPLICATION_STATUS_CHANGED" for event in events) == 1
            outbox_events = verify_db.scalars(select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "Application",
                OutboxEvent.aggregate_id == application_id,
            )).all()
            assert len(outbox_events) == 2
            assert sum(event.event_type == "APPLICATION_STATUS_CHANGED" for event in outbox_events) == 1
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
                    cleanup.execute(delete(PuntEvaluation).where(PuntEvaluation.punt_id == punt_id))
                    cleanup.execute(delete(Punt).where(Punt.id == punt_id))
                cleanup.execute(delete(CandidateEvaluation).where(
                    CandidateEvaluation.candidate_profile_id == profile_id,
                ))
                cleanup.execute(delete(CandidateProfile).where(CandidateProfile.id == profile_id))
                cleanup.execute(delete(User).where(User.id == user_id))
                cleanup.execute(delete(Job).where(Job.id == job_id))
                cleanup.commit()
        engine.dispose()

def test_submission_requires_latest_application_package_to_be_approved(db):
    user, profile = candidate(db)
    job_record = job(db, "Technical Product Manager")
    ev = evaluation(db, profile, job_record)
    service = OpportunityService(db)
    punt = service.create_punt(user.id, ev.id)
    application = service.create_application(user.id, punt.id)
    approved = service.transition_application(
        user.id, application.id, "APPROVED", expected_revision=1,
    )
    event_count_before = len(db.scalars(select(ApplicationEvent).where(
        ApplicationEvent.application_id == application.id,
    )).all())
    outbox_count_before = len(db.scalars(select(OutboxEvent).where(
        OutboxEvent.aggregate_type == "Application",
        OutboxEvent.aggregate_id == application.id,
    )).all())

    with pytest.raises(DomainError) as error:
        service.transition_application(
            user.id, application.id, "SUBMITTED", expected_revision=approved.revision,
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

