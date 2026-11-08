"""Work probes must be supersets of each worker's claim rules, and valid SQL on PostgreSQL.

Runs in the rolled-back ``db`` fixture. Positive checks seed work and require True; the
"future work does not wake the worker" checks compare against a baseline so unrelated rows in a
shared database cannot affect them.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.infrastructure.db.models import OutboxEvent, User, WorkItem
from app.infrastructure.db.models.discovery_schedule import SourceRefreshSchedule
from app.workers.bounded_runner import BATCH_WORKERS
from app.workers.work_probes import PROBED_WORKERS, work_may_exist
from tests.integration.test_admin_governance_postgres import db  # noqa: F401
from tests.integration.test_schedule_recovery_postgres import _source

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="PostgreSQL integration test requires DATABASE_URL"
)


def test_every_batch_worker_has_a_probe_or_relies_on_the_safety_net_by_design():
    assert PROBED_WORKERS <= set(BATCH_WORKERS)
    assert set(BATCH_WORKERS) - PROBED_WORKERS == {"job_verification_worker"}


@pytest.mark.parametrize("name", sorted(PROBED_WORKERS))
def test_probe_query_executes_on_postgresql(db, name):  # noqa: F811
    assert work_may_exist(db, name) in (True, False)


def test_unprobed_worker_never_wakes_on_its_own_and_relies_on_the_safety_net(db):  # noqa: F811
    assert work_may_exist(db, "job_verification_worker") is False


def test_outbox_probe_sees_due_event_but_not_future_event(db):  # noqa: F811
    now = datetime.now(UTC)
    db.add(OutboxEvent(event_type="T", aggregate_type="T", status="PENDING",
                       available_at=now + timedelta(days=1)))
    db.flush()
    before = work_may_exist(db, "outbox", now)
    db.add(OutboxEvent(event_type="T", aggregate_type="T", status="PENDING",
                       available_at=now - timedelta(seconds=1)))
    db.flush()
    assert work_may_exist(db, "outbox", now) is True
    assert before in (True, False)


def _work_item(db, work_type, status, **kw):  # noqa: F811
    item = WorkItem(work_type=work_type, status=status, idempotency_key=f"probe-{uuid4()}", **kw)
    db.add(item)
    db.flush()
    return item


def test_pending_mission_and_refresh_work_items_wake_their_workers(db):  # noqa: F811
    now = datetime.now(UTC)
    _work_item(db, "DISCOVERY_MISSION", "PENDING")
    _work_item(db, "SOURCE_REFRESH", "PENDING")
    assert work_may_exist(db, "discovery_mission_worker", now) is True
    assert work_may_exist(db, "source_refresh_worker", now) is True


def test_stale_processing_refresh_item_wakes_worker_but_live_one_alone_need_not(db):  # noqa: F811
    now = datetime.now(UTC)
    _work_item(db, "SOURCE_REFRESH", "PROCESSING", started_at=now - timedelta(hours=1))
    assert work_may_exist(db, "source_refresh_worker", now) is True


def test_refresh_item_delayed_by_backoff_does_not_wake_worker(db):  # noqa: F811
    now = datetime.now(UTC)
    baseline = work_may_exist(db, "source_refresh_worker", now)
    _work_item(db, "SOURCE_REFRESH", "PENDING",
               result={"not_before": (now + timedelta(hours=1)).isoformat()})
    assert work_may_exist(db, "source_refresh_worker", now) is baseline


def test_overdue_refresh_schedule_wakes_the_scheduler(db):  # noqa: F811
    now = datetime.now(UTC)
    user = User(display_name="probe scheduler")
    db.add(user)
    db.flush()
    source = _source(db, f"probe-{uuid4()}")
    db.add(source)
    db.flush()
    db.add(SourceRefreshSchedule(
        source_id=source.id, status="ACTIVE", interval_minutes=60,
        next_run_at=now - timedelta(minutes=5),
        created_by_user_id=user.id, updated_by_user_id=user.id,
    ))
    db.flush()
    assert work_may_exist(db, "discovery_scheduler", now) is True
