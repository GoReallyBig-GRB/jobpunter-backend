"""DEP-07 failure simulations: the REAL worker host process against real PostgreSQL.

Each scenario starts ``python -m app.workers.worker_host`` as a subprocess, creates real
durable outbox work, injects a failure, and asserts the work is still completed exactly
once (as observed through ``attempt_count`` and final status) or fails visibly.

Events used are WORK_REQUESTED signals for DISCOVERY_MISSION work items: the production
dispatcher acknowledges them without side effects, so no external service is touched.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.orm import sessionmaker

from app.infrastructure.db.models import OutboxEvent, WorkItem

pytestmark = pytest.mark.skipif(
    os.name != "posix" or not os.environ.get("DATABASE_URL"),
    reason="Requires POSIX signals and PostgreSQL via DATABASE_URL",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TAG = "dep07-"


@pytest.fixture()
def engine():
    eng = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    _cleanup(eng)
    yield eng
    _cleanup(eng)
    eng.dispose()


def _cleanup(eng) -> None:
    with sessionmaker(bind=eng)() as db:
        db.execute(delete(OutboxEvent).where(OutboxEvent.event_type == "WORK_REQUESTED",
                                             OutboxEvent.payload["tag"].as_string() == TAG))
        db.execute(delete(WorkItem).where(WorkItem.idempotency_key.like(f"{TAG}%")))
        db.commit()


def _seed(eng, count: int) -> list:
    ids = []
    with sessionmaker(bind=eng)() as db:
        for i in range(count):
            item = WorkItem(work_type="DISCOVERY_MISSION", status="PENDING",
                            idempotency_key=f"{TAG}{uuid4()}")
            db.add(item)
            db.flush()
            event = OutboxEvent(event_type="WORK_REQUESTED", aggregate_type="WorkItem",
                                aggregate_id=item.id, payload={"tag": TAG},
                                available_at=datetime.now(timezone.utc) - timedelta(seconds=1))
            db.add(event)
            db.flush()
            ids.append(event.id)
        db.commit()
    return ids


def _states(eng, ids) -> dict[str, int]:
    with sessionmaker(bind=eng)() as db:
        rows = db.execute(select(OutboxEvent.status, func.count())
                          .where(OutboxEvent.id.in_(ids)).group_by(OutboxEvent.status)).all()
    return {status: n for status, n in rows}


def _attempts(eng, ids) -> list[int]:
    with sessionmaker(bind=eng)() as db:
        return list(db.scalars(select(OutboxEvent.attempt_count).where(OutboxEvent.id.in_(ids))))


def _start_host(*extra: str) -> subprocess.Popen:
    # Log to a file, never a pipe: an outage floods the log and a full, unread pipe would
    # block the host and fake a deadlock.
    log = tempfile.TemporaryFile(mode="w+")
    host = subprocess.Popen(
        [sys.executable, "-m", "app.workers.worker_host", "--poll-seconds", "0.5",
         "--safety-net-seconds", "2", "--outbox-batch-size", "10", *extra],
        cwd=REPO_ROOT, env=os.environ.copy(), start_new_session=True,
        stdout=log, stderr=subprocess.STDOUT, text=True,
    )
    host.log_file = log  # type: ignore[attr-defined]
    return host


def _stop_host(host: subprocess.Popen) -> str:
    if host.poll() is None:
        host.send_signal(signal.SIGTERM)
        try:
            host.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(host.pid, signal.SIGKILL)
            host.wait(timeout=10)
    log = host.log_file  # type: ignore[attr-defined]
    log.seek(0)
    return log.read()


def _wait(predicate, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.3)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


def _all_processed(eng, ids) -> bool:
    return _states(eng, ids) == {"PROCESSED": len(ids)}


def test_two_hosts_at_once_process_every_event_exactly_once(engine):
    ids = _seed(engine, 120)
    a, b = _start_host(), _start_host()
    try:
        _wait(lambda: _all_processed(engine, ids), 120, "both hosts to drain the outbox")
    finally:
        out = _stop_host(a) + _stop_host(b)
    assert _attempts(engine, ids) == [1] * len(ids), "an event was claimed more than once"
    assert "Traceback" not in out


def test_host_killed_with_backlog_then_restarted_loses_nothing(engine):
    ids = _seed(engine, 400)
    first = _start_host("--outbox-batch-size", "5")
    try:
        _wait(lambda: 0 < _states(engine, ids).get("PROCESSED", 0) < len(ids), 60,
              "first host to make partial progress")
    finally:
        os.killpg(first.pid, signal.SIGKILL)  # hard host death, no graceful shutdown
        first.wait(timeout=10)
    partial = _states(engine, ids).get("PROCESSED", 0)
    assert partial < len(ids)
    second = _start_host()
    try:
        _wait(lambda: _all_processed(engine, ids), 180, "restarted host to finish the backlog")
    finally:
        _stop_host(second)
    # A claim made by the killed host's child may be retried after the lease; none are lost.
    assert max(_attempts(engine, ids)) <= 2
    assert _states(engine, ids) == {"PROCESSED": len(ids)}


def test_database_connections_dropped_mid_run_host_survives_and_recovers(engine):
    ids = _seed(engine, 150)
    host = _start_host("--outbox-batch-size", "5")
    dbname = engine.url.database
    try:
        _wait(lambda: _states(engine, ids).get("PROCESSED", 0) > 0, 60, "processing to begin")
        admin_engine = create_engine(os.environ["DATABASE_URL"].rsplit("/", 1)[0] + "/postgres",
                                     isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as admin:
            admin.execute(text(f'ALTER DATABASE "{dbname}" WITH ALLOW_CONNECTIONS false'))
            admin.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                               "WHERE datname = :d"), {"d": dbname})
        try:
            time.sleep(4)  # database unreachable for workers and heartbeat
            assert host.poll() is None, "host exited when the database became unreachable"
        finally:
            with admin_engine.connect() as admin:
                admin.execute(text(f'ALTER DATABASE "{dbname}" WITH ALLOW_CONNECTIONS true'))
            admin_engine.dispose()
        _wait(lambda: _all_processed(engine, ids), 180, "host to recover after the outage")
    finally:
        out = _stop_host(host)
    assert host.returncode == 0
    assert max(_attempts(engine, ids)) <= 2


def test_worker_blocked_by_database_lock_is_killed_at_deadline_and_recovers(engine):
    ids = _seed(engine, 20)
    # Hold ACCESS EXCLUSIVE on outbox_events: the outbox worker's first query blocks (hangs).
    blocker = engine.connect()
    blocker.execute(text("LOCK TABLE outbox_events IN ACCESS EXCLUSIVE MODE"))
    host = _start_host("--worker-timeout-seconds", "3", "--termination-grace-seconds", "2")
    try:
        time.sleep(9)  # at least two deadline cycles
        assert host.poll() is None, "host died instead of killing the hung worker"
    finally:
        blocker.rollback()
        blocker.close()
    try:
        _wait(lambda: _all_processed(engine, ids), 120, "work to complete after the lock cleared")
    finally:
        out = _stop_host(host)
    assert "worker_deadline_exceeded name=outbox" in out
    assert host.returncode == 0
