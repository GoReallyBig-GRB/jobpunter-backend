import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import (
    AcquisitionRun, Job, JobDescriptionVersion, JobResolution, RawSourceRecord,
    Source, SourceConnector,
)
from app.infrastructure.db.repositories.canonicalization import SqlAlchemyCanonicalizationRepository


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


def seed_raw_record(db, *, source, connector, title, description, url, external_id):
    now = datetime.now(timezone.utc)
    run = AcquisitionRun(
        source_id=source.id, source_connector_id=connector.id,
        run_key=f"test-{uuid4()}", status="SUCCEEDED", started_at=now,
        completed_at=now, metrics={},
    )
    db.add(run)
    db.flush()
    record = RawSourceRecord(
        acquisition_run_id=run.id, source_id=source.id, external_record_id=external_id,
        deduplication_key=f"id:{external_id}", source_url=url,
        raw_content={"title": title, "company": "Example Co", "url": url,
                     "description": description, "city": "Lagos"},
        content_hash=f"raw-{uuid4().hex}", status="ACQUIRED", acquired_at=now,
    )
    db.add(record)
    db.flush()
    return record


def test_postgres_canonicalization_creates_job_versions_description_and_replays(db):
    now = datetime.now(timezone.utc)
    source = Source(name="integration source", source_type="TEST", status="ACTIVE")
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id, connector_type="TEST", connector_version="1", status="ACTIVE",
    )
    db.add(connector)
    db.flush()

    first = seed_raw_record(
        db, source=source, connector=connector, title="Technical Product Manager",
        description=("Lead technical product delivery across teams. " * 12),
        url="https://jobs.example.test/role/123?utm_source=feed", external_id="one",
    )
    repository = SqlAlchemyCanonicalizationRepository(db)
    created = repository.process_raw_record(first.id)
    assert created["status"] == "CREATED"
    assert created["replayed"] is False

    replay = repository.process_raw_record(first.id)
    assert replay["replayed"] is True
    assert replay["job_id"] == created["job_id"]

    second = seed_raw_record(
        db, source=source, connector=connector, title="Technical Product Manager",
        description=("Lead technical product delivery across distributed teams. " * 13),
        url="https://jobs.example.test/role/123", external_id="two",
    )
    matched = repository.process_raw_record(second.id)
    assert matched["status"] == "MATCHED"
    assert matched["job_id"] == created["job_id"]

    job_id = __import__("uuid").UUID(created["job_id"])
    versions = db.scalars(
        select(JobDescriptionVersion).where(JobDescriptionVersion.job_id == job_id)
    ).all()
    resolutions = db.scalars(
        select(JobResolution).where(JobResolution.job_id == job_id)
    ).all()
    assert len(versions) == 2
    assert len(resolutions) == 2
    assert db.get(Job, job_id) is not None
