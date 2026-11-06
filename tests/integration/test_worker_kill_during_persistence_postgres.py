"""Forced process death (SIGKILL) during acquisition persistence on real PostgreSQL.

Proves: an uncommitted raw record + outbox event vanish together with the dead worker, row
locks are released, a committed pair stays atomic, and lease recovery fails the orphaned run.
No browser, external provider, or production credential is used.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.orm import sessionmaker

from app.infrastructure.db.models import (
    AcquisitionRun,
    OutboxEvent,
    RawSourceRecord,
    Source,
    SourceConnector,
)
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository

pytestmark = pytest.mark.skipif(
    os.name != "posix" or not os.environ.get("DATABASE_URL"),
    reason="Requires POSIX SIGKILL and PostgreSQL via DATABASE_URL",
)

REPO_ROOT = Path(__file__).resolve().parents[2]

WORKER = textwrap.dedent(
    """
    import os, sys, time
    from datetime import datetime, timezone
    from uuid import UUID
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
    from app.ports.acquisition import RawAcquisition

    run_id, source_id, mode = UUID(sys.argv[1]), UUID(sys.argv[2]), sys.argv[3]
    engine = create_engine(os.environ["DATABASE_URL"])
    with sessionmaker(bind=engine)() as db:
        repo = SqlAlchemyAcquisitionRepository(db)
        item = RawAcquisition(
            source_id=source_id, external_record_id="kill-" + sys.argv[1],
            source_url="https://example.test/kill", raw_content={"title": "kill test"},
            content_hash="kill-" + sys.argv[1], observed_at=datetime.now(timezone.utc),
        )
        assert repo.persist_raw_if_new(run_id, item) is True
        if mode == "commit_then_hang":
            db.commit()
        print("READY", flush=True)
        time.sleep(600)
    """
)


@pytest.fixture
def run_fixture():
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=4)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    with sessions.begin() as db:
        source = Source(
            name=f"dep025-kill-{uuid4()}", source_type="TEST", status="ACTIVE",
            permission_review_status="APPROVED", permission_review_reference="dep025-test",
            permission_reviewed_at=now,
            configuration={"permission_review": {
                "access_method": "PUBLIC_JOB_BOARD_API",
                "terms_reference": "https://example.test/source-terms",
                "rate_limit_policy": "Respect documented rate limits",
                "rate_limit_reference": "https://example.test/rate-limits",
            }},
        )
        db.add(source)
        db.flush()
        connector = SourceConnector(
            source_id=source.id, connector_type="TEST", connector_version="1", status="ACTIVE",
        )
        db.add(connector)
        db.flush()
        run = AcquisitionRun(
            source_id=source.id, source_connector_id=connector.id,
            run_key=f"dep025-kill-{uuid4()}", status="RUNNING", started_at=now,
            lease_expires_at=now + timedelta(hours=1), metrics={},
        )
        db.add(run)
        db.flush()
        ids = (source.id, connector.id, run.id)
    yield sessions, *ids
    source_id, connector_id, run_id = ids
    with sessions.begin() as db:
        db.execute(delete(OutboxEvent).where(
            OutboxEvent.payload["acquisition_run_id"].as_string() == str(run_id)))
        db.execute(delete(RawSourceRecord).where(RawSourceRecord.acquisition_run_id == run_id))
        db.execute(delete(AcquisitionRun).where(AcquisitionRun.id == run_id))
        db.execute(delete(SourceConnector).where(SourceConnector.id == connector_id))
        db.execute(delete(Source).where(Source.id == source_id))
    engine.dispose()


def _kill_worker_mid_persistence(source_id, run_id, mode):
    process = subprocess.Popen(
        [sys.executable, "-c", WORKER, str(run_id), str(source_id), mode],
        stdout=subprocess.PIPE, text=True, cwd=REPO_ROOT,
    )
    try:
        assert process.stdout.readline().strip() == "READY"
        process.send_signal(signal.SIGKILL)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=15)
    assert process.returncode == -signal.SIGKILL


def _counts(db, run_id):
    raw = db.scalar(select(func.count()).select_from(RawSourceRecord).where(
        RawSourceRecord.acquisition_run_id == run_id))
    outbox = db.scalar(select(func.count()).select_from(OutboxEvent).where(
        OutboxEvent.payload["acquisition_run_id"].as_string() == str(run_id)))
    return raw, outbox


def _recover_after_lease_expiry(sessions, run_id):
    with sessions.begin() as db:
        db.get(AcquisitionRun, run_id).lease_expires_at = datetime.now(timezone.utc) - timedelta(
            seconds=1)
    with sessions() as db:
        assert SqlAlchemyAcquisitionRepository(db).recover_stale_runs() >= 1
    with sessions() as db:
        run = db.get(AcquisitionRun, run_id)
        assert run.status == "FAILED"
        assert run.error_summary == "worker_lost:lease_expired"


def test_sigkill_before_commit_rolls_back_raw_and_outbox_together(run_fixture):
    sessions, source_id, _connector_id, run_id = run_fixture
    _kill_worker_mid_persistence(source_id, run_id, "hang_uncommitted")
    with sessions() as db:
        assert _counts(db, run_id) == (0, 0)
        # The dead worker's row lock on the run must be gone: NOWAIT would raise otherwise.
        db.execute(text("select 1 from acquisition_runs where id = :i for update nowait"),
                   {"i": run_id})
    _recover_after_lease_expiry(sessions, run_id)


def test_sigkill_after_commit_before_complete_keeps_raw_and_outbox_atomic(run_fixture):
    sessions, source_id, _connector_id, run_id = run_fixture
    _kill_worker_mid_persistence(source_id, run_id, "commit_then_hang")
    with sessions() as db:
        assert _counts(db, run_id) == (1, 1)
        assert db.get(AcquisitionRun, run_id).status == "RUNNING"
    _recover_after_lease_expiry(sessions, run_id)
    with sessions() as db:
        # Recovery fails the run but never deletes already-committed evidence.
        assert _counts(db, run_id) == (1, 1)
