"""End-to-end PostgreSQL test for acquisition -> outbox -> canonical job."""

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.application.common.outbox_dispatcher import (
    OutboxDispatcher,
    canonicalization_event_handler,
)
from app.application.jobs.service import AcquisitionService
from app.infrastructure.db.models import (
    AcquisitionRun,
    Job,
    JobDescriptionVersion,
    JobProvenance,
    JobResolution,
    OutboxEvent,
    RawSourceRecord,
    Source,
    SourceConnector,
)
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
from app.ports.acquisition import RawAcquisition


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


class OneJobConnector:
    connector_type = "TEST"
    connector_version = "1"

    def __init__(self, item):
        self.item = item

    def acquire(self, **_kwargs):
        return [self.item]


def test_acquired_job_reaches_canonical_storage_through_outbox(db):
    source = Source(
        name=f"end-to-end source {uuid4()}",
        source_type="TEST",
        status="ACTIVE",
    )
    db.add(source)
    db.flush()

    connector_row = SourceConnector(
        source_id=source.id,
        connector_type="TEST",
        connector_version="1",
        status="ACTIVE",
    )
    db.add(connector_row)
    db.flush()

    unique = uuid4().hex
    raw = RawAcquisition(
        source_id=source.id,
        external_record_id=f"job-{unique}",
        source_url=f"https://example.test/jobs/{unique}",
        raw_content={
            "title": "Technical Product Manager",
            "company": f"Example Company {unique}",
            "url": f"https://example.test/jobs/{unique}",
            "description": (
                "Lead technical product delivery, coordinate engineering teams, "
                "define product requirements, and manage complex workflows. "
            ) * 3,
            "city": "Lagos",
            "location": "Remote - Nigeria",
        },
        content_hash=f"sha256-{unique}",
        observed_at=datetime.now(timezone.utc),
    )

    result = AcquisitionService(
        SqlAlchemyAcquisitionRepository(db),
        OneJobConnector(raw),
    ).run(source.id, connector_row.id, f"e2e-{unique}", {})

    assert result.status == "SUCCEEDED"
    assert result.acquired_count == 1
    assert result.persisted_count == 1

    db.expire_all()
    run = db.scalar(
        select(AcquisitionRun).where(
            AcquisitionRun.source_id == source.id,
            AcquisitionRun.run_key == f"e2e-{unique}",
        )
    )
    raw_record = db.scalar(
        select(RawSourceRecord).where(
            RawSourceRecord.acquisition_run_id == run.id,
        )
    )
    assert raw_record is not None
    event = db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.event_type == "RAW_SOURCE_RECORD_ACQUIRED",
            OutboxEvent.aggregate_id == raw_record.id,
        )
    )
    assert event is not None
    assert event.status == "PENDING"

    session_factory = sessionmaker(
        bind=db.connection(),
        class_=Session,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    dispatcher = OutboxDispatcher(
        session_factory,
        {"RAW_SOURCE_RECORD_ACQUIRED": canonicalization_event_handler(session_factory)},
    )
    dispatch_result = dispatcher.dispatch_batch(limit=10)

    assert dispatch_result == {
        "claimed": 1,
        "processed": 1,
        "retrying": 0,
        "dead": 0,
        "other": 0,
    }

    db.expire_all()
    resolution = db.scalar(
        select(JobResolution).where(
            JobResolution.raw_source_record_id == raw_record.id,
        )
    )
    assert resolution is not None
    assert resolution.status == "CREATED"
    assert resolution.job_id is not None

    job = db.get(Job, resolution.job_id)
    assert job is not None
    assert job.canonical_title == "Technical Product Manager"

    description = db.scalar(
        select(JobDescriptionVersion).where(
            JobDescriptionVersion.job_id == job.id,
        )
    )
    assert description is not None
    assert len(description.content) > 80

    provenance = db.scalar(
        select(JobProvenance).where(
            JobProvenance.job_id == job.id,
            JobProvenance.raw_source_record_id == raw_record.id,
        )
    )
    assert provenance is not None

    db.refresh(event)
    assert event.status == "PROCESSED"
    assert event.processed_at is not None
