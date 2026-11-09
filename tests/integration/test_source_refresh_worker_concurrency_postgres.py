"""DEP-02.5: independent-session claim ownership and acquisition recovery.

Tests intentionally use committed PostgreSQL rows, not the transaction-wrapped db
fixture, because independent worker sessions must see and lock the same records.
No browser, external provider, or production credential is used.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import sessionmaker

from app.application.jobs.source_refresh import process_source_refresh
from app.infrastructure.db.models import (
    AcquisitionRun,
    OutboxEvent,
    RawSourceRecord,
    Source,
    SourceConnector,
    WorkItem,
)
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
from app.ports.acquisition import RawAcquisition
from app.workers.source_refresh_worker import SourceRefreshWorker

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="Requires PostgreSQL via DATABASE_URL"
)


def _sessions():
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=4)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _new_refresh_item(sessions):
    item = WorkItem(
        work_type="SOURCE_REFRESH", resource_type="SourceRefreshSchedule",
        status="PENDING", idempotency_key=f"dep025:{uuid4()}",
    )
    with sessions.begin() as db:
        db.add(item)
        db.flush()
    return item.id


def _cleanup_items(sessions, ids):
    with sessions.begin() as db:
        db.execute(delete(WorkItem).where(WorkItem.id.in_(ids)))


def test_two_independent_workers_cannot_claim_same_refresh():
    engine, sessions = _sessions()
    item_id = None
    try:
        item_id = _new_refresh_item(sessions)
        workers = [
            SourceRefreshWorker(
                session_factory=sessions, lease_seconds=30, max_concurrent=1
            ) for _ in range(2)
        ]
        gate = Barrier(2)

        def concurrently_claim(worker):
            gate.wait(timeout=15)
            return worker.claim_batch(1)

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(concurrently_claim, workers))
        assert sum(len(ids) for ids in claims) == 1
        assert [item_id] in claims
        assert [] in claims
        with sessions() as db:
            item = db.get(WorkItem, item_id)
            assert item.status == "PROCESSING"
            assert item.attempt_count == 1
            owner = next(worker for worker, ids in zip(workers, claims) if ids)
            assert item.execution_id == owner._claim_tokens[item_id]
    finally:
        if item_id is not None:
            _cleanup_items(sessions, [item_id])
        engine.dispose()


def test_expired_owner_cannot_cancel_refresh_reclaimed_by_another_worker():
    engine, sessions = _sessions()
    item_id = None
    try:
        item_id = _new_refresh_item(sessions)
        first = SourceRefreshWorker(
            session_factory=sessions, lease_seconds=30, max_concurrent=1
        )
        second = SourceRefreshWorker(
            session_factory=sessions, lease_seconds=30, max_concurrent=1
        )
        assert first.claim_batch(1) == [item_id]
        first_token = first._claim_tokens[item_id]
        with sessions.begin() as db:
            item = db.get(WorkItem, item_id)
            item.started_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        assert second.claim_batch(1) == [item_id]
        second_token = second._claim_tokens[item_id]
        assert first_token != second_token

        with sessions() as db:
            # Without a token check, this scope-less item would be cancelled.
            assert process_source_refresh(
                db, item_id, execution_id=first_token
            ) == "STALE_CLAIM"
        with sessions() as db:
            item = db.get(WorkItem, item_id)
            assert item.status == "PROCESSING"
            assert item.execution_id == second_token
            assert item.completed_at is None
            assert item.attempt_count == 2
    finally:
        if item_id is not None:
            _cleanup_items(sessions, [item_id])
        engine.dispose()


def test_late_completion_from_separate_session_cannot_undo_lease_recovery():
    engine, sessions = _sessions()
    source_id = connector_id = run_id = None
    now = datetime.now(timezone.utc)
    try:
        with sessions.begin() as db:
            source = Source(
                name=f"dep025-lost-worker-{uuid4()}", source_type="TEST",
                status="ACTIVE", permission_review_status="APPROVED",
                permission_review_reference="dep025-test-review",
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
                source_id=source.id, connector_type="TEST",
                connector_version="1", status="ACTIVE",
            )
            db.add(connector)
            db.flush()
            run = AcquisitionRun(
                source_id=source.id, source_connector_id=connector.id,
                run_key=f"dep025-lost-{uuid4()}", status="RUNNING",
                started_at=now - timedelta(hours=2),
                lease_expires_at=now - timedelta(minutes=1), metrics={},
            )
            db.add(run)
            db.flush()
            source_id, connector_id, run_id = source.id, connector.id, run.id

        started, release = Event(), Event()

        def late_completion():
            with sessions() as db:
                started.set()
                assert release.wait(timeout=15)
                try:
                    SqlAlchemyAcquisitionRepository(db).complete_run(
                        run_id, acquired_count=1, persisted_count=1
                    )
                except RuntimeError as exc:
                    return str(exc)
                return "INCORRECTLY_COMPLETED"

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(late_completion)
            assert started.wait(timeout=15)
            with sessions() as db:
                assert SqlAlchemyAcquisitionRepository(db).recover_stale_runs(
                    now=now
                ) >= 1
            release.set()
            assert future.result(timeout=15) == "Acquisition run is no longer active"

        with sessions() as db:
            run = db.get(AcquisitionRun, run_id)
            assert run.status == "FAILED"
            assert run.error_summary == "worker_lost:lease_expired"
            assert run.completed_at is not None
    finally:
        if source_id is not None:
            with sessions.begin() as db:
                if run_id is not None:
                    db.execute(delete(AcquisitionRun).where(AcquisitionRun.id == run_id))
                if connector_id is not None:
                    db.execute(delete(SourceConnector).where(SourceConnector.id == connector_id))
                db.execute(delete(Source).where(Source.id == source_id))
        engine.dispose()


def test_expired_or_recovered_owner_cannot_persist_raw_or_outbox():
    engine, sessions = _sessions()
    source_id = connector_id = run_id = None
    now = datetime.now(timezone.utc)
    try:
        with sessions.begin() as db:
            source = Source(
                name=f"dep025-stale-write-{uuid4()}", source_type="TEST",
                status="ACTIVE", permission_review_status="APPROVED",
                permission_review_reference="dep025-test-review",
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
                source_id=source.id, connector_type="TEST",
                connector_version="1", status="ACTIVE",
            )
            db.add(connector)
            db.flush()
            run = AcquisitionRun(
                source_id=source.id, source_connector_id=connector.id,
                run_key=f"dep025-stale-write-{uuid4()}", status="RUNNING",
                started_at=now - timedelta(hours=2),
                lease_expires_at=now - timedelta(minutes=1), metrics={},
            )
            db.add(run)
            db.flush()
            source_id, connector_id, run_id = source.id, connector.id, run.id

        stale_item = RawAcquisition(
            source_id=source_id,
            external_record_id=f"stale-{uuid4()}",
            source_url="https://example.test/stale-owner",
            raw_content={"title": "Must not persist"},
            content_hash=f"stale-{uuid4()}",
            observed_at=now,
        )
        with sessions() as db:
            repository = SqlAlchemyAcquisitionRepository(db)
            with pytest.raises(RuntimeError, match="no longer active"):
                repository.persist_raw_if_new(run_id, stale_item)
            # Lease expiry itself fences the write, even before a recovery pass.
            db.commit()

        with sessions() as db:
            assert SqlAlchemyAcquisitionRepository(db).recover_stale_runs(now=now) >= 1

        with sessions() as db:
            repository = SqlAlchemyAcquisitionRepository(db)
            with pytest.raises(RuntimeError, match="no longer active"):
                repository.persist_raw_if_new(run_id, stale_item)
            # Recovery status also fences the write. Callers must not need a later
            # complete_run failure to roll raw records and outbox events back.
            db.commit()

        with sessions() as db:
            assert db.scalar(
                select(func.count()).select_from(RawSourceRecord).where(
                    RawSourceRecord.acquisition_run_id == run_id
                )
            ) == 0
            assert db.scalar(
                select(func.count()).select_from(OutboxEvent).where(
                    OutboxEvent.payload["acquisition_run_id"].as_string() == str(run_id)
                )
            ) == 0
    finally:
        if source_id is not None:
            with sessions.begin() as db:
                if run_id is not None:
                    db.execute(delete(OutboxEvent).where(
                        OutboxEvent.payload["acquisition_run_id"].as_string() == str(run_id)
                    ))
                    db.execute(delete(RawSourceRecord).where(
                        RawSourceRecord.acquisition_run_id == run_id
                    ))
                    db.execute(delete(AcquisitionRun).where(AcquisitionRun.id == run_id))
                if connector_id is not None:
                    db.execute(delete(SourceConnector).where(SourceConnector.id == connector_id))
                db.execute(delete(Source).where(Source.id == source_id))
        engine.dispose()
