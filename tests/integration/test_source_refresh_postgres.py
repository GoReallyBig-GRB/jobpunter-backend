"""E4.1-E4.3/E4.8: scheduled source refresh is durable, idempotent, non-overlapping and candidate-independent."""
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.application.jobs import source_refresh
from app.infrastructure.db.models import AcquisitionRun, DiscoveryMission, DiscoveryMissionSourceRun, Notification, User, WorkItem
from app.infrastructure.db.models.discovery_schedule import SourceRefreshSchedule
from app.infrastructure.db.models.discovery_tool import DiscoveryToolControl
from tests.integration.test_admin_governance_postgres import db  # noqa: F401
from tests.integration.test_discovery_missions_postgres import _seed_active_sources

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)
NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def _scope(db):
    user = User(display_name="refresh creator")
    db.add(user)
    db.flush()
    source, connector, target = _seed_active_sources(db, 1, created_by_user_id=user.id)[0]
    return user, source, connector, target


def _schedule(db, user, source, *, minutes=60, due=True, status="ACTIVE"):
    schedule = SourceRefreshSchedule(
        source_id=source.id, status=status, interval_minutes=minutes,
        next_run_at=NOW - timedelta(minutes=1) if due else NOW + timedelta(hours=5),
        created_by_user_id=user.id, updated_by_user_id=user.id,
    )
    db.add(schedule)
    db.flush()
    return schedule


def _items(db, schedule):
    return db.scalars(select(WorkItem).where(
        WorkItem.work_type == "SOURCE_REFRESH", WorkItem.resource_id == schedule.id,
    ).order_by(WorkItem.created_at)).all()


class _FakeService:
    """Stands in for the registered acquisition service; persists a real run like the real one."""
    calls = []

    def __init__(self, repository, registry=None):
        self.repository = repository

    def run(self, source_id, connector_id, run_key, context):
        _FakeService.calls.append((source_id, run_key, dict(context)))
        run = self.repository.db.merge(AcquisitionRun(
            source_id=source_id, source_connector_id=connector_id, run_key=run_key,
            status="SUCCEEDED", completed_at=datetime.now(timezone.utc),
        ))
        self.repository.db.flush()
        return SimpleNamespace(
            run_id=run.id, status="SUCCEEDED", acquired_count=3, persisted_count=3,
            failed_item_count=0, retry_after_seconds=None, error_category=None,
        )


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeService.calls = []
    yield


def test_due_schedule_enqueues_exactly_one_idempotent_occurrence_and_advances(db):  # noqa: F811
    user, source, _c, _t = _scope(db)
    schedule = _schedule(db, user, source, minutes=60)
    first = source_refresh.enqueue_due_source_refreshes(db, now=NOW)
    assert first == [schedule.id]
    items = _items(db, schedule)
    assert len(items) == 1 and items[0].status == "PENDING"
    assert items[0].idempotency_key.startswith(f"{schedule.id}:")
    db.refresh(schedule)
    assert schedule.next_run_at == NOW + timedelta(minutes=60)
    # Duplicate trigger for the same instant is a no-op.
    assert source_refresh.enqueue_due_source_refreshes(db, now=NOW) == []
    assert len(_items(db, schedule)) == 1


def test_not_due_paused_inactive_source_and_in_flight_schedules_are_not_enqueued(db):  # noqa: F811
    user, source, _c, _t = _scope(db)
    not_due = _schedule(db, user, source, due=False)
    assert source_refresh.enqueue_due_source_refreshes(db, now=NOW) == []
    not_due.next_run_at = NOW - timedelta(minutes=1)
    not_due.status = "PAUSED"
    db.flush()
    assert source_refresh.enqueue_due_source_refreshes(db, now=NOW) == []
    not_due.status = "ACTIVE"
    source.status = "PAUSED"
    db.flush()
    assert source_refresh.enqueue_due_source_refreshes(db, now=NOW) == []
    source.status = "ACTIVE"
    db.flush()
    assert source_refresh.enqueue_due_source_refreshes(db, now=NOW) == [not_due.id]
    # Overlap policy: while the previous occurrence is unfinished no second one is queued.
    not_due.next_run_at = NOW - timedelta(minutes=1)
    db.flush()
    assert source_refresh.enqueue_due_source_refreshes(db, now=NOW + timedelta(minutes=1)) == []
    assert len(_items(db, not_due)) == 1


def test_execution_records_a_schedule_owned_step_without_missions_or_notifications(db, monkeypatch):  # noqa: F811
    user, source, connector, target = _scope(db)
    schedule = _schedule(db, user, source)
    monkeypatch.setattr(source_refresh, "RegisteredAcquisitionService", _FakeService)
    missions_before = db.scalar(select(func.count()).select_from(DiscoveryMission))
    notes_before = db.scalar(select(func.count()).select_from(Notification))
    source_refresh.enqueue_due_source_refreshes(db, now=NOW)
    item = _items(db, schedule)[0]
    assert source_refresh.process_source_refresh(db, item.id) == "COMPLETED"
    step = db.scalar(select(DiscoveryMissionSourceRun).where(DiscoveryMissionSourceRun.schedule_id == schedule.id))
    assert step.mission_id is None and step.status == "SUCCEEDED" and step.acquired_count == 3
    assert step.source_id == source.id and step.source_connector_id == connector.id
    assert _FakeService.calls[0][2] == {"endpoint_url": target.url}  # same target narrowing as missions
    db.refresh(item)
    db.refresh(schedule)
    assert item.status == "COMPLETED" and item.result["outcome"] == "EXECUTED"
    assert schedule.last_run_at is not None
    assert db.scalar(select(func.count()).select_from(DiscoveryMission)) == missions_before
    assert db.scalar(select(func.count()).select_from(Notification)) == notes_before


@pytest.mark.parametrize("site_name", ["indeed", "linkedin"])
def test_scheduled_refresh_executes_registered_jobspy_safely(db, monkeypatch, site_name):  # noqa: F811
    user, source, connector, target = _scope(db)
    connector.connector_type = "JOBSPY"
    connector.configuration = {
        "search_terms": ["product manager"], "location": "Nigeria",
        "site_names": [site_name],
        "site_permissions": {site_name: "TEST-WRITTEN-PERMISSION"},
    }
    target.target_kind = "SEARCH"
    schedule = _schedule(db, user, source)

    class Frame:
        def to_dict(self, *, orient):
            assert orient == "records"
            return [{
                "id": f"{site_name}-scheduled", "title": "Product Manager",
                "job_url": f"https://jobs.example.org/{site_name}-scheduled",
            }]

    package = types.ModuleType("jobspy")
    package.scrape_jobs = lambda **_kwargs: Frame()
    monkeypatch.setitem(sys.modules, "jobspy", package)
    monkeypatch.setattr(
        "app.infrastructure.acquisition.jobspy.install_jobspy_transport", lambda: None
    )
    source_refresh.enqueue_due_source_refreshes(db, now=NOW)
    item = _items(db, schedule)[0]

    assert source_refresh.process_source_refresh(db, item.id) == "COMPLETED"
    step = db.scalar(select(DiscoveryMissionSourceRun).where(
        DiscoveryMissionSourceRun.schedule_id == schedule.id
    ))
    assert step.status == "SUCCEEDED"
    assert step.acquired_count == step.persisted_count == 1


def test_reprocessing_the_same_occurrence_does_not_run_acquisition_again(db, monkeypatch):  # noqa: F811
    user, source, _c, _t = _scope(db)
    schedule = _schedule(db, user, source)
    monkeypatch.setattr(source_refresh, "RegisteredAcquisitionService", _FakeService)
    source_refresh.enqueue_due_source_refreshes(db, now=NOW)
    item = _items(db, schedule)[0]
    source_refresh.process_source_refresh(db, item.id)
    item.status = "PROCESSING"  # duplicate delivery / crash after completion of the step
    db.flush()
    source_refresh.process_source_refresh(db, item.id)
    assert len(_FakeService.calls) == 1
    assert db.scalar(select(func.count()).select_from(DiscoveryMissionSourceRun).where(
        DiscoveryMissionSourceRun.schedule_id == schedule.id)) == 1


def test_policy_skips_apply_to_scheduled_runs_and_nothing_executed_is_labelled(db, monkeypatch):  # noqa: F811
    user, source, connector, target = _scope(db)
    schedule = _schedule(db, user, source)
    db.add(DiscoveryToolControl(tool_key="JSON_API", status="DISABLED", reason="provider incident", updated_by_user_id=user.id))
    db.flush()

    class Forbidden:
        def __init__(self, *_a, **_k):
            raise AssertionError("a disabled tool must not start acquisition")

    monkeypatch.setattr(source_refresh, "RegisteredAcquisitionService", Forbidden)
    source_refresh.enqueue_due_source_refreshes(db, now=NOW)
    item = _items(db, schedule)[0]
    assert source_refresh.process_source_refresh(db, item.id) == "COMPLETED"
    step = db.scalar(select(DiscoveryMissionSourceRun).where(DiscoveryMissionSourceRun.schedule_id == schedule.id))
    assert (step.status, step.error_code) == ("UNAVAILABLE", "DISCOVERY_TOOL_DISABLED")
    db.refresh(item)
    assert item.result["outcome"] == "NOTHING_EXECUTED" and item.result["skipped"] == 1


def test_source_paused_after_enqueue_cancels_the_occurrence_without_steps(db, monkeypatch):  # noqa: F811
    user, source, _c, _t = _scope(db)
    schedule = _schedule(db, user, source)
    monkeypatch.setattr(source_refresh, "RegisteredAcquisitionService", _FakeService)
    source_refresh.enqueue_due_source_refreshes(db, now=NOW)
    item = _items(db, schedule)[0]
    source.status = "PAUSED"
    db.flush()
    assert source_refresh.process_source_refresh(db, item.id) == "CANCELLED"
    assert _FakeService.calls == []
    assert db.scalar(select(func.count()).select_from(DiscoveryMissionSourceRun).where(
        DiscoveryMissionSourceRun.schedule_id == schedule.id)) == 0


def test_worker_claims_pending_occurrences_once_and_recovers_stale_claims(db, monkeypatch):  # noqa: F811
    from contextlib import contextmanager

    from app.workers.source_refresh_worker import SourceRefreshWorker

    user, source, _c, _t = _scope(db)
    schedule = _schedule(db, user, source)
    source_refresh.enqueue_due_source_refreshes(db, now=NOW)
    item = _items(db, schedule)[0]

    @contextmanager
    def session_factory():
        yield db

    worker = SourceRefreshWorker(session_factory=lambda: session_factory(), lease_seconds=30)
    monkeypatch.setattr(source_refresh, "RegisteredAcquisitionService", _FakeService)
    assert worker.claim_batch(5) == [item.id]
    db.refresh(item)
    assert item.status == "PROCESSING" and item.attempt_count >= 1
    assert worker.claim_batch(5) == []  # a fresh claim is not handed out twice
    item.started_at = datetime.now(timezone.utc) - timedelta(seconds=600)
    db.flush()
    assert worker.claim_batch(5) == [item.id]  # an abandoned claim is recovered
    item.status = "PENDING"
    db.flush()
    outcome = worker.run_once(5)
    assert outcome == {"claimed": 1, "completed": 1, "failed": 0}
    db.refresh(item)
    assert item.status == "COMPLETED"


def test_global_concurrency_ceiling_limits_live_claims_and_frees_on_completion(db, monkeypatch):  # noqa: F811
    from contextlib import contextmanager

    from app.workers.source_refresh_worker import SourceRefreshWorker

    items = []
    for _ in range(3):
        user, source, _c, _t = _scope(db)
        schedule = _schedule(db, user, source)
        items.append(schedule)
    source_refresh.enqueue_due_source_refreshes(db, now=NOW)

    @contextmanager
    def session_factory():
        yield db

    worker = SourceRefreshWorker(session_factory=lambda: session_factory(), max_concurrent=2)
    mine = {i.id for s in items for i in _items(db, s)}
    claimed = worker.claim_batch(10)
    assert len(claimed) == 2 and set(claimed) <= mine
    assert worker.claim_batch(10) == []  # ceiling reached: nothing more is handed out
    first = db.get(WorkItem, claimed[0])
    first.status = "COMPLETED"
    db.flush()
    assert len(worker.claim_batch(10)) == 1  # a finished item frees one slot


def test_default_ceiling_comes_from_settings_and_is_validated():
    from app.config.settings import Settings
    from app.workers.source_refresh_worker import SourceRefreshWorker

    assert Settings().source_refresh_max_concurrent == 4
    with pytest.raises(ValueError):
        SourceRefreshWorker(max_concurrent=0)
    with pytest.raises(Exception):
        Settings(source_refresh_max_concurrent=0)


def test_due_schedule_outside_its_window_waits_for_the_window_to_open(db):  # noqa: F811
    user, source, _c, _t = _scope(db)
    schedule = _schedule(db, user, source)
    # NOW is 12:00 UTC; the window is 22:00-02:00 UTC (crosses midnight).
    schedule.window_start_minute, schedule.window_end_minute, schedule.window_timezone = 22 * 60, 2 * 60, "UTC"
    db.flush()
    assert source_refresh.enqueue_due_source_refreshes(db, now=NOW) == []
    assert _items(db, schedule) == []
    db.refresh(schedule)
    assert schedule.next_run_at == datetime(2026, 11, 2, 22, tzinfo=timezone.utc)
    # Once inside the window it enqueues normally.
    inside = datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc)
    assert source_refresh.enqueue_due_source_refreshes(db, now=inside) == [schedule.id]
    assert len(_items(db, schedule)) == 1


def test_window_in_a_local_timezone(db):  # noqa: F811
    user, source, _c, _t = _scope(db)
    schedule = _schedule(db, user, source)
    # 09:00-17:00 Africa/Lagos (UTC+1) == 08:00-16:00 UTC. NOW (12:00 UTC) is inside.
    schedule.window_start_minute, schedule.window_end_minute, schedule.window_timezone = 9 * 60, 17 * 60, "Africa/Lagos"
    db.flush()
    assert source_refresh.enqueue_due_source_refreshes(db, now=NOW) == [schedule.id]


def test_source_refresh_crashed_claims_exhaust_retry_budget(db):  # noqa: F811
    """A terminated worker cannot leave an occurrence eligible forever."""
    from contextlib import contextmanager
    from app.workers.source_refresh_worker import MAX_ATTEMPTS, SourceRefreshWorker

    user, source, _connector, _target = _scope(db)
    schedule = _schedule(db, user, source)
    source_refresh.enqueue_due_source_refreshes(db, now=NOW)
    item = _items(db, schedule)[0]

    @contextmanager
    def session_factory():
        yield db

    worker = SourceRefreshWorker(
        session_factory=lambda: session_factory(), lease_seconds=30,
    )
    for attempt in range(1, MAX_ATTEMPTS + 1):
        assert worker.claim_batch(1) == [item.id]
        db.refresh(item)
        assert item.status == "PROCESSING"
        assert item.attempt_count == attempt
        # Simulate process loss and an elapsed claim lease, with no exception handler.
        item.started_at = datetime.now(timezone.utc) - timedelta(seconds=600)
        db.flush()

    assert worker.claim_batch(1) == []
    db.refresh(item)
    assert item.status == "FAILED"
    assert item.attempt_count == MAX_ATTEMPTS
    assert item.error_code == "WORKER_LEASE_RETRIES_EXHAUSTED"
    assert item.error_details == {"retryable": False, "reason": "STALE_CLAIM"}
    assert item.completed_at is not None
    assert worker._claim_tokens == {}  # exhausted rows were not returned as claims
    assert worker.claim_batch(1) == []



def _interrupted_scope(db, schedule, source, connector, target, item):
    """Create the durable RUNNING step a killed process leaves behind."""
    run_key = (
        f"schedule:{item.idempotency_key}:connector:{connector.id}:target:{target.id}"
    )
    step = DiscoveryMissionSourceRun(
        mission_id=None, schedule_id=schedule.id, source_id=source.id,
        source_connector_id=connector.id, wave_key="scheduled", attempt_number=1,
        run_key=run_key, status="RUNNING", started_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    db.add(step)
    db.commit()
    return step, run_key


def test_interrupted_scope_without_acquisition_resumes_existing_step(db, monkeypatch):  # noqa: F811
    user, source, connector, target = _scope(db)
    schedule = _schedule(db, user, source)
    source_refresh.enqueue_due_source_refreshes(db, now=NOW)
    item = _items(db, schedule)[0]
    step, _run_key = _interrupted_scope(db, schedule, source, connector, target, item)
    monkeypatch.setattr(source_refresh, "RegisteredAcquisitionService", _FakeService)

    assert source_refresh.process_source_refresh(db, item.id) == "COMPLETED"
    db.refresh(step)
    assert step.status == "SUCCEEDED"
    assert len(_FakeService.calls) == 1
    assert db.scalar(select(func.count()).select_from(DiscoveryMissionSourceRun).where(
        DiscoveryMissionSourceRun.run_key == step.run_key
    )) == 1


def test_interrupted_scope_reconciles_terminal_acquisition_without_refetch(db, monkeypatch):  # noqa: F811
    user, source, connector, target = _scope(db)
    schedule = _schedule(db, user, source)
    source_refresh.enqueue_due_source_refreshes(db, now=NOW)
    item = _items(db, schedule)[0]
    step, run_key = _interrupted_scope(db, schedule, source, connector, target, item)
    db.add(AcquisitionRun(
        source_id=source.id, source_connector_id=connector.id, run_key=run_key,
        status="SUCCEEDED", completed_at=datetime.now(timezone.utc),
        metrics={"acquired_count": 4, "persisted_count": 3},
    ))
    db.commit()

    class MustNotReacquire:
        def __init__(self, *_a, **_kw):
            raise AssertionError("terminal acquisition must be reconciled, never re-fetched")

    monkeypatch.setattr(source_refresh, "RegisteredAcquisitionService", MustNotReacquire)
    assert source_refresh.process_source_refresh(db, item.id) == "COMPLETED"
    db.refresh(step)
    db.refresh(item)
    assert (step.status, step.acquired_count, step.persisted_count) == ("SUCCEEDED", 4, 3)
    assert step.error_details["recovered_from_acquisition_run"] is True
    assert item.result["failed"] == 0


def test_interrupted_scope_waits_for_active_acquisition_without_false_completion(db, monkeypatch):  # noqa: F811
    user, source, connector, target = _scope(db)
    schedule = _schedule(db, user, source)
    source_refresh.enqueue_due_source_refreshes(db, now=NOW)
    item = _items(db, schedule)[0]
    step, run_key = _interrupted_scope(db, schedule, source, connector, target, item)
    lease_expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
    db.add(AcquisitionRun(
        source_id=source.id, source_connector_id=connector.id, run_key=run_key,
        status="RUNNING", lease_expires_at=lease_expiry,
        metrics={},
    ))
    db.commit()

    class MustNotReacquire:
        def __init__(self, *_a, **_kw):
            raise AssertionError("live acquisition must not be re-fetched")

    monkeypatch.setattr(source_refresh, "RegisteredAcquisitionService", MustNotReacquire)
    assert source_refresh.process_source_refresh(db, item.id) == "DEFERRED"
    db.refresh(item)
    db.refresh(step)
    assert item.status == "PENDING"
    assert item.completed_at is None
    assert item.started_at is None
    assert item.result["outcome"] == "WAITING_FOR_ACQUISITION"
    assert datetime.fromisoformat(item.result["not_before"]) > datetime.now(timezone.utc)
    assert step.status == "RUNNING"
    assert schedule.last_run_at is None
    from contextlib import contextmanager
    from app.workers.source_refresh_worker import SourceRefreshWorker

    @contextmanager
    def session_factory():
        yield db

    worker = SourceRefreshWorker(session_factory=lambda: session_factory(), lease_seconds=30)
    assert worker.claim_batch(1) == []
    item.result = {"outcome": "WAITING_FOR_ACQUISITION", "not_before": (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()}
    db.commit()
    assert worker.claim_batch(1) == [item.id]
    db.refresh(item)
    assert item.attempt_count == 0


def test_waiting_source_refresh_at_retry_limit_can_reconcile(db):  # noqa: F811
    from contextlib import contextmanager
    from app.workers.source_refresh_worker import MAX_ATTEMPTS, SourceRefreshWorker

    user, source, connector, target = _scope(db)
    schedule = _schedule(db, user, source)
    source_refresh.enqueue_due_source_refreshes(db, now=NOW)
    item = _items(db, schedule)[0]
    step, run_key = _interrupted_scope(db, schedule, source, connector, target, item)
    db.add(AcquisitionRun(
        source_id=source.id, source_connector_id=connector.id, run_key=run_key,
        status="SUCCEEDED", completed_at=datetime.now(timezone.utc),
        metrics={"acquired_count": 1, "persisted_count": 1},
    ))
    item.attempt_count = MAX_ATTEMPTS
    item.result = {
        "outcome": "WAITING_FOR_ACQUISITION",
        "not_before": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    }
    db.commit()

    @contextmanager
    def session_factory():
        yield db

    worker = SourceRefreshWorker(session_factory=lambda: session_factory(), lease_seconds=30)
    assert worker.claim_batch(1) == [item.id]
    db.refresh(item)
    assert item.attempt_count == MAX_ATTEMPTS
    assert item.status == "PROCESSING"
    assert source_refresh.process_source_refresh(
        db, item.id, execution_id=worker._claim_tokens[item.id]
    ) == "COMPLETED"
    db.refresh(step)
    assert step.status == "SUCCEEDED"


def test_expired_acquisition_recovers_as_failed_not_success(db, monkeypatch):  # noqa: F811
    user, source, connector, target = _scope(db)
    schedule = _schedule(db, user, source)
    source_refresh.enqueue_due_source_refreshes(db, now=NOW)
    item = _items(db, schedule)[0]
    step, run_key = _interrupted_scope(db, schedule, source, connector, target, item)
    acquisition = AcquisitionRun(
        source_id=source.id, source_connector_id=connector.id, run_key=run_key,
        status="RUNNING", lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        metrics={},
    )
    db.add(acquisition)
    db.commit()

    class MustNotReacquire:
        def __init__(self, *_a, **_kw):
            raise AssertionError("expired acquisition must reconcile its recorded failure")

    monkeypatch.setattr(source_refresh, "RegisteredAcquisitionService", MustNotReacquire)
    assert source_refresh.process_source_refresh(db, item.id) == "COMPLETED"
    db.refresh(acquisition)
    db.refresh(step)
    db.refresh(item)
    assert acquisition.status == "FAILED"
    assert step.status == "FAILED"
    assert item.result["failed"] == 1
    assert step.acquisition_run_id == acquisition.id


def test_stale_refresh_execution_token_cannot_finalize_new_claim(db, monkeypatch):  # noqa: F811
    from contextlib import contextmanager
    from app.workers.source_refresh_worker import SourceRefreshWorker

    user, source, connector, target = _scope(db)
    schedule = _schedule(db, user, source)
    source_refresh.enqueue_due_source_refreshes(db, now=NOW)
    item = _items(db, schedule)[0]

    @contextmanager
    def session_factory():
        yield db

    worker = SourceRefreshWorker(session_factory=lambda: session_factory(), lease_seconds=30)
    assert worker.claim_batch(1) == [item.id]
    previous_execution_id = worker._claim_tokens[item.id]
    assert previous_execution_id == item.execution_id
    item.execution_id = uuid4()  # another worker has taken ownership
    db.commit()

    monkeypatch.setattr(source_refresh, "RegisteredAcquisitionService", _FakeService)
    assert source_refresh.process_source_refresh(
        db, item.id, execution_id=previous_execution_id
    ) == "STALE_CLAIM"
    db.refresh(item)
    assert item.status == "PROCESSING"
    assert item.execution_id != previous_execution_id
    assert _FakeService.calls == []
    assert db.scalar(select(func.count()).select_from(DiscoveryMissionSourceRun).where(
        DiscoveryMissionSourceRun.schedule_id == schedule.id
    )) == 0


def test_failed_old_worker_does_not_reset_new_owner(db, monkeypatch):  # noqa: F811
    from contextlib import contextmanager
    from app.workers import source_refresh_worker as module
    from app.workers.source_refresh_worker import SourceRefreshWorker

    user, source, connector, target = _scope(db)
    schedule = _schedule(db, user, source)
    source_refresh.enqueue_due_source_refreshes(db, now=NOW)
    item = _items(db, schedule)[0]

    @contextmanager
    def session_factory():
        yield db

    def lose_ownership(session, item_id, *, execution_id):
        row = session.get(WorkItem, item_id)
        row.execution_id = uuid4()
        session.commit()
        raise RuntimeError("old worker has failed")

    monkeypatch.setattr(module, "process_source_refresh", lose_ownership)
    worker = SourceRefreshWorker(session_factory=lambda: session_factory(), lease_seconds=30)
    assert worker.run_once(1) == {"claimed": 1, "completed": 0, "failed": 1}
    db.refresh(item)
    assert item.status == "PROCESSING"
    assert item.execution_id is not None
    assert item.attempt_count == 1
