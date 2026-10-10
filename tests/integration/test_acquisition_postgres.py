import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.application.jobs.service import AcquisitionService
from app.infrastructure.db.models import AcquisitionRun, OutboxEvent, RawSourceRecord, Source, SourceConnector
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

    with pytest.raises((TypeError, ValueError)):
        service.run(source.id, connector.id, run_key, {})

    run = db.scalar(select(AcquisitionRun).where(
        AcquisitionRun.source_id == source.id,
        AcquisitionRun.run_key == run_key,
    ))
    assert run is not None
    assert run.status == "FAILED"
    assert run.completed_at is not None
    assert run.error_summary == "persistence_error:StatementError"
    assert run.metrics == {"acquired_count": 2, "persisted_count": 0}

    raw_count = db.scalar(select(func.count()).select_from(RawSourceRecord).where(
        RawSourceRecord.acquisition_run_id == run.id,
    ))
    assert raw_count == 0, "the first raw record must roll back with the failed batch"

    outbox_count = db.scalar(select(func.count()).select_from(OutboxEvent).where(
        OutboxEvent.event_type == "RAW_SOURCE_RECORD_ACQUIRED",
        OutboxEvent.payload["acquisition_run_id"].as_string() == str(run.id),
    ))
    assert outbox_count == 0, "outbox events for rolled-back raw records must also roll back"
