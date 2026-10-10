from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.application.jobs.discovery_evaluation import screen_job_for_discovery
from app.workers.discovery_evaluation_worker import run_worker


NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def make_job(**overrides):
    values = {
        "identity_status": "CANONICAL",
        "canonical_title": "Technical Product Manager",
        "company_name": "Example Ltd",
        "location_country": "Nigeria",
        "application_url": "https://example.com/jobs/123",
        "last_observed_at": NOW - timedelta(days=1),
        "last_verified_at": NOW - timedelta(hours=1),
        "lifecycle_status": "OPEN",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_description(**overrides):
    values = {"completeness_status": "USABLE"}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_fresh_canonical_job_with_verified_route_can_reach_matching():
    result = screen_job_for_discovery(
        make_job(), make_description(), now=NOW, max_job_age_days=21,
        verification_required=True,
    )
    assert result.eligible_for_matching is True
    assert result.reasons == ()


def test_explicitly_closed_job_is_not_qualified_even_if_recently_verified():
    result = screen_job_for_discovery(
        make_job(lifecycle_status="CLOSED"), make_description(), now=NOW,
        max_job_age_days=21, verification_required=True,
    )
    assert result.outcome == "NOT_QUALIFIED"
    assert "JOB_CLOSED" in result.reasons


def test_unverified_job_is_review_not_qualified():
    result = screen_job_for_discovery(
        make_job(last_verified_at=None), make_description(), now=NOW,
        max_job_age_days=21, verification_required=True,
    )
    assert result.eligible_for_matching is False
    assert result.outcome == "NEEDS_REVIEW"
    assert "JOB_NOT_VERIFIED" in result.reasons


def test_stale_job_is_not_qualified():
    result = screen_job_for_discovery(
        make_job(last_observed_at=NOW - timedelta(days=22)), make_description(),
        now=NOW, max_job_age_days=21, verification_required=False,
    )
    assert result.outcome == "NOT_QUALIFIED"
    assert "JOB_STALE" in result.reasons


def test_incomplete_description_is_review():
    result = screen_job_for_discovery(
        make_job(), make_description(completeness_status="INCOMPLETE"),
        now=NOW, max_job_age_days=21, verification_required=False,
    )
    assert result.outcome == "NEEDS_REVIEW"
    assert "JOB_DESCRIPTION_INCOMPLETE" in result.reasons


def test_missing_application_url_is_review():
    result = screen_job_for_discovery(
        make_job(application_url=None), make_description(), now=NOW,
        max_job_age_days=21, verification_required=False,
    )
    assert result.outcome == "NEEDS_REVIEW"
    assert "APPLICATION_URL_MISSING" in result.reasons


def test_missing_employer_is_review_not_automatic_disqualification():
    result = screen_job_for_discovery(
        make_job(company_name=None), make_description(), now=NOW,
        max_job_age_days=21, verification_required=False,
    )
    assert result.eligible_for_matching is False
    assert result.outcome == "NEEDS_REVIEW"
    assert "COMPANY_NAME_MISSING" in result.reasons


def test_missing_location_and_work_mode_is_review_not_automatic_disqualification():
    result = screen_job_for_discovery(
        make_job(
            location_country=None,
            location_region=None,
            location_city=None,
            original_location_text=None,
            work_mode=None,
            remote_eligibility=None,
        ),
        make_description(),
        now=NOW,
        max_job_age_days=21,
        verification_required=False,
    )
    assert result.eligible_for_matching is False
    assert result.outcome == "NEEDS_REVIEW"
    assert "LOCATION_OR_WORK_MODE_UNKNOWN" in result.reasons


def test_evaluation_worker_once_runs_one_cycle():
    class FakeWorker:
        calls = 0

        def run_once(self, limit):
            self.calls += 1
            assert limit == 3
            return {"claimed": 0, "completed": 0, "failed": 0}

    fake = FakeWorker()
    sleeps = []
    cycles = run_worker(
        once=True, batch_size=3, worker=fake, sleep=sleeps.append,
        should_stop=lambda: False,
    )
    assert cycles == 1
    assert fake.calls == 1
    assert sleeps == []


def test_evaluation_worker_rejects_invalid_limits():
    with pytest.raises(ValueError):
        run_worker(interval_seconds=0)
    with pytest.raises(ValueError):
        run_worker(batch_size=0)
