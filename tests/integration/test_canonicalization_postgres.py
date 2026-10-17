import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.db.models import (
    AcquisitionRun, Job, JobDescriptionVersion, JobProvenance, JobResolution,
    JobResolutionCandidate, JobVerificationAttempt, RawSourceRecord, Source, SourceConnector, User,
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
    first.raw_content = {
        **first.raw_content,
        "salary_min": 120000,
        "salary_max": 150000,
        "salary_currency": "usd",
        "salary_period": "annual",
        "seniority": "Senior",
    }
    repository = SqlAlchemyCanonicalizationRepository(db)
    created = repository.process_raw_record(first.id)
    assert created["status"] == "CREATED"
    assert created["replayed"] is False

    replay = repository.process_raw_record(first.id)
    assert replay["replayed"] is True
    assert replay["job_id"] == created["job_id"]

    matched_record = seed_raw_record(
        db, source=source, connector=connector, title="Technical Product Manager",
        description=("Lead technical product delivery across distributed teams. " * 13),
        url="https://jobs.example.test/role/123", external_id="one",
    )
    matched_record.raw_content = {
        **matched_record.raw_content,
        "salary_min": 130000,
        "salary_max": 160000,
        "salary_currency": "USD",
        "salary_period": "yearly",
        "seniority": "Lead",
    }
    matched = repository.process_raw_record(matched_record.id)
    assert matched["status"] == "MATCHED"
    assert matched["job_id"] == created["job_id"]

    # An observation with no compensation fields must not erase known canonical facts.
    missing_salary_record = seed_raw_record(
        db, source=source, connector=connector, title="Technical Product Manager",
        description=("Lead technical product delivery across distributed teams. " * 13),
        url="https://jobs.example.test/role/123", external_id="one",
    )
    missing_salary = repository.process_raw_record(missing_salary_record.id)
    assert missing_salary["status"] == "MATCHED"
    assert missing_salary["job_id"] == created["job_id"]

    job_id = __import__("uuid").UUID(created["job_id"])
    canonical_job = db.get(Job, job_id)
    assert canonical_job is not None
    assert canonical_job.salary_min == 130000
    assert canonical_job.salary_max == 160000
    assert canonical_job.salary_currency == "USD"
    assert canonical_job.salary_period == "YEAR"
    assert canonical_job.metadata_json["seniority"] == "Lead"
    assert canonical_job.last_observed_at == max(
        first.acquired_at, matched_record.acquired_at, missing_salary_record.acquired_at
    )
    versions = db.scalars(
        select(JobDescriptionVersion).where(JobDescriptionVersion.job_id == job_id)
    ).all()
    resolutions = db.scalars(
        select(JobResolution).where(JobResolution.job_id == job_id)
    ).all()
    assert len(versions) == 2
    assert len({version.content_hash for version in versions}) == 2
    assert {version.requirement_extraction_status for version in versions} == {"PENDING"}
    assert len(resolutions) == 3
    assert db.get(Job, job_id) is not None



def test_postgres_url_collision_with_conflicting_title_requires_review(db):
    source = Source(name=f"url collision source {uuid4()}", source_type="TEST", status="ACTIVE")
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id, connector_type="TEST", connector_version="1", status="ACTIVE",
    )
    db.add(connector)
    db.flush()

    repository = SqlAlchemyCanonicalizationRepository(db)
    shared_url = "https://jobs.example.test/careers"
    first = seed_raw_record(
        db, source=source, connector=connector, title="Product Manager",
        description=("Manage product delivery and coordinate teams. Requirements include relevant experience and communication skills. " * 4),
        url=shared_url, external_id="url-collision-one",
    )
    created = repository.process_raw_record(first.id)
    assert created["status"] == "CREATED"

    second = seed_raw_record(
        db, source=source, connector=connector, title="Finance Analyst",
        description=("Analyze financial reports and prepare forecasts. Requirements include accounting experience and strong analytical skills. " * 4),
        url=shared_url, external_id="url-collision-two",
    )
    result = repository.process_raw_record(second.id)

    assert result["status"] == "REQUIRES_REVIEW"
    assert result["job_id"] is None
    assert result["candidate_job_ids"] == [created["job_id"]]
    resolution = db.scalar(
        select(JobResolution).where(JobResolution.raw_source_record_id == second.id)
    )
    assert resolution is not None
    assert resolution.status == "REQUIRES_REVIEW"
    assert resolution.resolution_method == "URL_IDENTITY_COLLISION"
    assert resolution.job_id is None
    canonical_jobs = db.scalars(
        select(Job).where(
            Job.identity_status == "CANONICAL",
            Job.application_url == shared_url,
        )
    ).all()
    assert len(canonical_jobs) == 1, "conflicting URL identity must not silently merge or auto-create a canonical job"


def test_postgres_canonicalization_records_each_observation_without_duplicate_description_version(db):
    source = Source(name="provenance test source", source_type="TEST", status="ACTIVE")
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id, connector_type="TEST", connector_version="1", status="ACTIVE",
    )
    db.add(connector)
    db.flush()

    description = "Manage product delivery and coordinate technical teams. " * 12
    first = seed_raw_record(
        db, source=source, connector=connector, title="Technical Product Manager",
        description=description,
        url="https://jobs.example.test/role/456?utm_source=feed", external_id="provenance-one",
    )
    repository = SqlAlchemyCanonicalizationRepository(db)
    created = repository.process_raw_record(first.id)
    assert created["status"] == "CREATED"

    second = seed_raw_record(
        db, source=source, connector=connector, title="Technical Product Manager",
        description=description,
        url="https://jobs.example.test/role/456", external_id="provenance-two",
    )
    matched = repository.process_raw_record(second.id)
    assert matched["status"] == "MATCHED"
    assert matched["job_id"] == created["job_id"]

    job_id = __import__("uuid").UUID(created["job_id"])
    versions = db.scalars(
        select(JobDescriptionVersion).where(JobDescriptionVersion.job_id == job_id)
    ).all()
    provenance = db.scalars(
        select(JobProvenance).where(JobProvenance.job_id == job_id)
    ).all()

    assert len(versions) == 1, "unchanged description content must not create a new version"
    assert {entry.raw_source_record_id for entry in provenance} == {first.id, second.id}
    assert len(provenance) == 2, "each distinct source observation must retain provenance"

    replay = repository.process_raw_record(second.id)
    assert replay["replayed"] is True
    assert len(db.scalars(
        select(JobDescriptionVersion).where(JobDescriptionVersion.job_id == job_id)
    ).all()) == 1
    assert len(db.scalars(
        select(JobProvenance).where(JobProvenance.job_id == job_id)
    ).all()) == 2, "replaying a raw record must not duplicate provenance"



def test_postgres_canonicalization_matches_source_external_id_when_url_and_description_change(db):
    source = Source(name="external ID test source", source_type="TEST", status="ACTIVE")
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id, connector_type="TEST", connector_version="1", status="ACTIVE",
    )
    db.add(connector)
    db.flush()

    repository = SqlAlchemyCanonicalizationRepository(db)
    first = seed_raw_record(
        db, source=source, connector=connector, title="Product Manager",
        description="Own roadmap planning and delivery. " * 12,
        url="https://jobs.example.test/role/old", external_id="stable-source-job-42",
    )
    created = repository.process_raw_record(first.id)
    assert created["status"] == "CREATED"
    job_id = UUID(created["job_id"])
    canonical_job = db.get(Job, job_id)
    canonical_job.lifecycle_status = "ACTIVE"
    canonical_job.freshness_status = "FRESH"
    canonical_job.last_verified_at = first.acquired_at
    db.add(JobVerificationAttempt(
        job_id=job_id,
        checked_url=canonical_job.application_url,
        final_url=canonical_job.application_url,
        status="ACTIVE",
        http_status_code=200,
        reason_code="HTTP_200",
        evidence={"source": "integration-test"},
        verifier_version="test-verifier",
        checked_at=first.acquired_at,
        created_at=first.acquired_at,
    ))
    db.flush()

    second = seed_raw_record(
        db, source=source, connector=connector, title="Product Manager",
        description="Own product strategy, roadmap planning and delivery. " * 13,
        url="https://jobs.example.test/role/new", external_id="stable-source-job-42",
    )
    matched = repository.process_raw_record(second.id)

    assert matched["status"] == "MATCHED"
    assert matched["job_id"] == created["job_id"]
    db.refresh(canonical_job)
    assert canonical_job.application_url == "https://jobs.example.test/role/new"
    assert canonical_job.lifecycle_status == "DISCOVERED"
    assert canonical_job.freshness_status == "UNKNOWN"
    assert canonical_job.last_verified_at is None

    missing_title_record = seed_raw_record(
        db, source=source, connector=connector, title="Product Manager",
        description="Own product strategy, roadmap planning and delivery. " * 13,
        url="https://jobs.example.test/role/new", external_id="stable-source-job-42",
    )
    missing_title_record.raw_content = {
        **missing_title_record.raw_content,
        "title": None,
    }
    missing_title_result = repository.process_raw_record(missing_title_record.id)
    assert missing_title_result["status"] == "MATCHED"
    db.refresh(canonical_job)
    assert canonical_job.canonical_title == "Product Manager"

    second_resolution = db.scalar(
        select(JobResolution).where(JobResolution.raw_source_record_id == second.id)
    )
    assert second_resolution.resolution_method == "SOURCE_EXTERNAL_ID"
    canonical_jobs = db.scalars(
        select(Job).where(Job.identity_status == "CANONICAL")
    ).all()
    assert len(canonical_jobs) == 1, "source-scoped external IDs should converge on one canonical job"
    job_id = __import__("uuid").UUID(created["job_id"])
    versions = db.scalars(
        select(JobDescriptionVersion).where(JobDescriptionVersion.job_id == job_id)
    ).all()
    assert len(versions) == 2, "changed source descriptions must retain version history"



def test_postgres_concurrent_distinct_raw_records_converge_on_one_canonical_job():
    """Two transactions resolving the same vacancy must not both create a Job."""
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=5)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    source_id = None
    connector_id = None
    raw_ids = []
    run_ids = []
    try:
        with SessionLocal() as seed_db:
            source = Source(name=f"concurrency source {uuid4()}", source_type="TEST", status="ACTIVE")
            seed_db.add(source)
            seed_db.flush()
            connector = SourceConnector(
                source_id=source.id, connector_type="TEST",
                connector_version="1", status="ACTIVE",
            )
            seed_db.add(connector)
            seed_db.flush()
            source_id, connector_id = source.id, connector.id

            description = "Coordinate technical product delivery across engineering teams. " * 12
            first = seed_raw_record(
                seed_db, source=source, connector=connector,
                title="Technical Product Manager", description=description,
                url="https://jobs.example.test/concurrent/role-a", external_id="concurrent-a",
            )
            second = seed_raw_record(
                seed_db, source=source, connector=connector,
                title="Technical Product Manager", description=description,
                url="https://jobs.example.test/concurrent/role-b", external_id="concurrent-b",
            )
            raw_ids = [first.id, second.id]
            run_ids = [first.acquisition_run_id, second.acquisition_run_id]
            seed_db.commit()

        barrier = Barrier(2)

        def process(raw_id):
            with SessionLocal() as session:
                barrier.wait(timeout=10)
                return SqlAlchemyCanonicalizationRepository(session).process_raw_record(raw_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(process, raw_id) for raw_id in raw_ids]
            results = [future.result(timeout=30) for future in futures]

        assert results[0]["job_id"] == results[1]["job_id"]
        assert sorted(result["status"] for result in results) == ["CREATED", "MATCHED"]

        with SessionLocal() as verify_db:
            resolutions = verify_db.scalars(
                select(JobResolution).where(JobResolution.raw_source_record_id.in_(raw_ids))
            ).all()
            assert len(resolutions) == 2
            canonical_job_ids = {resolution.job_id for resolution in resolutions}
            assert len(canonical_job_ids) == 1
            assert verify_db.get(Job, next(iter(canonical_job_ids))) is not None
    finally:
        # This test commits setup data so independent PostgreSQL connections can
        # observe it. Remove only the uniquely identified records created here.
        with SessionLocal() as cleanup_db:
            resolutions = cleanup_db.scalars(
                select(JobResolution).where(JobResolution.raw_source_record_id.in_(raw_ids))
            ).all() if raw_ids else []
            resolution_ids = [row.id for row in resolutions]
            job_ids = {row.job_id for row in resolutions if row.job_id is not None}
            if resolution_ids:
                for candidate in cleanup_db.scalars(
                    select(JobResolutionCandidate).where(
                        JobResolutionCandidate.resolution_id.in_(resolution_ids)
                    )
                ).all():
                    cleanup_db.delete(candidate)
            if raw_ids:
                for provenance in cleanup_db.scalars(
                    select(JobProvenance).where(JobProvenance.raw_source_record_id.in_(raw_ids))
                ).all():
                    cleanup_db.delete(provenance)
                for resolution in resolutions:
                    cleanup_db.delete(resolution)
            if job_ids:
                for version in cleanup_db.scalars(
                    select(JobDescriptionVersion).where(JobDescriptionVersion.job_id.in_(job_ids))
                ).all():
                    cleanup_db.delete(version)
                for job_id in job_ids:
                    job = cleanup_db.get(Job, job_id)
                    if job is not None:
                        cleanup_db.delete(job)
            if raw_ids:
                for raw in cleanup_db.scalars(
                    select(RawSourceRecord).where(RawSourceRecord.id.in_(raw_ids))
                ).all():
                    cleanup_db.delete(raw)
            if run_ids:
                for run in cleanup_db.scalars(
                    select(AcquisitionRun).where(AcquisitionRun.id.in_(run_ids))
                ).all():
                    cleanup_db.delete(run)
            if connector_id:
                connector = cleanup_db.get(SourceConnector, connector_id)
                if connector is not None:
                    cleanup_db.delete(connector)
            if source_id:
                source = cleanup_db.get(Source, source_id)
                if source is not None:
                    cleanup_db.delete(source)
            cleanup_db.commit()
        engine.dispose()


def test_production_outbox_handler_is_idempotent_under_duplicate_delivery(db):
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from app.application.common.outbox_dispatcher import canonicalization_event_handler
    from app.infrastructure.db.models import OutboxEvent

    source = Source(name=f"duplicate delivery source {uuid4()}", source_type="TEST", status="ACTIVE")
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id, connector_type="TEST",
        connector_version="1", status="ACTIVE",
    )
    db.add(connector)
    db.flush()
    raw = seed_raw_record(
        db,
        source=source,
        connector=connector,
        title="Technical Product Manager",
        description="Coordinate product delivery across technical teams. " * 12,
        url="https://jobs.example.test/duplicate-delivery/role",
        external_id=f"duplicate-{uuid4()}",
    )
    db.add(OutboxEvent(
        event_type="RAW_SOURCE_RECORD_ACQUIRED",
        aggregate_type="RawSourceRecord",
        aggregate_id=raw.id,
        payload={"raw_source_record_id": str(raw.id)},
        status="PENDING",
        attempt_count=0,
        available_at=datetime.now(timezone.utc),
    ))
    db.flush()

    factory = sessionmaker(
        bind=db.connection(),
        class_=Session,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    handler = canonicalization_event_handler(factory)
    payload = {"raw_source_record_id": str(raw.id)}

    handler(payload)
    handler(payload)

    resolutions = db.scalars(
        select(JobResolution).where(JobResolution.raw_source_record_id == raw.id)
    ).all()
    provenance = db.scalars(
        select(JobProvenance).where(JobProvenance.raw_source_record_id == raw.id)
    ).all()
    assert len(resolutions) == 1, "duplicate delivery must not duplicate resolution"
    assert len(provenance) == 1, "duplicate delivery must not duplicate provenance"
    assert resolutions[0].job_id is not None
    versions = db.scalars(
        select(JobDescriptionVersion).where(JobDescriptionVersion.job_id == resolutions[0].job_id)
    ).all()
    assert len(versions) == 1
    assert db.get(Job, resolutions[0].job_id) is not None



def test_postgres_canonicalization_uses_explicit_connector_route_configuration(db):
    source = Source(
        name=f"route config source {uuid4()}",
        source_type="TEST",
        status="ACTIVE",
        configuration={"application_route_type": "DIRECT_EMPLOYER"},
    )
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id,
        connector_type="TEST",
        connector_version="1",
        status="ACTIVE",
        configuration={"application_route_type": "free ats"},
    )
    db.add(connector)
    db.flush()

    record = seed_raw_record(
        db,
        source=source,
        connector=connector,
        title="Route Configured Product Manager",
        description="Lead product delivery and coordinate engineering teams. " * 12,
        url="https://jobs.example.test/route-configured-role",
        external_id="route-configured-role",
    )
    repository = SqlAlchemyCanonicalizationRepository(db)
    result = repository.process_raw_record(record.id)

    assert result["status"] == "CREATED"
    job = db.get(Job, __import__("uuid").UUID(result["job_id"]))
    assert job.metadata_json["application_route_type"] == "FREE_ATS"


def test_postgres_canonicalization_prefers_explicit_per_job_route_over_config(db):
    source = Source(
        name=f"route override source {uuid4()}",
        source_type="TEST",
        status="ACTIVE",
        configuration={"application_route_type": "DIRECT_EMPLOYER"},
    )
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id,
        connector_type="TEST",
        connector_version="1",
        status="ACTIVE",
        configuration={"application_route_type": "FREE_ATS"},
    )
    db.add(connector)
    db.flush()

    record = seed_raw_record(
        db,
        source=source,
        connector=connector,
        title="Explicit Route Override Product Manager",
        description="Lead product delivery and coordinate engineering teams. " * 12,
        url="https://jobs.example.test/route-override-role",
        external_id="route-override-role",
    )
    record.raw_content["application_route_type"] = "BLOCKED"
    db.flush()

    result = SqlAlchemyCanonicalizationRepository(db).process_raw_record(record.id)
    job = db.get(Job, __import__("uuid").UUID(result["job_id"]))

    assert result["status"] == "CREATED"
    assert job.metadata_json["application_route_type"] == "BLOCKED"


def test_postgres_canonicalization_does_not_infer_route_type_from_url(db):
    source = Source(
        name=f"unknown route source {uuid4()}",
        source_type="TEST",
        status="ACTIVE",
        configuration={},
    )
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id,
        connector_type="TEST",
        connector_version="1",
        status="ACTIVE",
        configuration={},
    )
    db.add(connector)
    db.flush()

    record = seed_raw_record(
        db,
        source=source,
        connector=connector,
        title="Unknown Route Product Manager",
        description="Lead product delivery and coordinate engineering teams. " * 12,
        url="https://free.example.test/apply",
        external_id="unknown-route-role",
    )
    result = SqlAlchemyCanonicalizationRepository(db).process_raw_record(record.id)
    job = db.get(Job, __import__("uuid").UUID(result["job_id"]))

    assert result["status"] == "CREATED"
    assert "application_route_type" not in (job.metadata_json or {})



def test_postgres_route_classification_does_not_cross_overwrite_canonical_application_url(db):
    first_source = Source(
        name=f"first route source {uuid4()}",
        source_type="TEST",
        status="ACTIVE",
        configuration={"application_route_type": "DIRECT_EMPLOYER"},
    )
    second_source = Source(
        name=f"second route source {uuid4()}",
        source_type="TEST",
        status="ACTIVE",
        configuration={"application_route_type": "FREE_AGGREGATOR"},
    )
    db.add_all([first_source, second_source])
    db.flush()
    first_connector = SourceConnector(
        source_id=first_source.id,
        connector_type="TEST",
        connector_version="1",
        status="ACTIVE",
        configuration={},
    )
    second_connector = SourceConnector(
        source_id=second_source.id,
        connector_type="TEST",
        connector_version="1",
        status="ACTIVE",
        configuration={},
    )
    db.add_all([first_connector, second_connector])
    db.flush()

    description = "Lead product delivery and coordinate engineering teams. " * 12
    repository = SqlAlchemyCanonicalizationRepository(db)
    first_record = seed_raw_record(
        db,
        source=first_source,
        connector=first_connector,
        title="Cross Source Route Product Manager",
        description=description,
        url="https://employer.example.test/careers/role-1",
        external_id="first-source-route-role",
    )
    first_result = repository.process_raw_record(first_record.id)
    job = db.get(Job, __import__("uuid").UUID(first_result["job_id"]))
    assert job.metadata_json["application_route_type"] == "DIRECT_EMPLOYER"

    second_record = seed_raw_record(
        db,
        source=second_source,
        connector=second_connector,
        title="Cross Source Route Product Manager",
        description=description,
        url="https://aggregator.example.test/role-1",
        external_id="second-source-route-role",
    )
    second_result = repository.process_raw_record(second_record.id)

    assert second_result["status"] == "MATCHED"
    assert second_result["job_id"] == first_result["job_id"]
    assert job.application_url == "https://employer.example.test/careers/role-1"
    assert job.metadata_json["application_route_type"] == "DIRECT_EMPLOYER"


def test_manual_submission_provenance_is_private_and_cannot_mutate_shared_job(db):
    now = datetime.now(timezone.utc)
    public_source = Source(
        name=f"public source {uuid4()}",
        source_type="GREENHOUSE_JOB_BOARD",
        status="ACTIVE",
        permission_review_status="APPROVED",
        permission_review_reference="policy-review://canonicalization-test",
        permission_reviewed_at=now,
        operational_state="AVAILABLE",
    )
    manual_source = Source(
        name=f"manual source {uuid4()}",
        source_type="MANUAL_SUBMISSION",
        status="ACTIVE",
        permission_review_status="PENDING",
        operational_state="AVAILABLE",
    )
    owner = User(display_name=f"manual submitter {uuid4()}")
    db.add_all([public_source, manual_source, owner])
    db.flush()
    public_connector = SourceConnector(
        source_id=public_source.id, connector_type="GREENHOUSE_JOB_BOARD",
        connector_version="1", status="ACTIVE",
    )
    manual_connector = SourceConnector(
        source_id=manual_source.id, connector_type="MANUAL_SUBMISSION",
        connector_version="1", status="ACTIVE",
    )
    db.add_all([public_connector, manual_connector])
    db.flush()

    public_record = seed_raw_record(
        db, source=public_source, connector=public_connector,
        title="Technical Product Manager",
        description="Public job description with responsibilities and requirements. " * 12,
        url="https://jobs.example.test/shared-role", external_id="public-shared-role",
    )
    repository = SqlAlchemyCanonicalizationRepository(db)
    created = repository.process_raw_record(public_record.id)
    assert created["status"] == "CREATED"
    job_id = __import__("uuid").UUID(created["job_id"])
    original_job = db.get(Job, job_id)
    original_last_observed = original_job.last_observed_at
    original_metadata = dict(original_job.metadata_json)
    original_versions = db.scalars(
        select(JobDescriptionVersion).where(JobDescriptionVersion.job_id == job_id)
    ).all()
    assert len(original_versions) == 1
    original_content_hash = original_versions[0].content_hash

    manual_record = seed_raw_record(
        db, source=manual_source, connector=manual_connector,
        title="Technical Product Manager",
        description="Unreviewed user content that must not replace the shared JD. " * 15,
        url="https://jobs.example.test/shared-role", external_id="manual-shared-role",
    )
    manual_record.acquired_at = original_last_observed + timedelta(days=1)
    manual_record.raw_content = {
        **manual_record.raw_content,
        "submitted_by_user_id": str(owner.id),
        "discovery_method": "MANUAL_JD",
    }
    matched = repository.process_raw_record(manual_record.id)
    assert matched["status"] == "MATCHED"
    assert matched["job_id"] == str(job_id)

    db.refresh(original_job)
    assert original_job.last_observed_at == original_last_observed
    assert original_job.metadata_json == original_metadata
    versions = db.scalars(
        select(JobDescriptionVersion).where(JobDescriptionVersion.job_id == job_id)
    ).all()
    assert len(versions) == 1
    assert versions[0].content_hash == original_content_hash

    provenance = db.scalar(
        select(JobProvenance).where(JobProvenance.raw_source_record_id == manual_record.id)
    )
    assert provenance is not None
    assert provenance.submitted_by_user_id == owner.id


def test_manual_only_submissions_from_different_candidates_do_not_merge(db):
    owner_a = User(display_name=f"manual owner A {uuid4()}")
    owner_b = User(display_name=f"manual owner B {uuid4()}")
    source = Source(
        name=f"private manual source {uuid4()}",
        source_type="MANUAL_SUBMISSION",
        status="ACTIVE",
    )
    db.add_all([owner_a, owner_b, source])
    db.flush()
    connector = SourceConnector(
        source_id=source.id,
        connector_type="MANUAL_SUBMISSION",
        connector_version="1",
        status="ACTIVE",
    )
    db.add(connector)
    db.flush()

    repository = SqlAlchemyCanonicalizationRepository(db)
    url = "https://jobs.example.test/private-role"
    first = seed_raw_record(
        db, source=source, connector=connector, title="Private Product Manager",
        description=("Coordinate product delivery across engineering and design. " * 10),
        url=url, external_id="private-role-a",
    )
    first.raw_content = {**first.raw_content, "submitted_by_user_id": str(owner_a.id)}
    created_a = repository.process_raw_record(first.id)
    assert created_a["status"] == "CREATED"

    second = seed_raw_record(
        db, source=source, connector=connector, title="Private Product Manager",
        description=("Coordinate product delivery across engineering, design, and operations. " * 11),
        url=url, external_id="private-role-b",
    )
    second.raw_content = {**second.raw_content, "submitted_by_user_id": str(owner_b.id)}
    created_b = repository.process_raw_record(second.id)
    assert created_b["status"] == "CREATED"
    assert created_b["job_id"] != created_a["job_id"]

    owners = db.scalars(
        select(JobProvenance.submitted_by_user_id)
        .where(JobProvenance.job_id.in_([
            __import__("uuid").UUID(created_a["job_id"]),
            __import__("uuid").UUID(created_b["job_id"]),
        ]))
        .order_by(JobProvenance.submitted_by_user_id)
    ).all()
    assert set(owners) == {owner_a.id, owner_b.id}
