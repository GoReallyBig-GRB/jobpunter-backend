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
    CandidateProfile,
    DiscoveryMission,
    DiscoveryMissionSourceRun,
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


def _run_mission_with_source_outcomes(db, monkeypatch, outcomes):
    from app.workers import discovery_mission_worker as mission_worker

    user, profile = _candidate(db)
    mission = create_discovery_mission(
        db,
        candidate_profile_id=profile.id,
        idempotency_key=f"source-outcomes-{uuid4()}",
    )
    work_item = db.scalar(select(WorkItem).where(
        WorkItem.work_type == "DISCOVERY_MISSION",
        WorkItem.resource_id == mission.id,
    ))
    assert work_item is not None
    pairs = _seed_active_sources(db, len(outcomes))
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
            outcome = by_source_id[source_id]
            if outcome == "fail":
                raise RuntimeError("connector unavailable")
            now = datetime.now(timezone.utc)
            run = AcquisitionRun(
                source_id=source_id,
                source_connector_id=connector_id,
                run_key=run_key,
                status="SUCCEEDED",
                started_at=now,
                completed_at=now,
                metrics={"acquired_count": 1, "persisted_count": 1},
            )
            self.db.add(run)
            self.db.flush()
            return SimpleNamespace(
                run_id=run.id,
                status="SUCCEEDED",
                acquired_count=1,
                persisted_count=1,
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
    mission, work_item, rows, status = _run_mission_with_source_outcomes(
        db, monkeypatch, ["fail", "fail"]
    )

    assert status == "PENDING"  # Retry the next configured wave, not a successful empty run.
    assert mission.status == "PENDING"
    assert work_item.status == "PENDING"
    assert len(rows) == 2
    assert all(row.status == "FAILED" for row in rows)
    assert all(row.error_code == "RuntimeError" for row in rows)
    assert all(row.persisted_count == 0 for row in rows)


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


def _settings(db, profile, *, status="ENABLED", next_run_at=None, cadence_minutes=60):
    settings = CandidateDiscoverySettings(
        candidate_profile_id=profile.id,
        status=status,
        cadence_minutes=cadence_minutes,
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
    finally:
        processor.close()

    check = Session(engine)
    try:
        mission = check.get(DiscoveryMission, mission_id)
        assert mission.status == "FAILED"
        assert mission.error_code == "WORK_ITEM_MISSING"
        assert mission.completed_at is not None
    finally:
        check.close()
