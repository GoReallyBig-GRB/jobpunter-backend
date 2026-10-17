import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker

from app.infrastructure.ai.job_requirement_extractor import ExtractedRequirement
from app.infrastructure.db.models import Job, JobDescriptionVersion, JobRequirement
from app.workers.job_requirement_extraction_worker import JobRequirementExtractionWorker


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


def test_postgres_extraction_skips_locked_expired_version_then_recovers_once():
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=4)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    version_id = None
    job_id = None
    now = datetime.now(timezone.utc)

    def extractor(**_kwargs):
        return [ExtractedRequirement(
            requirement_type="SKILL",
            description="Experience with SQL in production systems",
            source_excerpt="Experience with SQL in production systems.",
            importance="HIGH",
            severity="IMPORTANT",
            dimension="TECHNICAL",
            hard_constraint=False,
            confidence=0.9,
        )]

    worker = JobRequirementExtractionWorker(
        session_factory=session_factory,
        extractor=extractor,
    )

    try:
        with session_factory() as db:
            job = Job(canonical_title=f"Extraction lease test {uuid4()}")
            db.add(job)
            db.flush()
            version = JobDescriptionVersion(
                job_id=job.id,
                version_number=1,
                content="Experience with SQL in production systems.",
                content_hash=f"sha-{uuid4()}",
                completeness_status="USABLE",
                retrieved_at=now - timedelta(days=1),
                requirement_extraction_status="IN_PROGRESS",
                requirement_extraction_attempts=1,
                requirement_extraction_started_at=now - timedelta(minutes=10),
            )
            db.add(version)
            db.commit()
            job_id = job.id
            version_id = version.id

        # Simulate another worker holding the expired row while recovery attempts
        # to claim it. The SKIP LOCKED query must not block or double-claim it.
        with session_factory() as locking_session:
            locked_version = locking_session.scalar(
                select(JobDescriptionVersion)
                .where(JobDescriptionVersion.id == version_id)
                .with_for_update()
            )
            assert locked_version is not None

            skipped = worker.run_once(limit=1)
            assert skipped["claimed"] == 0

            locking_session.commit()

        recovered = worker.run_once(limit=1)
        assert recovered["claimed"] == 1
        assert recovered["needs_review"] == 1
        assert recovered["failed"] == 0

        with session_factory() as db:
            version = db.get(JobDescriptionVersion, version_id)
            requirements = db.scalars(
                select(JobRequirement).where(
                    JobRequirement.job_description_version_id == version_id
                )
            ).all()
            assert version is not None
            assert version.requirement_extraction_status == "NEEDS_REVIEW"
            assert version.requirement_extraction_attempts == 2
            assert len(requirements) == 1
            assert requirements[0].extraction_status == "NEEDS_REVIEW"

        replay = worker.run_once(limit=1)
        assert replay["claimed"] == 0
    finally:
        with session_factory() as db:
            if version_id is not None:
                db.execute(delete(JobRequirement).where(
                    JobRequirement.job_description_version_id == version_id
                ))
                db.execute(delete(JobDescriptionVersion).where(
                    JobDescriptionVersion.id == version_id
                ))
            if job_id is not None:
                db.execute(delete(Job).where(Job.id == job_id))
            db.commit()
        engine.dispose()
