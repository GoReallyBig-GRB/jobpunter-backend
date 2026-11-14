"""DEP-03/DEP-04: duplicate triggers, long scheduler downtime, and overdue-work detection.

Concurrency tests use committed rows and independent sessions. Detection tests run in the
rolled-back ``db`` fixture and assert deltas, so other data in a shared database is irrelevant.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from app.application.jobs import source_refresh
from app.application.operations.overdue import overdue_snapshot
from app.infrastructure.db.models import OutboxEvent, Source, User, WorkItem
from app.infrastructure.db.models.discovery_schedule import SourceRefreshSchedule
from tests.integration.test_admin_governance_postgres import db  # noqa: F401

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="PostgreSQL integration test requires DATABASE_URL"
)


def _source(session, name):
    return Source(
        name=name, source_type="TEST", status="ACTIVE", permission_review_status="APPROVED",
        permission_review_reference="dep04-test", permission_reviewed_at=datetime.now(UTC),
        configuration={},
    )


@pytest.fixture
def committed_overdue_schedule():
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=5)
    now = datetime.now(UTC)
    with Session(engine) as setup:
        user = User(display_name="dep04 scheduler")
        setup.add(user)
        setup.flush()
        source = _source(setup, f"dep04-{uuid4()}")
        setup.add(source)
        setup.flush()
        from tests.integration.test_source_refresh_postgres import _published_version
        config = _published_version(setup, user, source, 60)
        schedule = SourceRefreshSchedule(
            config_version_id=config.id, source_id=source.id, status="ACTIVE", interval_minutes=60,
            next_run_at=now - timedelta(days=3),  # scheduler was down for three days
            created_by_user_id=user.id, updated_by_user_id=user.id,
        )
        setup.add(schedule)
        setup.commit()
        ids = (user.id, source.id, schedule.id)
    try:
        yield engine, ids
    finally:
        _user_id, source_id, schedule_id = ids
        with Session(engine) as cleanup:
            cleanup.execute(delete(WorkItem).where(WorkItem.resource_id == schedule_id))
            cleanup.execute(delete(SourceRefreshSchedule).where(SourceRefreshSchedule.id == schedule_id))
            cleanup.execute(delete(Source).where(Source.id == source_id))
            # The actor is retained because immutable governance versions reference it.
            cleanup.commit()
        engine.dispose()


def test_long_scheduler_downtime_recovers_with_one_occurrence_not_a_burst(committed_overdue_schedule):
    engine, (_user, _source_id, schedule_id) = committed_overdue_schedule
    now = datetime.now(UTC)
    with Session(engine) as db_session:
        assert source_refresh.enqueue_due_source_refreshes(db_session, now=now) == [schedule_id]
    with Session(engine) as check:
        items = check.scalars(select(WorkItem).where(WorkItem.resource_id == schedule_id)).all()
        assert len(items) == 1 and items[0].status == "PENDING"
        schedule = check.get(SourceRefreshSchedule, schedule_id)
        assert schedule.next_run_at == now + timedelta(minutes=60)  # re-anchored; no 72 catch-up runs
    # A repeated poll straight after recovery is a no-op.
    with Session(engine) as db_session:
        assert source_refresh.enqueue_due_source_refreshes(db_session, now=now) == []


def test_concurrent_scheduler_polls_enqueue_a_due_refresh_exactly_once(committed_overdue_schedule):
    engine, (_user, _source_id, schedule_id) = committed_overdue_schedule
    now = datetime.now(UTC)
    barrier = Barrier(3)

    def poll():
        with Session(engine) as session:
            barrier.wait(timeout=10)
            return source_refresh.enqueue_due_source_refreshes(session, now=now)

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: poll(), range(3)))
    assert sum(len(r) for r in results) == 1
    with Session(engine) as check:
        assert len(check.scalars(select(WorkItem).where(WorkItem.resource_id == schedule_id)).all()) == 1


def test_overdue_snapshot_detects_missed_schedules_and_aged_work(db):  # noqa: F811
    now = datetime.now(UTC)
    before = overdue_snapshot(db, now=now)
    user = User(display_name="dep04 detect")
    db.add(user)
    db.flush()
    overdue_source = _source(db, f"dep04-overdue-{uuid4()}")
    on_time_source = _source(db, f"dep04-ontime-{uuid4()}")
    paused_source = _source(db, f"dep04-paused-{uuid4()}")
    db.add_all([overdue_source, on_time_source, paused_source])
    db.flush()

    def sched(source, next_run, status="ACTIVE"):
        db.add(SourceRefreshSchedule(
            source_id=source.id, status=status, interval_minutes=60, next_run_at=next_run,
            created_by_user_id=user.id, updated_by_user_id=user.id,
        ))

    sched(overdue_source, now - timedelta(hours=2))            # missed: counted
    sched(on_time_source, now - timedelta(seconds=30))         # inside grace: not counted
    sched(paused_source, now - timedelta(hours=2), "PAUSED")   # paused: not counted
    old = now - timedelta(hours=1)
    db.add(WorkItem(work_type="X", status="PENDING", idempotency_key=f"dep04:{uuid4()}",
                    created_at=old, updated_at=old))
    db.add(WorkItem(work_type="X", status="PENDING", idempotency_key=f"dep04:{uuid4()}",
                    created_at=now, updated_at=now))           # fresh: not counted
    db.add(WorkItem(work_type="X", status="PROCESSING", idempotency_key=f"dep04:{uuid4()}",
                    started_at=old))
    db.add(OutboxEvent(event_type="T", aggregate_type="T", payload={}, status="PENDING",
                       available_at=now - timedelta(minutes=30)))
    db.flush()

    after = overdue_snapshot(db, now=now)
    assert after["overdue_source_refresh_schedules"] - before["overdue_source_refresh_schedules"] == 1
    assert after["aged_pending_work_items"] - before["aged_pending_work_items"] == 1
    assert after["stuck_processing_work_items"] - before["stuck_processing_work_items"] == 1
    assert after["oldest_overdue_schedule_seconds"] >= 2 * 3600 - 5
    assert after["oldest_due_outbox_event_seconds"] >= 30 * 60 - 5
    assert set(after) >= {
        "schedule_grace_seconds", "work_age_seconds", "overdue_discovery_schedules",
    }
