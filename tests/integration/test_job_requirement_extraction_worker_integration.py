from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.infrastructure.ai.job_requirement_extractor import ExtractedRequirement
from app.infrastructure.db.models import Base, Job, JobDescriptionVersion, JobRequirement
from app.workers.job_requirement_extraction_worker import JobRequirementExtractionWorker


def make_database():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [Job.__table__, JobDescriptionVersion.__table__, JobRequirement.__table__]
    Base.metadata.create_all(engine, tables=tables)
    return engine, sessionmaker(bind=engine, expire_on_commit=True)


def seed_description(session_factory):
    with session_factory() as db:
        job = Job(canonical_title="Technical Product Manager")
        db.add(job)
        db.flush()
        version = JobDescriptionVersion(
            job_id=job.id,
            version_number=1,
            content="Experience with SQL in production systems.",
            content_hash="sha-1",
            completeness_status="USABLE",
            retrieved_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
        )
        db.add(version)
        db.commit()
        return version.id


def test_worker_persists_grounded_requirements_for_human_review():
    engine, session_factory = make_database()
    version_id = seed_description(session_factory)

    def extractor(**_kwargs):
        return [ExtractedRequirement(
            requirement_type="SKILL",
            description="Experience using SQL in production systems",
            source_excerpt="Experience with SQL in production systems.",
            importance="HIGH",
            severity="IMPORTANT",
            dimension="TECHNICAL",
            hard_constraint=False,
            confidence=0.91,
        )]

    worker = JobRequirementExtractionWorker(session_factory=session_factory, extractor=extractor)
    result = worker.run_once(limit=2)

    assert result["claimed"] == 1
    assert result["needs_review"] == 1
    with session_factory() as db:
        version = db.get(JobDescriptionVersion, version_id)
        requirements = db.scalars(
            select(JobRequirement).where(JobRequirement.job_description_version_id == version_id)
        ).all()
        assert version.requirement_extraction_status == "NEEDS_REVIEW"
        assert version.requirement_extraction_attempts == 1
        assert len(requirements) == 1
        assert requirements[0].extraction_status == "NEEDS_REVIEW"
        assert requirements[0].source_excerpt == "Experience with SQL in production systems."
        assert requirements[0].severity == "IMPORTANT"
        assert requirements[0].dimension == "TECHNICAL"
        assert requirements[0].extraction_confidence == 0.91

    second = worker.run_once(limit=2)
    assert second["claimed"] == 0
    engine.dispose()


def test_worker_retries_failed_extraction_after_backoff():
    engine, session_factory = make_database()
    version_id = seed_description(session_factory)

    def broken_extractor(**_kwargs):
        raise RuntimeError("provider unavailable")

    worker = JobRequirementExtractionWorker(session_factory=session_factory, extractor=broken_extractor)
    result = worker.run_once(limit=1)

    assert result["claimed"] == 1
    assert result["failed"] == 1
    with session_factory() as db:
        version = db.get(JobDescriptionVersion, version_id)
        assert version.requirement_extraction_status == "FAILED"
        assert version.requirement_extraction_retry_at is not None
        assert version.requirement_extraction_error == "RuntimeError"

    engine.dispose()


def test_worker_recovers_in_progress_claim_with_missing_started_at():
    engine, session_factory = make_database()
    version_id = seed_description(session_factory)
    with session_factory() as db:
        version = db.get(JobDescriptionVersion, version_id)
        version.requirement_extraction_status = "IN_PROGRESS"
        version.requirement_extraction_started_at = None
        db.commit()

    def extractor(**_kwargs):
        return [ExtractedRequirement(
            requirement_type="SKILL",
            description="Experience using SQL",
            source_excerpt="Experience with SQL in production systems.",
            importance="HIGH",
            severity="IMPORTANT",
            dimension="TECHNICAL",
            hard_constraint=False,
            confidence=0.91,
        )]

    worker = JobRequirementExtractionWorker(session_factory=session_factory, extractor=extractor)
    result = worker.run_once(limit=1)

    assert result["claimed"] == 1
    assert result["needs_review"] == 1
    with session_factory() as db:
        version = db.get(JobDescriptionVersion, version_id)
        assert version.requirement_extraction_status == "NEEDS_REVIEW"
        assert version.requirement_extraction_attempts == 1
        requirements = db.scalars(
            select(JobRequirement).where(JobRequirement.job_description_version_id == version_id)
        ).all()
        assert len(requirements) == 1
        assert requirements[0].source_excerpt == "Experience with SQL in production systems."

    engine.dispose()


def test_worker_does_not_reclaim_a_fresh_in_progress_claim():
    engine, session_factory = make_database()
    version_id = seed_description(session_factory)
    with session_factory() as db:
        version = db.get(JobDescriptionVersion, version_id)
        version.requirement_extraction_status = "IN_PROGRESS"
        version.requirement_extraction_started_at = datetime.now(timezone.utc)
        db.commit()

    def extractor(**_kwargs):
        raise AssertionError("A fresh extraction lease must not be reclaimed")

    worker = JobRequirementExtractionWorker(session_factory=session_factory, extractor=extractor)
    result = worker.run_once(limit=1)

    assert result["claimed"] == 0
    with session_factory() as db:
        version = db.get(JobDescriptionVersion, version_id)
        assert version.requirement_extraction_status == "IN_PROGRESS"
        assert version.requirement_extraction_attempts == 0

    engine.dispose()


def test_every_usable_description_version_is_extracted_against_its_exact_version_hash():
    engine, session_factory = make_database()
    with session_factory() as db:
        job = Job(canonical_title="Technical Product Manager")
        db.add(job)
        db.flush()
        versions = [
            JobDescriptionVersion(
                job_id=job.id,
                version_number=1,
                content="Experience with SQL in production systems.",
                content_hash="sha-version-one",
                completeness_status="USABLE",
                retrieved_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
            ),
            JobDescriptionVersion(
                job_id=job.id,
                version_number=2,
                content="Experience building Python services.",
                content_hash="sha-version-two",
                completeness_status="USABLE",
                retrieved_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
            ),
            JobDescriptionVersion(
                job_id=job.id,
                version_number=3,
                content="Incomplete description.",
                content_hash="sha-incomplete",
                completeness_status="PARTIAL",
                retrieved_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
            ),
        ]
        db.add_all(versions)
        db.commit()
        version_ids = [version.id for version in versions]

    def extractor(*, title, company, description):
        del title, company
        if "SQL" in description:
            excerpt = "Experience with SQL in production systems."
            description_text = "Experience using SQL in production systems"
        else:
            excerpt = "Experience building Python services."
            description_text = "Experience building Python services"
        return [ExtractedRequirement(
            requirement_type="SKILL",
            description=description_text,
            source_excerpt=excerpt,
            importance="HIGH",
            severity="IMPORTANT",
            dimension="TECHNICAL",
            hard_constraint=False,
            confidence=0.9,
        )]

    worker = JobRequirementExtractionWorker(session_factory=session_factory, extractor=extractor)
    outcome = worker.run_once(limit=10)

    assert outcome["claimed"] == 2
    assert outcome["needs_review"] == 2
    assert outcome["failed"] == 0

    with session_factory() as db:
        rows = db.execute(
            select(JobRequirement, JobDescriptionVersion)
            .join(
                JobDescriptionVersion,
                JobRequirement.job_description_version_id == JobDescriptionVersion.id,
            )
            .where(JobDescriptionVersion.id.in_(version_ids))
        ).all()
        by_hash = {
            version.content_hash: requirement
            for requirement, version in rows
        }
        assert set(by_hash) == {"sha-version-one", "sha-version-two"}
        assert by_hash["sha-version-one"].description == "Experience using SQL in production systems"
        assert by_hash["sha-version-two"].description == "Experience building Python services"
        assert all(requirement.extraction_status == "NEEDS_REVIEW" for requirement in by_hash.values())

        incomplete = db.get(JobDescriptionVersion, version_ids[2])
        assert incomplete.requirement_extraction_status == "PENDING"
        assert db.scalar(
            select(JobRequirement.id).where(
                JobRequirement.job_description_version_id == incomplete.id
            ).limit(1)
        ) is None

    replay = worker.run_once(limit=10)
    assert replay["claimed"] == 0
    engine.dispose()

def test_worker_honors_retry_backoff_then_recovers_without_duplicate_requirements():
    from datetime import timedelta

    engine, session_factory = make_database()
    version_id = seed_description(session_factory)
    calls = {"count": 0}

    def flaky_extractor(**_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("provider unavailable")
        return [ExtractedRequirement(
            requirement_type="SKILL",
            description="Experience using SQL in production systems",
            source_excerpt="Experience with SQL in production systems.",
            importance="HIGH",
            severity="IMPORTANT",
            dimension="TECHNICAL",
            hard_constraint=False,
            confidence=0.9,
        )]

    worker = JobRequirementExtractionWorker(
        session_factory=session_factory,
        extractor=flaky_extractor,
    )
    first = worker.run_once(limit=1)
    assert first["claimed"] == 1
    assert first["failed"] == 1

    during_backoff = worker.run_once(limit=1)
    assert during_backoff["claimed"] == 0
    assert calls["count"] == 1

    with session_factory() as db:
        version = db.get(JobDescriptionVersion, version_id)
        version.requirement_extraction_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    retry = worker.run_once(limit=1)
    assert retry["claimed"] == 1
    assert retry["needs_review"] == 1
    assert retry["failed"] == 0
    assert calls["count"] == 2

    with session_factory() as db:
        version = db.get(JobDescriptionVersion, version_id)
        requirements = db.scalars(
            select(JobRequirement).where(
                JobRequirement.job_description_version_id == version_id
            )
        ).all()
        assert version.requirement_extraction_status == "NEEDS_REVIEW"
        assert version.requirement_extraction_attempts == 2
        assert len(requirements) == 1

    replay = worker.run_once(limit=1)
    assert replay["claimed"] == 0
    with session_factory() as db:
        assert len(db.scalars(
            select(JobRequirement).where(
                JobRequirement.job_description_version_id == version_id
            )
        ).all()) == 1
    engine.dispose()

