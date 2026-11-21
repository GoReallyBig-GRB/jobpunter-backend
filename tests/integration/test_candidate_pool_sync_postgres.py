"""Candidate pool sync from the central job pool: database-only, no connector or network call."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.application.jobs.candidate_pool_sync import sync_candidate_pool
from app.application.jobs.discovery_missions import create_discovery_mission
from app.infrastructure.db.models import (
    AcquisitionRun, CandidatePoolEntry, CareerPathway, DiscoveryMission, DiscoveryMissionSourceRun,
    Job, JobProvenance, Source, WorkItem,
)
from tests.integration.test_discovery_api_postgres import _client_for, db  # noqa: F401
from tests.integration.test_discovery_missions_postgres import _candidate

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="PostgreSQL integration test requires DATABASE_URL",
)

NOW = datetime.now(timezone.utc)


def _public_source(db):
    source = Source(
        name=f"Pool source {uuid4()}", source_type="GREENHOUSE_JOB_BOARD", status="ACTIVE",
        permission_review_status="APPROVED", permission_review_reference="test-policy://pool",
        permission_reviewed_at=NOW, operational_state="AVAILABLE",
    )
    db.add(source)
    db.flush()
    return source


def _job(db, source, title, *, lifecycle="ACTIVE", freshness="FRESH", observed=None, country="Nigeria",
         work_mode="ONSITE", city="Lagos"):
    job = Job(
        canonical_title=title, company_name="Pool Co", lifecycle_status=lifecycle,
        freshness_status=freshness, identity_status="CANONICAL",
        application_url=f"https://example.test/{uuid4()}", location_country=country, location_city=city,
        original_location_text=f"{city}, {country}" if city else country, work_mode=work_mode,
        last_observed_at=observed or NOW, last_verified_at=NOW, metadata_json={}, location_restrictions={},
    )
    db.add(job)
    db.flush()
    db.add(JobProvenance(
        job_id=job.id, source_id=source.id, raw_source_record_id=None, submitted_by_user_id=None,
        source_url=job.application_url, normalized_source_url=job.application_url,
        observation_type="ACQUIRED", asserted_by="SOURCE", observed_at=NOW, confidence=1.0,
        status="OBSERVED", metadata_json={},
    ))
    db.flush()
    return job


def _sync(db, profile, roles=("Product Manager",), **kw):
    return sync_candidate_pool(db, candidate_profile_id=profile.id, target_roles=list(roles), now=NOW, **kw)


def _entries(db, profile, status=None):
    query = select(CandidatePoolEntry).where(CandidatePoolEntry.candidate_profile_id == profile.id)
    if status:
        query = query.where(CandidatePoolEntry.status == status)
    return db.scalars(query).all()


def test_matching_open_recent_jobs_enter_the_pool_and_others_do_not(db):
    _user, profile = _candidate(db)
    source = _public_source(db)
    match = _job(db, source, "Senior Product Manager")
    _job(db, source, "Backend Engineer")
    _job(db, source, "Product Manager", lifecycle="CLOSED")
    _job(db, source, "Product Manager", freshness="STALE")
    _job(db, source, "Product Manager", observed=NOW - timedelta(days=60))
    result = _sync(db, profile)
    assert (result.new_entries, result.closed_entries) == (1, 0)
    entries = _entries(db, profile)
    assert [e.job_id for e in entries] == [match.id]
    assert entries[0].status == "ACTIVE" and entries[0].match_basis["roles"] == ["Product Manager"]


def test_sync_is_idempotent(db):
    _user, profile = _candidate(db)
    _job(db, _public_source(db), "Product Manager")
    assert _sync(db, profile).new_entries == 1
    again = _sync(db, profile)
    assert again.new_entries == 0 and again.refreshed_entries == 1
    assert len(_entries(db, profile)) == 1


def test_entry_closes_when_central_job_closes_and_reopens_if_it_returns(db):
    _user, profile = _candidate(db)
    job = _job(db, _public_source(db), "Product Manager")
    _sync(db, profile)
    job.lifecycle_status = "CLOSED"
    db.flush()
    assert _sync(db, profile).closed_entries == 1
    entry = _entries(db, profile)[0]
    assert entry.status == "CLOSED" and entry.closed_at is not None
    job.lifecycle_status = "ACTIVE"
    db.flush()
    _sync(db, profile)
    assert _entries(db, profile)[0].status == "ACTIVE"


def test_unapproved_source_jobs_are_invisible(db):
    _user, profile = _candidate(db)
    source = _public_source(db)
    source.permission_review_status = "PENDING"
    db.flush()
    _job(db, source, "Product Manager")
    assert _sync(db, profile).new_entries == 0


def test_other_candidates_manual_submissions_are_never_synced(db):
    owner, _owner_profile = _candidate(db)
    _user, profile = _candidate(db)
    manual = Source(name=f"Manual {uuid4()}", source_type="MANUAL_SUBMISSION", status="ACTIVE",
                    permission_review_status="NOT_REQUIRED", operational_state="AVAILABLE")
    db.add(manual)
    db.flush()
    job = _job(db, manual, "Product Manager")
    prov = db.scalar(select(JobProvenance).where(JobProvenance.job_id == job.id))
    prov.submitted_by_user_id = owner.id
    db.flush()
    assert _sync(db, profile).new_entries == 0


def test_no_target_roles_matches_nothing(db):
    _user, profile = _candidate(db)
    _job(db, _public_source(db), "Product Manager")
    assert _sync(db, profile, roles=()).new_entries == 0


def test_location_preference_keeps_remote_and_unknown_but_drops_other_places(db):
    _user, profile = _candidate(db)
    source = _public_source(db)
    local = _job(db, source, "Product Manager", country="Nigeria", city="Lagos")
    remote = _job(db, source, "Product Manager", country="Germany", city="Berlin", work_mode="REMOTE")
    unknown = _job(db, source, "Product Manager", country="", city="")
    _job(db, source, "Product Manager", country="Germany", city="Berlin")
    _sync(db, profile, preferences={"preferredLocations": ["Nigeria"]})
    assert {e.job_id for e in _entries(db, profile)} == {local.id, remote.id, unknown.id}


def test_new_entries_are_capped_per_sync_and_remaining_ones_arrive_next_sync(db):
    _user, profile = _candidate(db)
    source = _public_source(db)
    for _ in range(5):
        _job(db, source, "Product Manager")
    first = _sync(db, profile, max_new_entries=3)
    assert (first.new_entries, first.capped, first.matched_total) == (3, True, 5)
    second = _sync(db, profile, max_new_entries=3)
    assert second.new_entries == 2 and not second.capped
    assert len(_entries(db, profile)) == 5


def test_role_text_with_like_wildcards_does_not_match_everything(db):
    _user, profile = _candidate(db)
    _job(db, _public_source(db), "Product Manager")
    assert _sync(db, profile, roles=("%",)).new_entries == 0


def test_pool_only_mission_completes_without_connectors_or_network(db, monkeypatch):
    from app.workers import discovery_mission_worker as worker

    _user, profile = _candidate(db)
    db.add(CareerPathway(
        candidate_profile_id=profile.id, title="PM", role_family="Product", target_roles=["Product Manager"],
        target_seniority=[], domains=[], supporting_evidence_refs=[], gaps=[], rationale="x",
        confidence=None, source_type="USER_DEFINED", approval_status="APPROVED",
    ))
    db.flush()
    _job(db, _public_source(db), "Product Manager")
    mission = create_discovery_mission(db, candidate_profile_id=profile.id, idempotency_key=f"pool-{uuid4()}")
    assert mission.policy_snapshot["pool_only"] is True

    def forbidden(*_a, **_k):
        raise AssertionError("pool-only mission must not touch connectors")

    monkeypatch.setattr(worker, "RegisteredAcquisitionService", forbidden)
    monkeypatch.setattr(worker, "_eligible_sources", forbidden)
    status = worker.process_mission_step(db, mission.id)
    db.refresh(mission)
    assert status == mission.status == "EXHAUSTED"  # 1 match is below the default target
    assert mission.cumulative_usable_opportunities == 1
    work_item = db.scalar(select(WorkItem).where(WorkItem.resource_id == mission.id,
                                                 WorkItem.work_type == "DISCOVERY_MISSION"))
    assert work_item.status == "COMPLETED" and work_item.result["mode"] == "POOL_ONLY"
    assert work_item.result["pool_sync"]["new_entries"] == 1
    assert db.scalar(select(func.count()).select_from(DiscoveryMissionSourceRun)
                     .where(DiscoveryMissionSourceRun.mission_id == mission.id)) == 0


def test_pool_only_mission_without_approved_pathways_reports_no_target_roles(db):
    from app.workers import discovery_mission_worker as worker

    _user, profile = _candidate(db)
    mission = create_discovery_mission(db, candidate_profile_id=profile.id, idempotency_key=f"pool-{uuid4()}")
    worker.process_mission_step(db, mission.id)
    db.refresh(mission)
    assert mission.error_code == "NO_TARGET_ROLES"


def test_pool_endpoint_lists_only_the_callers_entries(db):
    user, profile = _candidate(db)
    other_user, other_profile = _candidate(db)
    source = _public_source(db)
    _job(db, source, "Product Manager")
    _sync(db, profile)
    _sync(db, other_profile)
    client = _client_for(db, user)
    body = client.get("/api/v1/candidate/pool").json()
    assert body["pagination"]["total"] == 1
    assert body["data"][0]["title"] == "Product Manager" and body["data"][0]["status"] == "ACTIVE"
    assert client.get("/api/v1/candidate/pool?status=CLOSED").json()["pagination"]["total"] == 0
    assert client.get("/api/v1/candidate/pool?status=BOGUS").status_code == 422
