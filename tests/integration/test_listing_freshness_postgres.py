"""Listing-based freshness: confirm listed jobs, close after two complete misses, never on doubt."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.application.jobs.listing_freshness import (
    CLOSED_REASON, apply_listing_freshness, is_full_listing_connector,
)
from app.infrastructure.db.models import (
    AcquisitionRun, Job, JobDiscoveryEvent, JobProvenance, RawSourceRecord, Source, SourceConnector,
)
from tests.integration.test_admin_governance_postgres import db  # noqa: F401

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="PostgreSQL integration test requires DATABASE_URL"
)
LEVER = {"endpoint_url": "https://api.lever.co/v0/postings/acme", "base_params": {"mode": "json"}}
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def _world(db, external_ids):  # noqa: F811
    source = Source(name=f"fresh-{uuid4()}", source_type="TEST", status="ACTIVE")
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id, connector_type="JSON_API", connector_version="1.0.0",
        status="ACTIVE", configuration=LEVER,
    )
    db.add(connector)
    db.flush()
    first_run = _run(db, source, connector)
    jobs = {}
    for ext in external_ids:
        job = Job(canonical_title=f"Job {ext}", company_name="Acme", application_url=f"https://x.test/{ext}",
                  lifecycle_status="ACTIVE", freshness_status="UNKNOWN")
        db.add(job)
        db.flush()
        raw = RawSourceRecord(
            acquisition_run_id=first_run.id, source_id=source.id, external_record_id=ext,
            deduplication_key=f"id:{ext}", raw_content={"id": ext}, content_hash=f"h-{ext}-{uuid4()}",
        )
        db.add(raw)
        db.flush()
        db.add(JobProvenance(
            job_id=job.id, source_id=source.id, raw_source_record_id=raw.id, observation_type="ACQUIRED",
            asserted_by="test", observed_at=NOW, status="ACTIVE",
        ))
        jobs[ext] = job
    db.flush()
    return source, connector, jobs


def _run(db, source, connector):  # noqa: F811
    run = AcquisitionRun(source_id=source.id, source_connector_id=connector.id, run_key=f"k-{uuid4()}",
                         status="SUCCEEDED")
    db.add(run)
    db.flush()
    return run


def _listing(db, source, connector, listed, jobs, **overrides):  # noqa: F811
    run = _run(db, source, connector)
    for ext in listed:
        raw = db.query(RawSourceRecord).filter_by(source_id=source.id, external_record_id=ext).first()
        db.add(JobDiscoveryEvent(
            source_id=source.id, acquisition_run_id=run.id, raw_source_record_id=raw.id,
            external_record_id=ext, content_hash=raw.content_hash, observed_at=NOW,
            observation_outcome="UNCHANGED", processing_status="PENDING", metadata_json={},
        ))
    db.flush()
    args = dict(source_id=source.id, run_id=run.id, run_status="SUCCEEDED",
                connector_type=connector.connector_type, connector_configuration=connector.configuration, now=NOW)
    args.update(overrides)
    outcome = apply_listing_freshness(db, **args)
    for job in jobs.values():
        db.refresh(job)
    return outcome


def test_listed_jobs_are_confirmed_fresh_and_verified(db):  # noqa: F811
    source, connector, jobs = _world(db, ["a", "b"])
    outcome = _listing(db, source, connector, ["a", "b"], jobs)
    assert outcome.applied and outcome.confirmed == 2
    for job in jobs.values():
        assert job.freshness_status == "FRESH" and job.lifecycle_status == "ACTIVE"
        assert job.last_verified_at == NOW and job.last_observed_at == NOW


def test_job_closes_only_after_two_consecutive_complete_misses_and_reopens_on_return(db):  # noqa: F811
    source, connector, jobs = _world(db, ["a", "b"])
    _listing(db, source, connector, ["a", "b"], jobs)
    _listing(db, source, connector, ["a"], jobs)
    assert jobs["b"].lifecycle_status == "ACTIVE" and jobs["b"].freshness_status == "STALE"
    outcome = _listing(db, source, connector, ["a"], jobs)
    assert outcome.closed == 1 and jobs["b"].lifecycle_status == "CLOSED"
    assert jobs["b"].metadata_json["closed_reason"] == CLOSED_REASON
    assert jobs["a"].lifecycle_status == "ACTIVE"
    outcome = _listing(db, source, connector, ["a", "b"], jobs)
    assert outcome.reopened == 1 and jobs["b"].lifecycle_status == "ACTIVE"
    assert jobs["b"].freshness_status == "FRESH" and "closed_reason" not in jobs["b"].metadata_json


def test_a_seen_job_resets_the_miss_counter(db):  # noqa: F811
    source, connector, jobs = _world(db, ["a", "b"])
    _listing(db, source, connector, ["a"], jobs)
    _listing(db, source, connector, ["a", "b"], jobs)
    _listing(db, source, connector, ["a"], jobs)
    assert jobs["b"].lifecycle_status == "ACTIVE"


@pytest.mark.parametrize("override,reason", [
    ({"run_status": "PARTIAL"}, "RUN_NOT_SUCCEEDED"),
    ({"connector_type": "PUBLIC_HTTP"}, "LISTING_NOT_KNOWN_COMPLETE"),
])
def test_partial_runs_and_unknown_completeness_never_change_jobs(db, override, reason):  # noqa: F811
    source, connector, jobs = _world(db, ["a", "b"])
    outcome = _listing(db, source, connector, ["a"], jobs, **override)
    assert not outcome.applied and outcome.reason == reason
    assert all(j.freshness_status == "UNKNOWN" and j.lifecycle_status == "ACTIVE" for j in jobs.values())


def test_empty_listing_never_closes_anything(db):  # noqa: F811
    source, connector, jobs = _world(db, ["a"])
    for _ in range(3):
        outcome = _listing(db, source, connector, [], jobs)
    assert outcome.reason == "EMPTY_LISTING" and jobs["a"].lifecycle_status == "ACTIVE"


def test_job_also_provided_by_another_source_is_not_closed_by_this_one(db):  # noqa: F811
    source, connector, jobs = _world(db, ["a", "b"])
    other = Source(name=f"other-{uuid4()}", source_type="TEST", status="ACTIVE")
    db.add(other)
    db.flush()
    db.add(JobProvenance(job_id=jobs["b"].id, source_id=other.id, observation_type="ACQUIRED",
                         asserted_by="test", observed_at=NOW, status="ACTIVE"))
    db.flush()
    for _ in range(3):
        _listing(db, source, connector, ["a"], jobs)
    assert jobs["b"].lifecycle_status == "ACTIVE" and jobs["b"].freshness_status == "STALE"


def test_manually_terminal_jobs_are_left_alone(db):  # noqa: F811
    source, connector, jobs = _world(db, ["a"])
    jobs["a"].lifecycle_status = "REMOVED"
    db.flush()
    _listing(db, source, connector, ["a"], jobs)
    assert jobs["a"].lifecycle_status == "REMOVED"


def test_full_listing_endpoint_rules():
    assert is_full_listing_connector("JSON_API", LEVER)
    assert is_full_listing_connector("GREENHOUSE_JOB_BOARD", {})
    assert is_full_listing_connector("JSON_API", {"endpoint_url": "https://boards-api.greenhouse.io/v1/boards/x/jobs"})
    assert is_full_listing_connector("JSON_API", {"endpoint_url": "https://api.ashbyhq.com/posting-api/job-board/x"})
    assert not is_full_listing_connector("JSON_API", {**LEVER, "base_params": {"limit": 20}})
    assert not is_full_listing_connector("JSON_API", {**LEVER, "page_param": "page"})
    assert not is_full_listing_connector("JSON_API", {"endpoint_url": "http://api.lever.co/v0/postings/x"})
    assert not is_full_listing_connector("JSON_API", {"endpoint_url": "https://evil.test/v0/postings/x"})
    assert not is_full_listing_connector("PLAYWRIGHT", LEVER)
