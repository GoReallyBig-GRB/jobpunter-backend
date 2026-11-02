from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.application.jobs.job_verification import STALE_AFTER, classify_job_liveness

NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def job(**kw):
    values = dict(lifecycle_status="ACTIVE", freshness_status="FRESH", last_verified_at=NOW - timedelta(hours=1))
    values.update(kw)
    return SimpleNamespace(**values)


def test_recently_verified_active_job_is_verified_live():
    assert classify_job_liveness(job(), now=NOW) == "VERIFIED_LIVE"


@pytest.mark.parametrize("lifecycle", ["CLOSED", "EXPIRED", "REMOVED"])
def test_terminal_lifecycle_is_unavailable_even_if_fresh(lifecycle):
    assert classify_job_liveness(job(lifecycle_status=lifecycle), now=NOW) == "UNAVAILABLE"


def test_stale_freshness_is_stale():
    assert classify_job_liveness(job(freshness_status="STALE"), now=NOW) == "STALE"


def test_fresh_label_with_old_verification_is_not_trusted():
    old = NOW - STALE_AFTER - timedelta(minutes=1)
    assert classify_job_liveness(job(last_verified_at=old), now=NOW) == "STALE"


@pytest.mark.parametrize("kw", [
    dict(freshness_status="UNKNOWN"),
    dict(last_verified_at=None),
    dict(lifecycle_status="DISCOVERED"),
])
def test_unverified_jobs_are_unable_to_verify(kw):
    assert classify_job_liveness(job(**kw), now=NOW) == "UNABLE_TO_VERIFY"


def test_naive_timestamp_is_treated_as_utc():
    naive = (NOW - timedelta(hours=2)).replace(tzinfo=None)
    assert classify_job_liveness(job(last_verified_at=naive), now=NOW) == "VERIFIED_LIVE"
