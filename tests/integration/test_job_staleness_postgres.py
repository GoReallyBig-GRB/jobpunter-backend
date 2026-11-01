"""F1.6: FRESH jobs that are not re-verified within the window become STALE (PRD 15.5, 45)."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.application.jobs.job_verification import (
    STALE_AFTER,
    VERIFICATION_FRESHNESS_WINDOW,
    VerificationResult,
    mark_stale_jobs,
    record_job_verification,
)
from app.infrastructure.db.models import Job

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


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


def _job(db, *, freshness="FRESH", lifecycle="ACTIVE", verified_at=None):
    job = Job(
        canonical_title="Stale probe", company_name="Example",
        lifecycle_status=lifecycle, freshness_status=freshness,
        application_url="https://example.test/apply",
        last_verified_at=verified_at, metadata_json={}, location_restrictions={},
    )
    db.add(job)
    db.flush()
    return job


def test_window_is_longer_than_reverify_interval():
    assert STALE_AFTER > VERIFICATION_FRESHNESS_WINDOW


def test_fresh_job_past_window_becomes_stale_and_is_counted(db):
    old = _job(db, verified_at=NOW - STALE_AFTER - timedelta(minutes=1))
    assert mark_stale_jobs(db, now=NOW) >= 1
    db.refresh(old)
    assert old.freshness_status == "STALE"
    assert old.lifecycle_status == "ACTIVE"  # stale is not closed


def test_recently_verified_fresh_job_is_untouched(db):
    recent = _job(db, verified_at=NOW - STALE_AFTER + timedelta(minutes=1))
    mark_stale_jobs(db, now=NOW)
    db.refresh(recent)
    assert recent.freshness_status == "FRESH"


def test_fresh_job_with_no_verification_timestamp_cannot_stay_fresh(db):
    job = _job(db, verified_at=None)
    mark_stale_jobs(db, now=NOW)
    db.refresh(job)
    assert job.freshness_status == "STALE"


@pytest.mark.parametrize("freshness", ["UNKNOWN", "STALE"])
def test_non_fresh_jobs_are_not_rewritten(db, freshness):
    job = _job(db, freshness=freshness, verified_at=NOW - timedelta(days=30))
    before = job.revision if hasattr(job, "revision") else None
    mark_stale_jobs(db, now=NOW)
    db.refresh(job)
    assert job.freshness_status == freshness
    if before is not None:
        assert job.revision == before


@pytest.mark.parametrize("lifecycle", ["CLOSED", "EXPIRED", "REMOVED"])
def test_terminal_jobs_keep_their_verified_state(db, lifecycle):
    job = _job(db, lifecycle=lifecycle, verified_at=NOW - timedelta(days=30))
    mark_stale_jobs(db, now=NOW)
    db.refresh(job)
    assert job.freshness_status == "FRESH"


def test_sweep_is_idempotent(db):
    _job(db, verified_at=NOW - timedelta(days=10))
    mark_stale_jobs(db, now=NOW)
    assert mark_stale_jobs(db, now=NOW) == 0


def test_successful_reverification_restores_fresh(db):
    job = _job(db, freshness="STALE", verified_at=NOW - timedelta(days=10))
    record_job_verification(
        db, job.id,
        VerificationResult(
            status="ACTIVE", checked_url=job.application_url, final_url=job.application_url,
            http_status_code=200, reason_code="APPLICATION_PAGE_ACTIVE", evidence={},
        ),
        now=NOW, commit=False,
    )
    db.refresh(job)
    assert job.freshness_status == "FRESH"
    assert mark_stale_jobs(db, now=NOW) == 0
