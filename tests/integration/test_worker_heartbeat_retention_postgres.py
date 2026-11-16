import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.application.operations.worker_heartbeat import prune_stale_worker_heartbeats
from app.infrastructure.db.models.worker_heartbeat import WorkerHeartbeat


@pytest.fixture
def connection():
    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    outer = conn.begin()
    try:
        yield conn
    finally:
        outer.rollback()
        conn.close()
        engine.dispose()


def _row(name, seen):
    return WorkerHeartbeat(
        worker_name=name, instance_id=uuid4(), status="STOPPED",
        started_at=seen, last_seen_at=seen, metrics={},
    )


def test_prune_removes_only_rows_older_than_the_window(connection):
    def factory():
        return Session(bind=connection, join_transaction_mode="create_savepoint")

    now = datetime.now(UTC)
    with factory() as db:
        db.add_all([_row("retention_old", now - timedelta(days=8)), _row("retention_new", now - timedelta(days=1))])
        db.commit()
    removed = prune_stale_worker_heartbeats(factory)
    with factory() as db:
        names = set(db.scalars(select(WorkerHeartbeat.worker_name).where(WorkerHeartbeat.worker_name.like("retention_%"))))
    assert removed >= 1
    assert names == {"retention_new"}


def test_prune_rejects_a_zero_day_window():
    with pytest.raises(ValueError):
        prune_stale_worker_heartbeats(lambda: None, older_than_days=0)
