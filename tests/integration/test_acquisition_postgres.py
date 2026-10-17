import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Lock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.application.jobs.service import AcquisitionService
from app.infrastructure.db.models import AcquisitionRun, JobDiscoveryEvent, OutboxEvent, RawSourceRecord, Source, SourceConnector
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
from app.ports.acquisition import RawAcquisition

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


class BrokenConnector:
    def acquire(self, **_kwargs):
        raise RuntimeError("private connector detail must not be persisted")


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


def test_postgres_connector_failure_persists_failed_run_without_sensitive_error(db):
    source = Source(
        name=f"failure source {uuid4()}",
        source_type="TEST",
        status="ACTIVE",
        permission_review_status="APPROVED",
        permission_review_reference="test-policy-review",
        permission_reviewed_at=datetime.now(timezone.utc),
    )
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id,
        connector_type="TEST",
        connector_version="1",
        status="ACTIVE",
    )
    db.add(connector)
    db.flush()

    service = AcquisitionService(
        SqlAlchemyAcquisitionRepository(db),
        BrokenConnector(),
    )
    run_key = f"failure-{uuid4()}"

    with pytest.raises(RuntimeError, match="private connector detail"):
        service.run(source.id, connector.id, run_key, {})

    run = db.scalar(select(AcquisitionRun).where(
        AcquisitionRun.source_id == source.id,
        AcquisitionRun.run_key == run_key,
    ))
    assert run is not None
    assert run.status == "FAILED"
    assert run.completed_at is not None
    assert run.error_summary == "connector_error:RuntimeError"
    assert "private connector detail" not in run.error_summary
    assert run.metrics == {"acquired_count": 0, "persisted_count": 0}
    assert source.operational_state == "FAILED"
    assert source.last_error_category == "CONNECTOR_ERROR"



def test_postgres_recovery_marks_only_expired_acquisition_leases_failed(db):
    now = datetime.now(timezone.utc)
    source = Source(
        name=f"lease recovery source {uuid4()}",
        source_type="TEST",
        status="ACTIVE",
        permission_review_status="APPROVED",
        permission_review_reference="test-policy-review",
        permission_reviewed_at=datetime.now(timezone.utc),
    )
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id,
        connector_type="TEST",
        connector_version="1",
        status="ACTIVE",
    )
    db.add(connector)
    db.flush()
    stale = AcquisitionRun(
        source_id=source.id,
        source_connector_id=connector.id,
        run_key=f"stale-{uuid4()}",
        status="RUNNING",
        started_at=now - timedelta(hours=2),
        lease_expires_at=now - timedelta(minutes=1),
        completed_at=None,
        metrics={"acquired_count": 0, "persisted_count": 0},
    )
    fresh = AcquisitionRun(
        source_id=source.id,
        source_connector_id=connector.id,
        run_key=f"fresh-{uuid4()}",
        status="RUNNING",
        started_at=now,
        lease_expires_at=now + timedelta(minutes=10),
        completed_at=None,
        metrics={},
    )
    db.add_all([stale, fresh])
    db.commit()

    recovered = SqlAlchemyAcquisitionRepository(db).recover_stale_runs(now=now, limit=10)

    assert recovered == 1
    db.refresh(stale)
    db.refresh(fresh)
    db.refresh(source)
    assert stale.status == "FAILED"
    assert stale.completed_at == now
    assert stale.lease_expires_at is None
    assert stale.error_summary == "worker_lost:lease_expired"
    assert fresh.status == "RUNNING"
    assert fresh.lease_expires_at is not None
    assert source.operational_state == "FAILED"
    assert source.last_error_category == "WORKER_LOST"


def test_postgres_concurrent_stale_run_recovery_skips_locked_run_and_recovers_it_later():
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=4)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    source_id = connector_id = None
    run_ids = []
    now = datetime.now(timezone.utc)
    try:
        with SessionLocal() as seed_db:
            source = Source(
                name=f"concurrent lease recovery source {uuid4()}",
                source_type="TEST",
                status="ACTIVE",
                permission_review_status="APPROVED",
                permission_review_reference="test-policy-review",
                permission_reviewed_at=now,
            )
            seed_db.add(source)
            seed_db.flush()
            connector = SourceConnector(
                source_id=source.id,
                connector_type="TEST",
                connector_version="1",
                status="ACTIVE",
            )
            seed_db.add(connector)
            seed_db.flush()
            stale_first = AcquisitionRun(
                source_id=source.id,
                source_connector_id=connector.id,
                run_key=f"stale-first-{uuid4()}",
                status="RUNNING",
                started_at=now - timedelta(hours=2),
                lease_expires_at=now - timedelta(minutes=2),
                completed_at=None,
                metrics={},
            )
            stale_second = AcquisitionRun(
                source_id=source.id,
                source_connector_id=connector.id,
                run_key=f"stale-second-{uuid4()}",
                status="RUNNING",
                started_at=now - timedelta(hours=1),
                lease_expires_at=now - timedelta(minutes=1),
                completed_at=None,
                metrics={},
            )
            seed_db.add_all([stale_first, stale_second])
            seed_db.commit()
            source_id, connector_id = source.id, connector.id
            run_ids = [stale_first.id, stale_second.id]

        with SessionLocal() as locking_session:
            locked_run = locking_session.scalar(
                select(AcquisitionRun)
                .where(AcquisitionRun.id == run_ids[0])
                .with_for_update()
            )
            assert locked_run is not None

            with SessionLocal() as recovery_session:
                recovered_while_locked = SqlAlchemyAcquisitionRepository(
                    recovery_session
                ).recover_stale_runs(now=now, limit=1)

            assert recovered_while_locked == 1
            locking_session.commit()

        with SessionLocal() as recovery_session:
            recovered_after_release = SqlAlchemyAcquisitionRepository(
                recovery_session
            ).recover_stale_runs(now=now, limit=1)

        assert recovered_after_release == 1
        with SessionLocal() as verify_db:
            recovered_runs = verify_db.scalars(
                select(AcquisitionRun).where(AcquisitionRun.id.in_(run_ids))
            ).all()
            assert len(recovered_runs) == 2
            assert {run.status for run in recovered_runs} == {"FAILED"}
            assert all(run.completed_at == now for run in recovered_runs)
            assert all(run.lease_expires_at is None for run in recovered_runs)
    finally:
        if source_id is not None:
            with SessionLocal() as cleanup:
                if run_ids:
                    cleanup.execute(
                        delete(AcquisitionRun).where(AcquisitionRun.id.in_(run_ids))
                    )
                if connector_id is not None:
                    cleanup.execute(
                        delete(SourceConnector).where(SourceConnector.id == connector_id)
                    )
                cleanup.execute(delete(Source).where(Source.id == source_id))
                cleanup.commit()
        engine.dispose()


class ItemsConnector:
    def __init__(self, items):
        self.items = items

    def acquire(self, **_kwargs):
        return self.items


def test_postgres_persistence_failure_rolls_back_partial_raw_records_and_outbox(db):
    source = Source(
        name=f"persistence failure source {uuid4()}",
        source_type="TEST",
        status="ACTIVE",
        permission_review_status="APPROVED",
        permission_review_reference="test-policy-review",
        permission_reviewed_at=datetime.now(timezone.utc),
    )
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id,
        connector_type="TEST",
        connector_version="1",
        status="ACTIVE",
    )
    db.add(connector)
    db.flush()

    observed_at = datetime.now(timezone.utc)
    items = [
        RawAcquisition(
            source_id=source.id,
            external_record_id="valid-record",
            source_url="https://example.test/valid",
            raw_content={"title": "Valid record"},
            content_hash="valid-hash",
            observed_at=observed_at,
        ),
        RawAcquisition(
            source_id=source.id,
            external_record_id="invalid-record",
            source_url="https://example.test/invalid",
            raw_content={"title": "Invalid record", "not_json_serializable": object()},
            content_hash="invalid-hash",
            observed_at=observed_at,
        ),
    ]
    service = AcquisitionService(
        SqlAlchemyAcquisitionRepository(db),
        ItemsConnector(items),
    )
    run_key = f"persistence-failure-{uuid4()}"

    with pytest.raises(TypeError, match="not JSON serializable"):
        service.run(source.id, connector.id, run_key, {})

    run = db.scalar(select(AcquisitionRun).where(
        AcquisitionRun.source_id == source.id,
        AcquisitionRun.run_key == run_key,
    ))
    assert run is not None
    assert run.status == "FAILED"
    assert run.completed_at is not None
    assert run.error_summary == "persistence_error:TypeError"
    assert run.metrics == {"acquired_count": 2, "persisted_count": 0}
    assert source.operational_state == "FAILED"
    assert source.last_error_category == "PERSISTENCE_ERROR"

    raw_count = db.scalar(select(func.count()).select_from(RawSourceRecord).where(
        RawSourceRecord.acquisition_run_id == run.id,
    ))
    assert raw_count == 0, "the first raw record must roll back with the failed batch"

    outbox_count = db.scalar(select(func.count()).select_from(OutboxEvent).where(
        OutboxEvent.event_type == "RAW_SOURCE_RECORD_ACQUIRED",
        OutboxEvent.payload["acquisition_run_id"].as_string() == str(run.id),
    ))
    assert outbox_count == 0, "outbox events for rolled-back raw records must also roll back"



def test_concurrent_same_acquisition_run_key_executes_connector_once():
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=8)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    source_id = connector_id = None
    run_key = f"concurrent-run-{uuid4()}"
    barrier = Barrier(2)
    connector_lock = Lock()
    connector_calls = {"count": 0}
    try:
        with SessionLocal() as seed_db:
            source = Source(
                name=f"concurrent source {uuid4()}",
                source_type="TEST",
                status="ACTIVE",
                permission_review_status="APPROVED",
                permission_review_reference="test-policy-review",
                permission_reviewed_at=datetime.now(timezone.utc),
            )
            seed_db.add(source)
            seed_db.flush()
            connector = SourceConnector(
                source_id=source.id,
                connector_type="TEST",
                connector_version="1",
                status="ACTIVE",
            )
            seed_db.add(connector)
            seed_db.commit()
            source_id, connector_id = source.id, connector.id

        class BarrierRepository:
            def __init__(self, session):
                self.inner = SqlAlchemyAcquisitionRepository(session)
                self.first_lookup = True

            def get_run(self, source, key):
                existing = self.inner.get_run(source, key)
                if self.first_lookup and existing is None:
                    self.first_lookup = False
                    barrier.wait(timeout=10)
                return existing

            def __getattr__(self, name):
                return getattr(self.inner, name)

        class CountingConnector:
            def acquire(self, **_kwargs):
                with connector_lock:
                    connector_calls["count"] += 1
                return [RawAcquisition(
                    source_id=source_id,
                    external_record_id="same-external-id",
                    source_url="https://example.test/concurrent",
                    raw_content={"title": "Technical Product Manager"},
                    content_hash="same-content-hash",
                    observed_at=datetime.now(timezone.utc),
                )]

        def run_once():
            with SessionLocal() as session:
                service = AcquisitionService(BarrierRepository(session), CountingConnector())
                return service.run(source_id, connector_id, run_key, {})

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run_once), executor.submit(run_once)]
            results = [future.result(timeout=30) for future in futures]

        assert results[0].run_id == results[1].run_id
        assert connector_calls["count"] == 1
        assert all(result.status in {"RUNNING", "SUCCEEDED"} for result in results)

        with SessionLocal() as verify_db:
            run = verify_db.scalar(select(AcquisitionRun).where(
                AcquisitionRun.source_id == source_id,
                AcquisitionRun.run_key == run_key,
            ))
            assert run is not None
            assert run.status == "SUCCEEDED"
            refreshed_source = verify_db.get(Source, source_id)
            assert refreshed_source.operational_state == "AVAILABLE"
            assert refreshed_source.last_error_category is None
            raw_records = verify_db.scalars(select(RawSourceRecord).where(
                RawSourceRecord.acquisition_run_id == run.id,
            )).all()
            assert len(raw_records) == 1
            events = verify_db.scalars(select(OutboxEvent).where(
                OutboxEvent.event_type == "RAW_SOURCE_RECORD_ACQUIRED",
                OutboxEvent.payload["acquisition_run_id"].as_string() == str(run.id),
            )).all()
            assert len(events) == 1
    finally:
        if source_id is not None:
            with SessionLocal() as cleanup:
                run_ids = list(cleanup.scalars(select(AcquisitionRun.id).where(
                    AcquisitionRun.source_id == source_id,
                    AcquisitionRun.run_key == run_key,
                )).all())
                if run_ids:
                    cleanup.execute(delete(OutboxEvent).where(
                        OutboxEvent.event_type == "RAW_SOURCE_RECORD_ACQUIRED",
                        OutboxEvent.payload["acquisition_run_id"].as_string().in_(
                            [str(run_id) for run_id in run_ids]
                        ),
                    ))
                    cleanup.execute(delete(JobDiscoveryEvent).where(
                        JobDiscoveryEvent.raw_source_record_id.in_(
                            select(RawSourceRecord.id).where(
                                RawSourceRecord.acquisition_run_id.in_(run_ids),
                            )
                        ),
                    ))
                    cleanup.execute(delete(RawSourceRecord).where(
                        RawSourceRecord.acquisition_run_id.in_(run_ids),
                    ))
                    cleanup.execute(delete(AcquisitionRun).where(
                        AcquisitionRun.id.in_(run_ids),
                    ))
                cleanup.execute(delete(SourceConnector).where(SourceConnector.id == connector_id))
                cleanup.execute(delete(Source).where(Source.id == source_id))
                cleanup.commit()
        engine.dispose()
