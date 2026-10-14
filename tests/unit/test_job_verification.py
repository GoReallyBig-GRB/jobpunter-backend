from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.jobs.job_verification import (
    VerificationResult,
    current_application_route_verification,
    record_job_verification,
)


NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


class FakeSession:
    def __init__(self, job):
        self.job = job
        self.added = []
        self.committed = False

    def scalar(self, _statement):
        return self.job

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.committed = True

    def refresh(self, _item):
        pass


def make_job(**overrides):
    values = {
        "id": uuid4(),
        "application_url": "https://jobs.example.com/role/123",
        "last_verified_at": None,
        "lifecycle_status": "DISCOVERED",
        "freshness_status": "UNKNOWN",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_result(**overrides):
    values = {
        "status": "ACTIVE",
        "checked_url": "https://jobs.example.com/role/123",
        "final_url": "https://jobs.example.com/role/123",
        "http_status_code": 200,
        "reason_code": "APPLICATION_PAGE_ACTIVE",
        "evidence": {"page_title": "Technical Product Manager"},
    }
    values.update(overrides)
    return VerificationResult(**values)


def test_active_verification_is_audited_and_refreshes_job():
    job = make_job()
    db = FakeSession(job)

    attempt = record_job_verification(db, job.id, make_result(), now=NOW)

    assert attempt.status == "ACTIVE"
    assert attempt.checked_at == NOW
    assert db.committed is True
    assert job.last_verified_at == NOW
    assert job.lifecycle_status == "ACTIVE"
    assert job.freshness_status == "FRESH"


def test_closed_verification_marks_job_closed():
    job = make_job()
    db = FakeSession(job)

    record_job_verification(
        db, job.id,
        make_result(status="CLOSED", reason_code="APPLICATION_CLOSED"),
        now=NOW,
    )

    assert job.lifecycle_status == "CLOSED"
    assert job.last_verified_at == NOW


def test_unknown_verification_does_not_refresh_job_trust():
    job = make_job()
    db = FakeSession(job)

    record_job_verification(
        db, job.id,
        make_result(status="UNKNOWN", final_url=None, reason_code="TIMEOUT"),
        now=NOW,
    )

    assert job.last_verified_at is None
    assert job.lifecycle_status == "DISCOVERED"
    assert job.freshness_status == "UNKNOWN"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://user:password@jobs.example.com/role/123",
        "/relative/path",
    ],
)
def test_invalid_checked_url_is_rejected(url):
    job = make_job()
    db = FakeSession(job)

    with pytest.raises(ValueError):
        record_job_verification(
            db, job.id, make_result(checked_url=url), now=NOW
        )

    assert db.committed is False


def test_verification_must_check_the_current_application_url():
    job = make_job()
    db = FakeSession(job)

    with pytest.raises(ValueError, match="exactly match"):
        record_job_verification(
            db, job.id,
            make_result(checked_url="https://other.example.com/job"),
            now=NOW,
        )

    assert db.committed is False


def test_active_result_requires_final_url():
    job = make_job()
    db = FakeSession(job)

    with pytest.raises(ValueError, match="final_url"):
        record_job_verification(
            db, job.id, make_result(final_url=None), now=NOW
        )

    assert db.committed is False



class FakeVerificationLookup:
    def __init__(self, attempt):
        self.attempt = attempt

    def scalar(self, _statement):
        return self.attempt


def make_attempt(job, *, checked_at=NOW, status="ACTIVE", checked_url=None):
    return SimpleNamespace(
        id=uuid4(),
        job_id=job.id,
        checked_url=checked_url or job.application_url,
        final_url=job.application_url,
        status=status,
        http_status_code=200,
        reason_code="APPLICATION_PAGE_ACTIVE",
        verifier_version="test-verifier-v1",
        checked_at=checked_at,
    )


def test_current_application_route_verification_requires_recent_matching_active_attempt():
    job = make_job(last_verified_at=NOW)
    attempt = make_attempt(job)
    result = current_application_route_verification(
        FakeVerificationLookup(attempt), job, now=NOW,
    )
    assert result["status"] == "ACTIVE"
    assert result["checked_url"] == job.application_url


@pytest.mark.parametrize(
    ("status", "checked_url", "checked_at", "last_verified_at"),
    [
        ("UNKNOWN", "https://jobs.example.com/role/123", NOW, NOW),
        ("ACTIVE", "https://other.example.com/role", NOW, NOW),
        ("ACTIVE", "https://jobs.example.com/role/123", NOW.replace(day=7), NOW.replace(day=7)),
        ("ACTIVE", "https://jobs.example.com/role/123", NOW, None),
    ],
)
def test_stale_or_mismatched_route_verification_is_not_active(
    status, checked_url, checked_at, last_verified_at
):
    job = make_job(last_verified_at=last_verified_at)
    attempt = make_attempt(job, status=status, checked_url=checked_url, checked_at=checked_at)
    result = current_application_route_verification(
        FakeVerificationLookup(attempt), job, now=NOW,
    )
    assert result["status"] == "UNKNOWN"
