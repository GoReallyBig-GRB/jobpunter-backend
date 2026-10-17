"""PostgreSQL regression coverage for durable discovery mission scheduling."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select, func
from sqlalchemy.orm import Session

from app.application.jobs.discovery_missions import (
    create_discovery_mission,
    schedule_due_missions,
)
from app.infrastructure.db.models import (
    AcquisitionRun,
    CandidateDiscoverySettings,
    CandidateEvaluation,
    CandidateProfile,
    CareerPathway,
    DiscoveryMission,
    DiscoveryMissionSourceRun,
    Job,
    JobDescriptionVersion,
    JobResolution,
    OutboxEvent,
    RawSourceRecord,
    Source,
    SourceConnector,
    User,
    WorkItem,
)



def _seed_active_sources(db, count=2):
    pairs = []
    for index in range(count):
        source = Source(
            name=f"truthfulness source {uuid4()}",
            source_type="EMPLOYER",
            status="ACTIVE",
            permission_review_status="APPROVED",
            permission_review_reference=f"test-permission-review://approved/{uuid4()}",
            permission_reviewed_at=datetime.now(timezone.utc),
        )
        db.add(source)
        db.flush()
        connector = SourceConnector(
            source_id=source.id,
            connector_type="JOBSPY",
            connector_version="1",
            status="ACTIVE",
        )
        db.add(connector)
        db.flush()
        pairs.append((source, connector))
    return pairs


def _run_mission_with_source_outcomes(
    db, monkeypatch, outcomes, *, unapproved_source_indices=None,
    legacy_acquisition_context=None, captured_contexts=None,
):
    from app.workers import discovery_mission_worker as mission_worker

    user, profile = _candidate(db)
    mission = create_discovery_mission(
        db,
        candidate_profile_id=profile.id,
        idempotency_key=f"source-outcomes-{uuid4()}",
    )
    if legacy_acquisition_context is not None:
        mission.policy_snapshot = {
            **mission.policy_snapshot,
            "source_filters": {"acquisition_context": legacy_acquisition_context},
        }
        db.flush()
    work_item = db.scalar(select(WorkItem).where(
        WorkItem.work_type == "DISCOVERY_MISSION",
        WorkItem.resource_id == mission.id,
    ))
    assert work_item is not None
    pairs = _seed_active_sources(db, len(outcomes))
    for index in unapproved_source_indices or set():
        source, _connector = pairs[index]
        source.permission_review_status = "PENDING"
        source.permission_review_reference = None
        source.permission_reviewed_at = None
    by_source_id = {source.id: outcomes[index] for index, (source, _connector) in enumerate(pairs)}
    monkeypatch.setattr(
        mission_worker,
        "_eligible_sources",
        lambda _db, _wave_key, _filters: pairs,
    )

    class ControlledAcquisition:
        def __init__(self, repository, registry=None):
            self.db = repository.db

        def run(self, source_id, connector_id, run_key, context):
            if captured_contexts is not None:
                captured_contexts.append(context)
            outcome = by_source_id[source_id]
            if outcome == "fail":
                raise RuntimeError("connector unavailable")
            status = {
                "success": "SUCCEEDED",
                "empty": "SUCCEEDED",
                "partial": "PARTIAL",
                "unavailable": "UNAVAILABLE",
                "restricted": "RESTRICTED",
            }[outcome]
            acquired_count = 0 if outcome in {"empty", "unavailable", "restricted"} else 1
            persisted_count = 1 if outcome == "success" else 0
            error_category = {
                "partial": "PAGE_FETCH_FAILED",
                "unavailable": "UPSTREAM_UNAVAILABLE",
                "restricted": "SOURCE_RESTRICTED",
            }.get(outcome)
            now = datetime.now(timezone.utc)
            run = AcquisitionRun(
                source_id=source_id,
                source_connector_id=connector_id,
                run_key=run_key,
                status=status,
                error_summary=error_category,
                started_at=now,
                completed_at=now,
                metrics={
                    "acquired_count": acquired_count,
                    "persisted_count": persisted_count,
                    "error_category": error_category,
                    "failed_item_count": 1 if outcome == "partial" else 0,
                },
            )
            self.db.add(run)
            self.db.flush()
            return SimpleNamespace(
                run_id=run.id,
                status=status,
                acquired_count=acquired_count,
                persisted_count=persisted_count,
                error_category=error_category,
                failed_item_count=1 if outcome == "partial" else 0,
                retry_after_seconds=30 if outcome == "restricted" else None,
            )

    monkeypatch.setattr(mission_worker, "RegisteredAcquisitionService", ControlledAcquisition)
    status = mission_worker.process_mission_step(db, mission.id)
    db.refresh(mission)
    db.refresh(work_item)
    rows = db.scalars(select(DiscoveryMissionSourceRun).where(
        DiscoveryMissionSourceRun.mission_id == mission.id,
    ).order_by(DiscoveryMissionSourceRun.created_at.asc())).all()
    return mission, work_item, rows, status


def test_all_source_failures_remain_visible_and_do_not_report_empty_success(db, monkeypatch):
    from app.workers import discovery_mission_worker as mission_worker

    mission, work_item, rows, status = _run_mission_with_source_outcomes(
        db, monkeypatch, ["fail", "fail"]
    )
    assert status == "PENDING"  # Retry remaining configured waves, not an empty success.
    assert mission.status == "PENDING"
    assert work_item.status == "PENDING"
    assert len(rows) == 2
    assert all(row.status == "FAILED" for row in rows)
    assert all(row.error_code == "RuntimeError" for row in rows)
    assert all(row.persisted_count == 0 for row in rows)

    for _ in range(len(mission.policy_snapshot["waves"]) + 1):
        status = mission_worker.process_mission_step(db, mission.id)
        db.refresh(mission)
        db.refresh(work_item)
        if mission.status in {"FAILED", "EXHAUSTED", "TARGET_MET", "CANCELLED"}:
            break

    assert status == "FAILED"
    assert mission.status == "FAILED"
    assert mission.error_code == "ALL_SOURCES_FAILED"
    assert work_item.status == "FAILED"
    assert work_item.error_code == "ALL_SOURCES_FAILED"
    assert work_item.result["mission_status"] == "FAILED"
    assert work_item.result["failure_categories"] == ["RuntimeError"]


def test_legacy_candidate_context_cannot_override_connector_configuration(db, monkeypatch):
    captured_contexts = []
    _mission, _work_item, rows, status = _run_mission_with_source_outcomes(
        db,
        monkeypatch,
        ["success"],
        legacy_acquisition_context={
            "board_token": "candidate-controlled-token",
            "endpoint_url": "https://attacker.example/jobs",
        },
        captured_contexts=captured_contexts,
    )

    assert status == "WAITING_FOR_CANONICALIZATION"
    assert len(rows) == 1
    assert rows[0].status == "SUCCEEDED"
    assert captured_contexts == [{}]


def test_discovery_permission_review_block_is_recorded_as_restricted_not_connector_failure(db, monkeypatch):
    _mission, _work_item, rows, _status = _run_mission_with_source_outcomes(
        db, monkeypatch, ["success"], unapproved_source_indices={0},
    )

    assert len(rows) == 1
    source_run = rows[0]
    assert source_run.status == "RESTRICTED"
    assert source_run.error_code == "PERMISSION_REVIEW_REQUIRED"
    assert source_run.error_details == {"reason_code": "SOURCE_PERMISSION_REVIEW_REQUIRED"}

    source = db.get(Source, source_run.source_id)
    assert source.operational_state == "RESTRICTED"
    assert source.last_error_category == "PERMISSION_REVIEW_REQUIRED"
    assert db.scalar(select(func.count()).select_from(AcquisitionRun).where(
        AcquisitionRun.source_id == source.id
    )) == 0


def test_partial_source_success_preserves_both_success_and_failure_outcomes(db, monkeypatch):
    mission, work_item, rows, status = _run_mission_with_source_outcomes(
        db, monkeypatch, ["success", "fail"]
    )

    assert status == "WAITING_FOR_CANONICALIZATION"
    assert mission.status == "WAITING_FOR_CANONICALIZATION"
    assert work_item.status == "PENDING"
    assert len(rows) == 2
    assert sorted(row.status for row in rows) == ["FAILED", "SUCCEEDED"]
    failed = next(row for row in rows if row.status == "FAILED")
    succeeded = next(row for row in rows if row.status == "SUCCEEDED")
    assert failed.error_code == "RuntimeError"
    assert failed.persisted_count == 0
    assert succeeded.persisted_count == 1


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
    user = User(display_name=f"Discovery candidate {uuid4()}")
    db.add(user)
    db.flush()
    profile = CandidateProfile(user_id=user.id, status="ACTIVE")
    db.add(profile)
    db.flush()
    return user, profile


def _settings(
    db, profile, *, status="ENABLED", next_run_at=None, cadence_minutes=60,
    schedule_type="INTERVAL", schedule_time="09:00", schedule_timezone="UTC",
    schedule_weekday=None,
):
    settings = CandidateDiscoverySettings(
        candidate_profile_id=profile.id,
        status=status,
        cadence_minutes=cadence_minutes,
        schedule_type=schedule_type,
        schedule_time=schedule_time,
        schedule_timezone=schedule_timezone,
        schedule_weekday=schedule_weekday,
        next_run_at=next_run_at,
        target_opportunities=12,
        max_job_age_days=14,
        verification_required=True,
        evaluation_depth="preliminary",
        source_filters={},
    )
    db.add(settings)
    db.flush()
    return settings


def test_idempotent_mission_creation_persists_exactly_one_work_item(db):
    _, profile = _candidate(db)

    first = create_discovery_mission(
        db,
        candidate_profile_id=profile.id,
        idempotency_key="manual-replay-1",
    )
    replay = create_discovery_mission(
        db,
        candidate_profile_id=profile.id,
        idempotency_key="manual-replay-1",
    )

    assert replay.id == first.id
    assert replay.status == "PENDING"
    assert replay.policy_snapshot["target_opportunities"] == 20
    assert db.scalar(select(func.count()).select_from(DiscoveryMission).where(
        DiscoveryMission.candidate_profile_id == profile.id
    )) == 1
    work_items = db.scalars(select(WorkItem).where(
        WorkItem.work_type == "DISCOVERY_MISSION",
        WorkItem.resource_id == first.id,
    )).all()
    assert len(work_items) == 1
    assert work_items[0].status == "PENDING"
    assert work_items[0].idempotency_key == f"discovery-mission:{first.id}"


def test_mission_policy_snapshot_is_not_rewritten_when_settings_change(db):
    _, profile = _candidate(db)
    settings = _settings(db, profile, status="ENABLED")
    mission = create_discovery_mission(
        db,
        candidate_profile_id=profile.id,
        settings_id=settings.id,
        idempotency_key="snapshot-policy-1",
    )
    original_snapshot = dict(mission.policy_snapshot)

    settings.target_opportunities = 4
    settings.max_job_age_days = 3
    settings.source_filters = {"source_types": ["EMPLOYER"]}
    db.flush()

    persisted = db.get(DiscoveryMission, mission.id)
    assert persisted.policy_snapshot == original_snapshot
    assert persisted.policy_snapshot["target_opportunities"] == 12
    assert persisted.policy_snapshot["max_job_age_days"] == 14


def test_weekly_scheduler_advances_to_next_local_occurrence_after_outage(db):
    _, profile = _candidate(db)
    now = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
    settings = _settings(
        db,
        profile,
        status="ENABLED",
        next_run_at=datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc),
        cadence_minutes=60,
        schedule_type="WEEKLY",
        schedule_time="09:00",
        schedule_timezone="Africa/Lagos",
        schedule_weekday=0,
    )

    mission_ids = schedule_due_missions(db, now=now, limit=5)

    assert len(mission_ids) == 1
    db.refresh(settings)
    assert settings.last_scheduled_at == now
    assert settings.next_run_at == datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)
    assert schedule_due_missions(db, now=now, limit=5) == []


def test_scheduled_mission_rejects_disabled_or_foreign_settings(db):
    _, profile = _candidate(db)
    _, other_profile = _candidate(db)
    disabled = _settings(db, profile, status="DISABLED")
    foreign = _settings(db, other_profile, status="ENABLED")

    with pytest.raises(ValueError, match="enabled candidate settings"):
        create_discovery_mission(
            db,
            candidate_profile_id=profile.id,
            settings_id=disabled.id,
            idempotency_key="disabled-schedule",
            trigger_type="SCHEDULED",
        )
    with pytest.raises(ValueError, match="do not belong to candidate"):
        create_discovery_mission(
            db,
            candidate_profile_id=profile.id,
            settings_id=foreign.id,
            idempotency_key="foreign-schedule",
            trigger_type="SCHEDULED",
        )

    assert db.scalar(select(func.count()).select_from(DiscoveryMission)) == 0
    assert db.scalar(select(func.count()).select_from(WorkItem).where(
        WorkItem.work_type == "DISCOVERY_MISSION"
    )) == 0


def test_scheduler_advances_due_slot_and_does_not_enqueue_it_twice(db):
    _, profile = _candidate(db)
    now = datetime.now(timezone.utc)
    settings = _settings(
        db, profile, status="ENABLED",
        next_run_at=now - timedelta(minutes=180),
        cadence_minutes=60,
    )

    first_ids = schedule_due_missions(db, now=now, limit=5)
    assert len(first_ids) == 1
    db.refresh(settings)
    assert settings.last_scheduled_at == now
    assert settings.next_run_at > now

    second_ids = schedule_due_missions(db, now=now, limit=5)
    assert second_ids == []
    assert db.scalar(select(func.count()).select_from(DiscoveryMission).where(
        DiscoveryMission.candidate_profile_id == profile.id
    )) == 1
    assert db.scalar(select(func.count()).select_from(WorkItem).where(
        WorkItem.resource_id == first_ids[0],
        WorkItem.work_type == "DISCOVERY_MISSION",
    )) == 1


@pytest.fixture
def committed_discovery_candidate():
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=5)
    setup = Session(engine)
    try:
        user, profile = _candidate(setup)
        settings = _settings(
            setup, profile, status="ENABLED",
            next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            cadence_minutes=60,
        )
        setup.commit()
        ids = {
            "user_id": user.id,
            "profile_id": profile.id,
            "settings_id": settings.id,
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
            mission_ids = list(cleanup.scalars(select(DiscoveryMission.id).where(
                DiscoveryMission.candidate_profile_id == ids["profile_id"]
            )).all())
            if mission_ids:
                cleanup.execute(delete(OutboxEvent).where(
                    OutboxEvent.aggregate_type == "DiscoveryMission",
                    OutboxEvent.aggregate_id.in_(mission_ids),
                ))
                cleanup.execute(delete(WorkItem).where(
                    WorkItem.work_type == "DISCOVERY_MISSION",
                    WorkItem.resource_id.in_(mission_ids),
                ))
                cleanup.execute(delete(DiscoveryMissionSourceRun).where(
                    DiscoveryMissionSourceRun.mission_id.in_(mission_ids)
                ))
                cleanup.execute(delete(DiscoveryMission).where(
                    DiscoveryMission.id.in_(mission_ids)
                ))
            cleanup.execute(delete(CandidateDiscoverySettings).where(
                CandidateDiscoverySettings.id == ids["settings_id"]
            ))
            cleanup.execute(delete(CandidateProfile).where(
                CandidateProfile.id == ids["profile_id"]
            ))
            cleanup.execute(delete(User).where(User.id == ids["user_id"]))
            cleanup.commit()
        finally:
            cleanup.close()
            engine.dispose()


def test_concurrent_mission_creation_with_same_key_converges(
    committed_discovery_candidate,
):
    engine, ids = committed_discovery_candidate
    barrier = Barrier(2)

    def create_one():
        session = Session(engine)
        try:
            barrier.wait(timeout=5)
            mission = create_discovery_mission(
                session,
                candidate_profile_id=ids["profile_id"],
                idempotency_key="concurrent-manual-mission",
            )
            return mission.id
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        mission_ids = list(pool.map(lambda _: create_one(), range(2)))

    assert mission_ids[0] == mission_ids[1]
    check = Session(engine)
    try:
        missions = check.scalars(select(DiscoveryMission).where(
            DiscoveryMission.candidate_profile_id == ids["profile_id"],
            DiscoveryMission.idempotency_key == "concurrent-manual-mission",
        )).all()
        work_items = check.scalars(select(WorkItem).where(
            WorkItem.work_type == "DISCOVERY_MISSION",
            WorkItem.resource_id == mission_ids[0],
        )).all()
        assert len(missions) == 1
        assert len(work_items) == 1
    finally:
        check.close()


def test_concurrent_scheduler_workers_claim_a_due_slot_only_once(
    committed_discovery_candidate,
):
    engine, ids = committed_discovery_candidate
    barrier = Barrier(2)
    now = datetime.now(timezone.utc)

    def schedule_one():
        session = Session(engine)
        try:
            barrier.wait(timeout=5)
            return schedule_due_missions(session, now=now, limit=5)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: schedule_one(), range(2)))

    created_ids = [mission_id for result in results for mission_id in result]
    assert len(created_ids) == 1
    check = Session(engine)
    try:
        missions = check.scalars(select(DiscoveryMission).where(
            DiscoveryMission.candidate_profile_id == ids["profile_id"],
            DiscoveryMission.trigger_type == "SCHEDULED",
        )).all()
        assert len(missions) == 1
        assert missions[0].id == created_ids[0]
        work_items = check.scalars(select(WorkItem).where(
            WorkItem.work_type == "DISCOVERY_MISSION",
            WorkItem.resource_id == created_ids[0],
        )).all()
        assert len(work_items) == 1
    finally:
        check.close()


def _create_committed_mission(engine, profile_id, key):
    session = Session(engine)
    try:
        mission = create_discovery_mission(
            session,
            candidate_profile_id=profile_id,
            idempotency_key=key,
        )
        return mission.id
    finally:
        session.close()


def test_concurrent_discovery_workers_claim_one_mission_once(
    committed_discovery_candidate,
):
    from app.workers.discovery_mission_worker import DiscoveryMissionWorker

    engine, ids = committed_discovery_candidate
    mission_id = _create_committed_mission(
        engine, ids["profile_id"], "worker-claim-concurrency"
    )
    barrier = Barrier(2)

    def gated_session_factory():
        barrier.wait(timeout=5)
        return Session(engine)

    worker = DiscoveryMissionWorker(session_factory=gated_session_factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker.claim_batch(limit=1), range(2)))

    claimed = [value for batch in results for value in batch]
    assert claimed == [mission_id]
    check = Session(engine)
    try:
        work_item = check.scalar(select(WorkItem).where(
            WorkItem.work_type == "DISCOVERY_MISSION",
            WorkItem.resource_id == mission_id,
        ))
        assert work_item.status == "PROCESSING"
        assert work_item.attempt_count == 1
    finally:
        check.close()


def test_discovery_worker_reclaims_stale_processing_claim(
    committed_discovery_candidate,
):
    from app.workers.discovery_mission_worker import DiscoveryMissionWorker

    engine, ids = committed_discovery_candidate
    mission_id = _create_committed_mission(
        engine, ids["profile_id"], "worker-stale-claim"
    )
    stale_started = datetime.now(timezone.utc) - timedelta(minutes=10)
    setup = Session(engine)
    try:
        mission = setup.get(DiscoveryMission, mission_id)
        mission.next_attempt_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        work_item = setup.scalar(select(WorkItem).where(
            WorkItem.work_type == "DISCOVERY_MISSION",
            WorkItem.resource_id == mission_id,
        ))
        work_item.status = "PROCESSING"
        work_item.started_at = stale_started
        work_item.attempt_count = 2
        setup.commit()
    finally:
        setup.close()

    worker = DiscoveryMissionWorker(session_factory=lambda: Session(engine), lease_seconds=300)
    assert worker.claim_batch(limit=1) == [mission_id]
    check = Session(engine)
    try:
        work_item = check.scalar(select(WorkItem).where(
            WorkItem.work_type == "DISCOVERY_MISSION",
            WorkItem.resource_id == mission_id,
        ))
        assert work_item.status == "PROCESSING"
        assert work_item.attempt_count == 3
        assert work_item.started_at > stale_started
    finally:
        check.close()


def test_discovery_worker_does_not_claim_mission_before_next_attempt(
    committed_discovery_candidate,
):
    from app.workers.discovery_mission_worker import DiscoveryMissionWorker

    engine, ids = committed_discovery_candidate
    mission_id = _create_committed_mission(
        engine, ids["profile_id"], "worker-not-before"
    )
    setup = Session(engine)
    try:
        mission = setup.get(DiscoveryMission, mission_id)
        mission.next_attempt_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        setup.commit()
    finally:
        setup.close()

    worker = DiscoveryMissionWorker(session_factory=lambda: Session(engine))
    assert worker.claim_batch(limit=1) == []
    check = Session(engine)
    try:
        work_item = check.scalar(select(WorkItem).where(
            WorkItem.work_type == "DISCOVERY_MISSION",
            WorkItem.resource_id == mission_id,
        ))
        assert work_item.status == "PENDING"
        assert work_item.attempt_count == 0
    finally:
        check.close()


def test_terminal_discovery_mission_completes_its_work_item(
    committed_discovery_candidate,
):
    from app.workers.discovery_mission_worker import process_mission_step

    engine, ids = committed_discovery_candidate
    mission_id = _create_committed_mission(
        engine, ids["profile_id"], "worker-terminal-mission"
    )
    setup = Session(engine)
    try:
        mission = setup.get(DiscoveryMission, mission_id)
        mission.status = "EXHAUSTED"
        work_item = setup.scalar(select(WorkItem).where(
            WorkItem.work_type == "DISCOVERY_MISSION",
            WorkItem.resource_id == mission_id,
        ))
        work_item.status = "PROCESSING"
        setup.commit()
    finally:
        setup.close()

    processor = Session(engine)
    try:
        assert process_mission_step(processor, mission_id) == "EXHAUSTED"
    finally:
        processor.close()

    check = Session(engine)
    try:
        work_item = check.scalar(select(WorkItem).where(
            WorkItem.work_type == "DISCOVERY_MISSION",
            WorkItem.resource_id == mission_id,
        ))
        assert work_item.status == "COMPLETED"
        assert work_item.completed_at is not None
    finally:
        check.close()


def test_missing_discovery_work_item_fails_mission_closed(
    committed_discovery_candidate,
):
    from app.workers.discovery_mission_worker import process_mission_step

    engine, ids = committed_discovery_candidate
    mission_id = _create_committed_mission(
        engine, ids["profile_id"], "worker-missing-work-item"
    )
    setup = Session(engine)
    try:
        setup.execute(delete(WorkItem).where(
            WorkItem.work_type == "DISCOVERY_MISSION",
            WorkItem.resource_id == mission_id,
        ))
        setup.commit()
    finally:
        setup.close()

    processor = Session(engine)
    try:
        assert process_mission_step(processor, mission_id) == "FAILED"
        # A repeated missing-work-item recovery must not enqueue a duplicate alert.
        assert process_mission_step(processor, mission_id) == "FAILED"
    finally:
        processor.close()

    check = Session(engine)
    try:
        mission = check.get(DiscoveryMission, mission_id)
        assert mission.status == "FAILED"
        assert mission.error_code == "WORK_ITEM_MISSING"
        assert mission.completed_at is not None
        completion_events = check.scalars(select(OutboxEvent).where(
            OutboxEvent.event_type == "DISCOVERY_MISSION_COMPLETED",
            OutboxEvent.aggregate_type == "DiscoveryMission",
            OutboxEvent.aggregate_id == mission_id,
        )).all()
        assert len(completion_events) == 1
        assert completion_events[0].payload == {"mission_id": str(mission_id)}
    finally:
        check.close()



def test_mission_snapshots_only_approved_pathways_and_does_not_rewrite_them(db):
    user, profile = _candidate(db)
    now = datetime.now(timezone.utc)
    approved = CareerPathway(
        candidate_profile_id=profile.id,
        title="Platform Product Management",
        role_family="Product Management",
        target_roles=["Platform Product Manager", "Technical Product Manager"],
        target_seniority=["Senior"],
        domains=["B2B SaaS"],
        supporting_evidence_refs=[],
        gaps=[],
        rationale="Candidate-defined pathway.",
        confidence=None,
        source_type="USER_DEFINED",
        approval_status="APPROVED",
        approved_at=now,
        approved_by_user_id=user.id,
        revision=2,
        created_at=now,
        updated_at=now,
    )
    pending = CareerPathway(
        candidate_profile_id=profile.id,
        title="Unreviewed AI suggestion",
        role_family="Product Management",
        target_roles=["AI Product Manager"],
        target_seniority=[],
        domains=["AI"],
        supporting_evidence_refs=[],
        gaps=["Evidence gap"],
        rationale="AI-generated suggestion.",
        confidence=0.5,
        source_type="AI_SUGGESTED",
        approval_status="PENDING_REVIEW",
        approved_at=None,
        approved_by_user_id=None,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add_all([approved, pending])
    db.flush()

    mission = create_discovery_mission(
        db,
        candidate_profile_id=profile.id,
        idempotency_key="pathway-snapshot-1",
    )
    snapshot = mission.policy_snapshot["approved_career_pathways"]
    assert len(snapshot) == 1
    assert snapshot[0]["id"] == str(approved.id)
    assert snapshot[0]["revision"] == 2
    assert snapshot[0]["target_roles"] == ["Platform Product Manager", "Technical Product Manager"]

    approved.target_roles = ["Product Operations"]
    approved.revision = 3
    db.flush()
    db.refresh(mission)
    assert mission.policy_snapshot["approved_career_pathways"] == snapshot


def test_mission_is_cancelled_when_approved_pathway_snapshot_changes(db):
    from app.workers.discovery_mission_worker import process_mission_step

    user, profile = _candidate(db)
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
        rationale="Candidate-approved target.",
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
    mission = create_discovery_mission(
        db, candidate_profile_id=profile.id, idempotency_key="pathway-change-cancel"
    )
    pathway.approval_status = "RETIRED"
    pathway.revision = 2
    pathway.updated_at = datetime.now(timezone.utc)
    db.flush()

    assert process_mission_step(db, mission.id) == "CANCELLED"
    db.refresh(mission)
    work_item = db.scalar(select(WorkItem).where(
        WorkItem.work_type == "DISCOVERY_MISSION",
        WorkItem.resource_id == mission.id,
    ))
    assert mission.error_code == "CAREER_PATHWAYS_CHANGED"
    assert mission.completed_at is not None
    assert work_item.status == "COMPLETED"
    assert work_item.error_code == "CAREER_PATHWAYS_CHANGED"
    assert db.scalars(select(DiscoveryMissionSourceRun).where(
        DiscoveryMissionSourceRun.mission_id == mission.id
    )).all() == []

def test_mission_curation_awaits_candidate_evaluation_and_rejects_boilerplate_jd(db, monkeypatch):
    from app.workers import discovery_mission_worker as mission_worker

    _, profile = _candidate(db)
    now = datetime.now(timezone.utc)
    mission = DiscoveryMission(
        candidate_profile_id=profile.id,
        idempotency_key=f"completeness-curation-{uuid4()}",
        trigger_type="MANUAL",
        status="RUNNING",
        policy_snapshot={
            "target_opportunities": 10,
            "max_job_age_days": 21,
            "verification_required": True,
            "approved_career_pathways": [
                {"id": "approved-pathway", "revision": 1, "target_roles": ["Technical Product Manager"]},
            ],
        },
    )
    db.add(mission)
    source = Source(
        name=f"curation source {uuid4()}",
        source_type="EMPLOYER",
        status="ACTIVE",
    )
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id,
        connector_type="JSON_API",
        connector_version="1.0.0",
        status="ACTIVE",
    )
    db.add(connector)
    db.flush()
    run = AcquisitionRun(
        source_id=source.id,
        source_connector_id=connector.id,
        run_key=f"curation-run-{uuid4()}",
        status="SUCCEEDED",
        started_at=now,
        completed_at=now,
        metrics={"acquired_count": 2, "persisted_count": 2},
    )
    db.add(run)
    db.flush()
    source_run = DiscoveryMissionSourceRun(
        mission_id=mission.id,
        source_id=source.id,
        source_connector_id=connector.id,
        acquisition_run_id=run.id,
        wave_key="official_feeds",
        attempt_number=1,
        run_key=run.run_key,
        status="SUCCEEDED",
        acquired_count=2,
        persisted_count=2,
        canonicalized_count=2,
        usable_count=0,
        error_details={},
        started_at=now,
        completed_at=now,
        created_at=now,
    )
    db.add(source_run)
    db.flush()

    substantive_description = (
        "Responsibilities include managing product delivery, coordinating engineers, defining roadmaps, "
        "writing technical requirements, and improving release outcomes. Requirements include product "
        "management experience, technical fluency, stakeholder communication, and analytical skills. "
    ) * 3
    boilerplate_description = (
        "This is an excellent opportunity for a motivated individual. Apply today to join our team "
        "and enjoy great benefits, a collaborative culture, and career growth."
    )
    job_records = []
    for index, description_text in enumerate((substantive_description, boilerplate_description)):
        job_record = Job(
            canonical_title=f"Technical Product Manager {index}",
            company_name=f"Curation Employer {index}",
            lifecycle_status="ACTIVE",
            freshness_status="FRESH",
            identity_status="CANONICAL",
            application_url=f"https://example.test/curation/{index}",
            location_country="Nigeria",
            work_mode="REMOTE",
            original_location_text="Remote - Nigeria",
            last_observed_at=now,
            last_verified_at=now,
            metadata_json={},
            location_restrictions={},
        )
        db.add(job_record)
        db.flush()
        description = JobDescriptionVersion(
            job_id=job_record.id,
            version_number=1,
            content=description_text,
            content_hash=f"curation-jd-{uuid4()}",
            completeness_status="FULL",
            retrieved_at=now,
            requirement_extraction_status="COMPLETE",
        )
        db.add(description)
        raw = RawSourceRecord(
            acquisition_run_id=run.id,
            source_id=source.id,
            external_record_id=f"curation-job-{index}-{uuid4()}",
            deduplication_key=f"curation-job-{index}-{uuid4()}",
            source_url=job_record.application_url,
            raw_content={"title": job_record.canonical_title, "description": description_text},
            content_hash=f"curation-raw-{uuid4()}",
            status="ACQUIRED",
            acquired_at=now,
        )
        db.add(raw)
        db.flush()
        db.add(JobResolution(
            raw_source_record_id=raw.id,
            job_id=job_record.id,
            resolution_type="MATCHED",
            resolution_method="SOURCE_EXTERNAL_ID",
            confidence=1.0,
            resolver_version="test-resolver-v1",
            status="MATCHED",
            resolved_at=now,
            created_at=now,
        ))
        job_records.append(job_record)
    db.flush()

    evaluated_job_ids = []

    async def fake_evaluate(self, user_id, job_id, target_role_key, *, commit=True):
        assert commit is False
        evaluated_job_ids.append(job_id)
        return SimpleNamespace(outcome="APPLY")

    monkeypatch.setattr(mission_worker.CandidateEvaluationService, "evaluate", fake_evaluate)
    usable_count, per_run, evaluated_count = mission_worker._evaluate_mission_opportunities(db, mission)

    assert usable_count == 1
    assert per_run == {source_run.id: 1}
    assert evaluated_count == 1
    assert evaluated_job_ids == [job_records[0].id]
    assert db.scalar(select(func.count()).select_from(CandidateEvaluation).where(
        CandidateEvaluation.candidate_profile_id == profile.id,
    )) == 0




def test_partial_acquisition_is_not_misreported_as_success_and_waits_for_observations(db, monkeypatch):
    _, work_item, rows, status = _run_mission_with_source_outcomes(
        db, monkeypatch, ["partial", "fail"],
    )

    assert status == "WAITING_FOR_CANONICALIZATION"
    partial = next(row for row in rows if row.status == "PARTIAL")
    assert partial.acquired_count == 1
    assert partial.persisted_count == 0
    assert partial.error_code == "PAGE_FETCH_FAILED"
    assert partial.error_details["failed_item_count"] == 1
    assert work_item.status == "PENDING"


def test_restricted_and_unavailable_results_are_not_successful_empty_runs(db, monkeypatch):
    _, work_item, rows, status = _run_mission_with_source_outcomes(
        db, monkeypatch, ["unavailable", "restricted"],
    )

    assert status == "PENDING"
    assert {row.status for row in rows} == {"UNAVAILABLE", "RESTRICTED"}
    assert {row.error_code for row in rows} == {"UPSTREAM_UNAVAILABLE", "SOURCE_RESTRICTED"}
    assert all(row.acquired_count == 0 for row in rows)
    assert all(row.status != "SUCCEEDED_EMPTY" for row in rows)
    assert work_item.status == "PENDING"
