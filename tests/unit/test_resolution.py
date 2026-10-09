from uuid import uuid4

from app.domain.jobs.identity_resolution import (
    ExistingJobIdentity,
    IdentityAction,
    JobIdentityCandidate,
    description_fingerprint,
    normalize_application_url,
    resolve_job_identity,
)


def test_normalized_application_url_matches_existing_job():
    job_id = uuid4()
    existing = ExistingJobIdentity(
        job_id=job_id,
        application_url="HTTPS://EXAMPLE.TEST/careers/123/#details",
        title="Analyst",
        company_name="Example Co",
    )
    candidate = JobIdentityCandidate(
        source_id=uuid4(),
        external_record_id=None,
        application_url="https://example.test/careers/123",
        title="Analyst",
        company_name="Example Co",
    )

    result = resolve_job_identity(candidate, [existing])

    assert result.action == IdentityAction.MATCH_EXISTING
    assert result.job_id == job_id
    assert result.method == "APPLICATION_URL"


def test_external_id_only_matches_within_same_source():
    source_id = uuid4()
    job_id = uuid4()
    existing = ExistingJobIdentity(
        job_id=job_id,
        application_url=None,
        title="Analyst",
        company_name="Example Co",
        source_identifiers=((source_id, "job-123"),),
    )
    candidate = JobIdentityCandidate(
        source_id=source_id,
        external_record_id=" job-123 ",
        application_url=None,
        title="Analyst",
        company_name="Example Co",
    )

    result = resolve_job_identity(candidate, [existing])

    assert result.action == IdentityAction.MATCH_EXISTING
    assert result.job_id == job_id
    assert result.method == "SOURCE_EXTERNAL_ID"


def test_matching_description_fingerprint_can_resolve_same_job():
    job_id = uuid4()
    fingerprint = description_fingerprint("Build reports and analyse operational data")
    existing = ExistingJobIdentity(
        job_id=job_id,
        application_url=None,
        title="Business Analyst",
        company_name="Example Co",
        description_fingerprint=fingerprint,
    )
    candidate = JobIdentityCandidate(
        source_id=uuid4(),
        external_record_id=None,
        application_url=None,
        title=" Business Analyst ",
        company_name="EXAMPLE CO",
        description_fingerprint=fingerprint,
    )

    result = resolve_job_identity(candidate, [existing])

    assert result.action == IdentityAction.MATCH_EXISTING
    assert result.job_id == job_id
    assert result.method == "DESCRIPTION_FINGERPRINT"


def test_title_company_and_location_alone_require_review_not_merge():
    job_id = uuid4()
    existing = ExistingJobIdentity(
        job_id=job_id,
        application_url=None,
        title="Analyst",
        company_name="Example Co",
        location_city="Lagos",
        location_country="Nigeria",
    )
    candidate = JobIdentityCandidate(
        source_id=uuid4(),
        external_record_id=None,
        application_url=None,
        title="Analyst",
        company_name="Example Co",
        location_city="Lagos",
        location_country="Nigeria",
        description_fingerprint="different-content",
    )

    result = resolve_job_identity(candidate, [existing])

    assert result.action == IdentityAction.REVIEW
    assert result.job_id is None
    assert result.method == "TITLE_COMPANY_LOCATION_ONLY"


def test_missing_title_or_company_requires_review():
    candidate = JobIdentityCandidate(
        source_id=None,
        external_record_id=None,
        application_url=None,
        title="Analyst",
        company_name=None,
    )

    result = resolve_job_identity(candidate, [])

    assert result.action == IdentityAction.REVIEW
    assert result.method == "INSUFFICIENT_IDENTITY_FIELDS"


def test_unmatched_identity_can_create_new_job():
    candidate = JobIdentityCandidate(
        source_id=uuid4(),
        external_record_id="job-new",
        application_url="https://example.test/jobs/new",
        title="Analyst",
        company_name="New Co",
    )

    result = resolve_job_identity(candidate, [])

    assert result.action == IdentityAction.CREATE_NEW
    assert result.job_id is None


def test_application_url_normalization_removes_fragment_and_default_port():
    assert normalize_application_url("HTTPS://EXAMPLE.TEST:443/jobs/1/#apply") == (
        "https://example.test/jobs/1"
    )
