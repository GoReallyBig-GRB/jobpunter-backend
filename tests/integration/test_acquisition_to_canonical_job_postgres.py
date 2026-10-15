"""End-to-end PostgreSQL test for acquisition -> outbox -> canonical job."""

import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.application.common.outbox_dispatcher import (
    OutboxDispatcher,
    canonicalization_event_handler,
)
from app.application.jobs.service import AcquisitionService
from app.infrastructure.ai.job_requirement_extractor import ExtractedRequirement
from app.infrastructure.db.models import (
    AcquisitionRun,
    Job,
    JobDescriptionVersion,
    JobRequirement,
    JobDiscoveryEvent,
    JobProvenance,
    JobResolution,
    OutboxEvent,
    RawSourceRecord,
    Source,
    SourceConnector,
    SourceConnectorConfigurationVersion,
)
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
from app.infrastructure.db.repositories.canonicalization import SqlAlchemyCanonicalizationRepository
from app.ports.acquisition import RawAcquisition
from app.workers.job_requirement_extraction_worker import JobRequirementExtractionWorker


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
    assert description.completeness_status == "USABLE"
    assert description.requirement_extraction_status == "PENDING"

    def extract_grounded_requirement(**_kwargs):
        return [
            ExtractedRequirement(
                requirement_type="SKILL",
                description="Define product requirements",
                source_excerpt="define product requirements",
                importance="HIGH",
                hard_constraint=False,
                confidence=0.93,
            )
        ]

    extraction_worker = JobRequirementExtractionWorker(
        session_factory=session_factory,
        extractor=extract_grounded_requirement,
    )
    extraction_result = extraction_worker.run_once(limit=1)
    assert extraction_result["claimed"] == 1
    assert extraction_result["needs_review"] == 1

    db.expire_all()
    description = db.get(JobDescriptionVersion, description.id)
    assert description.requirement_extraction_status == "NEEDS_REVIEW"
    requirement = db.scalar(
        select(JobRequirement).where(
            JobRequirement.job_description_version_id == description.id,
        )
    )
    assert requirement is not None
    assert requirement.extraction_status == "NEEDS_REVIEW"
    assert requirement.source_excerpt == "define product requirements"
    assert requirement.extraction_version is not None

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

def test_unchanged_and_changed_observations_refresh_canonical_job_freshness(db):
    source = Source(
        name=f"observation source {uuid4()}",
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

    now = datetime.now(timezone.utc)
    unique = uuid4().hex
    base_content = {
        "title": "Technical Product Manager",
        "company": f"Observation Example {unique}",
        "url": f"https://example.test/jobs/{unique}",
        "description": (
            "Lead technical product delivery, coordinate engineering teams, define requirements, "
            "manage release planning and measure outcomes. "
        ) * 3,
        "location": "Remote - Nigeria",
    }
    raw_v1 = RawAcquisition(
        source_id=source.id,
        external_record_id=f"job-{unique}",
        source_url=base_content["url"],
        raw_content=base_content,
        content_hash=f"sha256-{unique}-v1",
        observed_at=now,
    )

    repository = SqlAlchemyAcquisitionRepository(db)
    first = AcquisitionService(repository, OneJobConnector(raw_v1)).run(
        source.id, connector_row.id, f"observation-first-{unique}", {},
    )
    assert first.status == "SUCCEEDED"
    assert (first.observed_count, first.new_count, first.changed_count, first.unchanged_count) == (1, 1, 0, 0)

    session_factory = sessionmaker(
        bind=db.connection(),
        class_=Session,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    handler = canonicalization_event_handler(session_factory)
    dispatcher = OutboxDispatcher(session_factory, {
        "RAW_SOURCE_RECORD_ACQUIRED": handler,
        "RAW_SOURCE_RECORD_OBSERVED": handler,
    })
    assert dispatcher.dispatch_batch(limit=10)["processed"] == 1

    db.expire_all()
    resolution = db.scalar(select(JobResolution).where(
        JobResolution.raw_source_record_id.is_not(None),
        JobResolution.status == "CREATED",
    ).order_by(JobResolution.created_at.desc()))
    assert resolution is not None and resolution.job_id is not None
    job_id = resolution.job_id
    first_job = db.get(Job, job_id)
    assert first_job.last_observed_at == now

    later = now + timedelta(minutes=1)
    raw_unchanged = RawAcquisition(
        source_id=source.id,
        external_record_id=raw_v1.external_record_id,
        source_url=raw_v1.source_url,
        raw_content=base_content,
        content_hash=raw_v1.content_hash,
        observed_at=later,
    )
    second = AcquisitionService(repository, OneJobConnector(raw_unchanged)).run(
        source.id, connector_row.id, f"observation-unchanged-{unique}", {},
    )
    assert second.status == "SUCCEEDED"
    assert (second.observed_count, second.new_count, second.changed_count, second.unchanged_count) == (1, 0, 0, 1)
    assert second.duplicate_count == 1

    db.expire_all()
    unchanged_event = db.scalar(select(JobDiscoveryEvent).where(
        JobDiscoveryEvent.acquisition_run_id == db.scalar(select(AcquisitionRun.id).where(
            AcquisitionRun.run_key == f"observation-unchanged-{unique}",
        )),
    ))
    assert unchanged_event is not None
    assert unchanged_event.observation_outcome == "UNCHANGED"
    assert unchanged_event.processing_status == "PENDING"

    assert dispatcher.dispatch_batch(limit=10)["processed"] == 1
    db.expire_all()
    refreshed_job = db.get(Job, job_id)
    assert refreshed_job.last_observed_at == later
    assert db.scalar(select(func.count(RawSourceRecord.id)).where(
        RawSourceRecord.source_id == source.id,
    )) == 1

    changed_at = now + timedelta(minutes=2)
    changed_content = dict(base_content)
    changed_content["description"] += (
        " New responsibility: own API integration strategy and cross-functional delivery metrics."
    )
    raw_v2 = RawAcquisition(
        source_id=source.id,
        external_record_id=raw_v1.external_record_id,
        source_url=raw_v1.source_url,
        raw_content=changed_content,
        content_hash=f"sha256-{unique}-v2",
        observed_at=changed_at,
    )
    third = AcquisitionService(repository, OneJobConnector(raw_v2)).run(
        source.id, connector_row.id, f"observation-changed-{unique}", {},
    )
    assert third.status == "SUCCEEDED"
    assert (third.observed_count, third.new_count, third.changed_count, third.unchanged_count) == (1, 0, 1, 0)

    db.expire_all()
    changed_event = db.scalar(select(JobDiscoveryEvent).where(
        JobDiscoveryEvent.acquisition_run_id == db.scalar(select(AcquisitionRun.id).where(
            AcquisitionRun.run_key == f"observation-changed-{unique}",
        )),
    ))
    assert changed_event is not None
    assert changed_event.observation_outcome == "CHANGED"
    assert dispatcher.dispatch_batch(limit=10)["processed"] == 1

    db.expire_all()
    final_job = db.get(Job, job_id)
    assert final_job.last_observed_at == changed_at
    assert changed_event.canonical_job_id == job_id
    assert changed_event.processing_status == "CANONICALIZED"
    assert db.scalar(select(func.count(JobDescriptionVersion.id)).where(
        JobDescriptionVersion.job_id == job_id,
    )) == 2
    assert db.scalar(select(func.count(Job.id)).where(
        Job.company_name == base_content["company"],
    )) == 1

def test_pending_canonicalization_uses_configuration_snapshot_from_acquisition_run(db):
    now = datetime.now(timezone.utc)
    source = Source(
        name=f"versioned canonicalization source {uuid4()}",
        source_type="TEST",
        status="ACTIVE",
        permission_review_status="APPROVED",
        permission_review_reference="test-policy-review",
        permission_reviewed_at=now,
        configuration={},
    )
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id,
        connector_type="TEST",
        connector_version="1",
        status="ACTIVE",
        configuration={"application_route_type": "FREE_ATS"},
        active_configuration_version=1,
    )
    db.add(connector)
    db.flush()
    first_configuration = SourceConnectorConfigurationVersion(
        source_connector_id=connector.id,
        version=1,
        configuration={"application_route_type": "FREE_ATS"},
        configuration_fingerprint="a" * 64,
        status="PUBLISHED",
        reason="Initial source route classification",
        created_at=now,
    )
    db.add(first_configuration)
    db.flush()

    unique = uuid4().hex
    raw = RawAcquisition(
        source_id=source.id,
        external_record_id=f"versioned-role-{unique}",
        source_url=f"https://listing.example.test/jobs/{unique}",
        raw_content={
            "title": "Technical Product Manager",
            "company": f"Versioned Employer {unique}",
            "job_url": f"https://apply.example.test/jobs/{unique}",
            "description": (
                "Lead technical product delivery, write engineering requirements, "
                "coordinate releases, and improve complex workflows. "
            ) * 3,
        },
        content_hash=f"versioned-hash-{unique}",
        observed_at=now,
    )
    result = AcquisitionService(
        SqlAlchemyAcquisitionRepository(db),
        OneJobConnector(raw),
    ).run(source.id, connector.id, f"versioned-run-{unique}", {})
    assert result.status == "SUCCEEDED"

    run = db.scalar(select(AcquisitionRun).where(
        AcquisitionRun.source_id == source.id,
        AcquisitionRun.run_key == f"versioned-run-{unique}",
    ))
    assert run is not None
    assert run.configuration_version_id == first_configuration.id
    raw_record = db.scalar(select(RawSourceRecord).where(
        RawSourceRecord.acquisition_run_id == run.id,
    ))
    assert raw_record is not None

    second_configuration = SourceConnectorConfigurationVersion(
        source_connector_id=connector.id,
        version=2,
        configuration={"application_route_type": "DIRECT_EMPLOYER"},
        configuration_fingerprint="b" * 64,
        status="PUBLISHED",
        reason="Change the default for future acquisitions",
        created_at=datetime.now(timezone.utc),
    )
    db.add(second_configuration)
    connector.configuration = {"application_route_type": "DIRECT_EMPLOYER"}
    connector.active_configuration_version = 2
    db.flush()

    resolution = SqlAlchemyCanonicalizationRepository(db).process_raw_record(raw_record.id)
    assert resolution["status"] == "CREATED"
    job = db.get(Job, UUID(resolution["job_id"]))
    assert job is not None
    assert job.metadata_json["application_route_type"] == "FREE_ATS"




@pytest.mark.parametrize(
    ("outcome", "error_category", "expected_source_state"),
    [
        ("SUCCEEDED", None, "AVAILABLE"),
        ("PARTIAL", "PAGE_FETCH_FAILED", "DEGRADED"),
        ("RESTRICTED", "SOURCE_RESTRICTED", "RESTRICTED"),
        ("UNAVAILABLE", "SOURCE_UNAVAILABLE", "UNAVAILABLE"),
        ("FAILED", "UPSTREAM_FAILURE", "FAILED"),
    ],
)
def test_acquisition_outcome_is_persisted_and_reflected_in_source_state(
    db, outcome, error_category, expected_source_state,
):
    source = Source(
        name=f"outcome source {uuid4()}",
        source_type="MANUAL_SUBMISSION",
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

    repository = SqlAlchemyAcquisitionRepository(db)
    run, created = repository.create_run(source.id, connector.id, f"outcome-{uuid4()}")
    assert created

    partial = outcome == "PARTIAL"
    repository.complete_run(
        run.id,
        acquired_count=2 if partial else 0,
        persisted_count=1 if partial else 0,
        status=outcome,
        error_category=error_category,
        failure_count=1 if partial else 0,
        failure_categories=[error_category] if partial else None,
        retry_after_seconds=30 if partial else None,
    )

    db.expire_all()
    persisted_run = db.get(AcquisitionRun, run.id)
    persisted_source = db.get(Source, source.id)
    assert persisted_run is not None
    assert persisted_run.status == outcome
    assert persisted_run.metrics["failed_item_count"] == (1 if partial else 0)
    assert persisted_source is not None
    assert persisted_source.operational_state == expected_source_state
    assert persisted_source.last_error_category == error_category



def test_failed_attempt_does_not_advance_last_successful_source_observation(db):
    previous_observation = datetime(2026, 10, 1, tzinfo=timezone.utc)
    source = Source(
        name=f"failed source {uuid4()}",
        source_type="MANUAL_SUBMISSION",
        status="ACTIVE",
        last_observed_at=previous_observation,
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

    repository = SqlAlchemyAcquisitionRepository(db)
    run, created = repository.create_run(source.id, connector.id, f"failed-{uuid4()}")
    assert created
    repository.fail_run(run.id, error_summary="connector_error:TimeoutError")

    db.expire_all()
    persisted_source = db.get(Source, source.id)
    persisted_run = db.get(AcquisitionRun, run.id)
    assert persisted_source.last_observed_at == previous_observation
    assert persisted_source.operational_state == "FAILED"
    assert persisted_run.status == "FAILED"
