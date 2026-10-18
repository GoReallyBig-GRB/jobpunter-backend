import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.infrastructure.db.models import (
    AcquisitionRun, AdminAuditEvent, AdminGrant, Job, JobDescriptionVersion,
    JobDiscoveryEvent, JobProvenance, JobResolution, JobResolutionCandidate,
    RawSourceRecord, Source, SourceConnector, User,
)
from app.infrastructure.db.session import get_db
from app.main import app
from app.ports.identity import AuthenticatedPrincipal


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
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def make_user(db, *, admin: bool):
    user = User(display_name=f"Identity review {uuid4()}")
    db.add(user)
    db.flush()
    if admin:
        db.add(AdminGrant(user_id=user.id, role="ADMIN", active=True))
        db.flush()
    return user


def make_client(db, user):
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=user.id, auth_provider="test",
    )
    connection = db.connection()

    def override_db():
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def make_review_case(
    db, *, title, company, description, include_secret=False,
    source_type="GREENHOUSE_JOB_BOARD", submitted_by_user_id=None,
):
    now = datetime.now(timezone.utc)
    source = Source(
        name=f"Identity review source {uuid4()}",
        source_type=source_type,
        base_url="https://boards.example.test",
        status="ACTIVE",
        permission_review_status="APPROVED",
        permission_review_reference="test-policy-review://identity",
        permission_reviewed_at=now,
        operational_state="AVAILABLE",
        configuration={},
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id,
        connector_type="GREENHOUSE_JOB_BOARD",
        connector_version="identity-review-v1",
        status="ACTIVE",
        configuration={"application_route_type": "FREE_ATS"},
        capabilities={"published_jobs": True},
        limits={},
        created_at=now,
        updated_at=now,
    )
    db.add(connector)
    db.flush()
    run = AcquisitionRun(
        source_id=source.id,
        source_connector_id=connector.id,
        run_key=f"identity-review-{uuid4()}",
        status="SUCCEEDED",
        started_at=now,
        completed_at=now,
        lease_expires_at=None,
        metrics={"acquired_count": 1, "persisted_count": 1},
        created_at=now,
    )
    db.add(run)
    db.flush()
    raw_content = {
        "title": title,
        "company_name": company,
        "application_url": "https://jobs.example.test/apply",
        "location_country": "US",
        "location_city": "Boston",
        "employment_type": "FULL_TIME",
        "salary_min": 120000,
        "salary_max": 150000,
        "salary_currency": "USD",
        "salary_period": "YEAR",
        "seniority": "SENIOR",
        "job_description": description,
        "external_job_id": f"external-{uuid4()}",
        "posting_date": "2026-10-10",
    }
    if submitted_by_user_id is not None:
        raw_content["submitted_by_user_id"] = str(submitted_by_user_id)
    if include_secret:
        raw_content["api_token"] = "must-not-leak"
    raw = RawSourceRecord(
        acquisition_run_id=run.id,
        source_id=source.id,
        external_record_id=raw_content["external_job_id"],
        deduplication_key=f"identity-review-{uuid4()}",
        source_url="https://boards.example.test/jobs/role",
        raw_content=raw_content,
        content_hash=f"identity-content-{uuid4()}",
        status="ACQUIRED",
        acquired_at=now,
        created_at=now,
    )
    db.add(raw)
    db.flush()
    candidate_jobs = []
    for suffix in ("A", "B"):
        candidate = Job(
            identity_status="CANONICAL",
            lifecycle_status="DISCOVERED",
            freshness_status="UNKNOWN",
            canonical_title=f"{title} Candidate {suffix}",
            company_name=company,
            location_country="US",
            location_city="Boston",
            application_url=f"https://jobs.example.test/{suffix.lower()}",
            location_restrictions={},
            first_discovered_at=now,
            last_observed_at=now,
            metadata_json={"identity_fingerprint": f"candidate-{suffix}-{uuid4()}"},
        )
        db.add(candidate)
        candidate_jobs.append(candidate)
    db.flush()
    resolution = JobResolution(
        raw_source_record_id=raw.id,
        job_id=None,
        resolution_type="RESOLVE",
        resolution_method="URL_IDENTITY_COLLISION",
        confidence=1.0,
        resolver_version="exact-identity-v1",
        status="REQUIRES_REVIEW",
        resolved_at=now,
        created_at=now,
    )
    db.add(resolution)
    db.flush()
    db.add_all([
        JobResolutionCandidate(
            resolution_id=resolution.id,
            candidate_job_id=candidate.id,
            confidence=0.7 if index == 0 else 0.65,
            method="EXACT_APPLICATION_URL",
            created_at=now,
        )
        for index, candidate in enumerate(candidate_jobs)
    ])
    event = JobDiscoveryEvent(
        source_id=source.id,
        acquisition_run_id=run.id,
        raw_source_record_id=raw.id,
        canonical_job_id=None,
        external_record_id=raw.external_record_id,
        source_url=raw.source_url,
        content_hash=raw.content_hash,
        observed_at=now,
        observation_outcome="NEW",
        processing_status="REQUIRES_REVIEW",
        processing_error="URL_IDENTITY_COLLISION",
        metadata_json={},
        created_at=now,
    )
    db.add(event)
    db.commit()
    return source, raw, resolution, candidate_jobs, event


def test_admin_can_review_and_resolve_ambiguous_job_to_existing_candidate_with_audit(db):
    user = make_user(db, admin=True)
    _source, raw, resolution, candidates, event = make_review_case(
        db, title="Technical Product Manager", company="Example Company",
        description=("Coordinate delivery and product outcomes across teams. " * 8),
        include_secret=True,
    )
    client = make_client(db, user)
    try:
        listing = client.get("/api/v1/admin/job-resolutions?limit=10&offset=0")
        assert listing.status_code == 200, listing.text
        assert listing.json()["pagination"]["total"] == 1
        assert listing.json()["data"][0]["resolution_id"] == str(resolution.id)
        assert {item["job_id"] for item in listing.json()["data"][0]["candidates"]} == {
            str(candidate.id) for candidate in candidates
        }
        detail = client.get(f"/api/v1/admin/job-resolutions/{resolution.id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["raw_record"]["raw_content"]["api_token"] == "[REDACTED]"
        assert "must-not-leak" not in detail.text

        reason = "Confirmed the existing vacancy using the source record and application details."
        resolved = client.post(
            f"/api/v1/admin/job-resolutions/{resolution.id}/resolve",
            headers={"x-request-id": "identity-resolution-match"},
            json={"action": "MATCH_EXISTING", "job_id": str(candidates[0].id), "reason": reason},
        )
        assert resolved.status_code == 200, resolved.text
        result = resolved.json()["data"]
        assert result["status"] == "MATCHED"
        assert result["job_id"] == str(candidates[0].id)
        assert result["superseded_resolution_id"] == str(resolution.id)

        db.expire_all()
        db.refresh(resolution)
        assert resolution.superseded_at is not None
        current = db.get(JobResolution, UUID(result["resolution_id"]))
        assert current.status == "MATCHED"
        assert current.related_resolution_id == resolution.id
        assert current.job_id == candidates[0].id
        provenance = db.scalar(select(JobProvenance).where(JobProvenance.raw_source_record_id == raw.id))
        assert provenance is not None and provenance.job_id == candidates[0].id
        db.refresh(candidates[0])
        assert candidates[0].salary_min == 120000
        assert candidates[0].salary_max == 150000
        assert candidates[0].salary_currency == "USD"
        assert candidates[0].salary_period == "YEAR"
        assert candidates[0].metadata_json["seniority"] == "SENIOR"
        description_version = db.scalar(select(JobDescriptionVersion).where(
            JobDescriptionVersion.job_id == candidates[0].id,
        ))
        assert description_version is not None
        assert description_version.source_record_id == raw.id
        db.refresh(event)
        assert event.canonical_job_id == candidates[0].id
        assert event.processing_status == "CANONICALIZED"
        assert event.processing_error is None
        audit = db.scalar(select(AdminAuditEvent).where(
            AdminAuditEvent.action == "JOB_IDENTITY_RESOLUTION_DECIDED",
            AdminAuditEvent.resource_id == current.id,
        ))
        assert audit is not None
        assert audit.actor_user_id == user.id
        assert audit.request_id == "identity-resolution-match"
        assert audit.details["before"]["status"] == "REQUIRES_REVIEW"
        assert audit.details["after"]["status"] == "MATCHED"
        assert audit.details["reason"] == reason
        remaining = client.get("/api/v1/admin/job-resolutions")
        assert remaining.status_code == 200
        assert remaining.json()["pagination"]["total"] == 0
    finally:
        client.close()


def test_admin_can_resolve_ambiguous_job_as_new_and_preserve_description_and_salary(db):
    user = make_user(db, admin=True)
    _source, raw, resolution, candidates, event = make_review_case(
        db, title="Newly Confirmed Operations Lead", company="Fresh Company",
        description=("Own operational planning and coordinate cross-functional delivery. " * 8),
    )
    original_candidate_title = candidates[0].canonical_title
    client = make_client(db, user)
    try:
        response = client.post(
            f"/api/v1/admin/job-resolutions/{resolution.id}/resolve",
            json={
                "action": "CREATE_NEW",
                "reason": "The candidate jobs are distinct vacancies; retain this source record as a new job.",
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()["data"]
        assert result["status"] == "CREATED"
        assert result["job_id"]
        db.expire_all()
        new_job = db.get(Job, UUID(result["job_id"]))
        assert new_job.canonical_title == "Newly Confirmed Operations Lead"
        assert new_job.company_name == "Fresh Company"
        assert float(new_job.salary_min) == 120000
        assert float(new_job.salary_max) == 150000
        assert new_job.salary_currency == "USD"
        assert new_job.salary_period == "YEAR"
        assert new_job.metadata_json["seniority"] == "SENIOR"
        assert candidates[0].canonical_title == original_candidate_title
        description_version = db.scalar(select(JobDescriptionVersion).where(
            JobDescriptionVersion.job_id == new_job.id,
        ))
        assert description_version is not None
        assert description_version.source_record_id == raw.id
        assert description_version.content == raw.raw_content["job_description"].strip()
        provenance = db.scalar(select(JobProvenance).where(JobProvenance.raw_source_record_id == raw.id))
        assert provenance is not None and provenance.job_id == new_job.id
        db.refresh(event)
        assert event.canonical_job_id == new_job.id
        assert event.processing_status == "CANONICALIZED"
        assert db.scalar(select(JobResolution).where(
            JobResolution.id == UUID(result["resolution_id"]),
            JobResolution.status == "CREATED",
            JobResolution.related_resolution_id == resolution.id,
        )) is not None
    finally:
        client.close()


def test_non_admin_cannot_list_or_resolve_job_identity_reviews(db):
    user = make_user(db, admin=False)
    _source, _raw, resolution, candidates, _event = make_review_case(
        db, title="Protected Identity Review", company="Example",
        description=("Responsibilities and qualifications for this role. " * 8),
    )
    client = make_client(db, user)
    try:
        assert client.get("/api/v1/admin/job-resolutions").status_code == 403
        attempt = client.post(
            f"/api/v1/admin/job-resolutions/{resolution.id}/resolve",
            json={
                "action": "MATCH_EXISTING",
                "job_id": str(candidates[0].id),
                "reason": "Unauthorized test attempt.",
            },
        )
        assert attempt.status_code == 403
        db.expire_all()
        assert db.get(JobResolution, resolution.id).superseded_at is None
        assert db.scalar(select(AdminAuditEvent).where(
            AdminAuditEvent.action == "JOB_IDENTITY_RESOLUTION_DECIDED",
        )) is None
    finally:
        client.close()



def test_manual_identity_review_cannot_overwrite_shared_canonical_job(db):
    admin = make_user(db, admin=True)
    owner = make_user(db, admin=False)
    _manual_source, raw, resolution, candidates, event = make_review_case(
        db,
        title="Manual Source Title",
        company="Manual Company",
        description=("Manual description should not overwrite a shared canonical job. " * 8),
        source_type="MANUAL_SUBMISSION",
        submitted_by_user_id=owner.id,
    )
    target = candidates[0]
    now = datetime.now(timezone.utc)
    public_source = Source(
        name=f"Public source for manual identity test {uuid4()}",
        source_type="GREENHOUSE_JOB_BOARD",
        base_url="https://public.example.test",
        status="ACTIVE",
        permission_review_status="APPROVED",
        permission_review_reference="test-policy-review://public",
        permission_reviewed_at=now,
        operational_state="AVAILABLE",
        configuration={},
        created_at=now,
        updated_at=now,
    )
    db.add(public_source)
    db.flush()
    target.canonical_title = "Shared canonical title"
    target.company_name = "Authoritative Company"
    target.salary_min = 200000
    target.salary_max = 240000
    target.salary_currency = "USD"
    target.salary_period = "YEAR"
    target.application_url = "https://public.example.test/jobs/canonical"
    target.lifecycle_status = "ACTIVE"
    target.freshness_status = "FRESH"
    target.last_verified_at = now
    target.metadata_json = {"seniority": "DIRECTOR"}
    target.last_observed_at = now
    db.add(JobProvenance(
        job_id=target.id,
        source_id=public_source.id,
        raw_source_record_id=None,
        submitted_by_user_id=None,
        source_url="https://public.example.test/jobs/canonical",
        normalized_source_url="https://public.example.test/jobs/canonical",
        observation_type="ACQUIRED",
        asserted_by="SOURCE",
        observed_at=now,
        confidence=1.0,
        status="OBSERVED",
        metadata_json={},
    ))
    db.commit()

    client = make_client(db, admin)
    try:
        response = client.post(
            f"/api/v1/admin/job-resolutions/{resolution.id}/resolve",
            json={
                "action": "MATCH_EXISTING",
                "job_id": str(target.id),
                "reason": "Manual submission is the same vacancy; retain shared source facts.",
            },
        )
        assert response.status_code == 200, response.text
        db.expire_all()
        db.refresh(target)
        assert target.canonical_title == "Shared canonical title"
        assert target.company_name == "Authoritative Company"
        assert target.salary_min == 200000
        assert target.salary_max == 240000
        assert target.salary_currency == "USD"
        assert target.salary_period == "YEAR"
        assert target.application_url == "https://public.example.test/jobs/canonical"
        assert target.lifecycle_status == "ACTIVE"
        assert target.freshness_status == "FRESH"
        assert target.last_verified_at == now
        assert target.metadata_json["seniority"] == "DIRECTOR"
        assert db.scalar(select(JobDescriptionVersion).where(
            JobDescriptionVersion.job_id == target.id,
        )) is None
        provenance = db.scalar(select(JobProvenance).where(
            JobProvenance.raw_source_record_id == raw.id,
        ))
        assert provenance is not None
        assert provenance.job_id == target.id
        assert provenance.submitted_by_user_id == owner.id
        db.refresh(event)
        assert event.canonical_job_id == target.id
        assert event.processing_status == "CANONICALIZED"
    finally:
        client.close()
