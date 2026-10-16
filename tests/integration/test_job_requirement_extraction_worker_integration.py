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
