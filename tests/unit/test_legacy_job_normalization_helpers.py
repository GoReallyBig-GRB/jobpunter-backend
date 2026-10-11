from app.application.jobs.normalization import (
    derive_provenance_label,
    extract_external_job_id,
)


def test_external_job_id_extraction_matches_supported_legacy_patterns():
    assert extract_external_job_id("https://jobs.lever.co/acme/123e4567-e89b-12d3-a456-426614174000") == (
        "lever_123e4567-e89b-12d3-a456-426614174000"
    )
    assert extract_external_job_id("https://boards.greenhouse.io/acme?gh_jid=12345") == (
        "greenhouse_12345"
    )
    assert extract_external_job_id("https://boards.greenhouse.io/jobs/67890") == (
        "greenhouse_67890"
    )
    assert extract_external_job_id("https://www.linkedin.com/jobs/view/98765") == (
        "linkedin_98765"
    )
    assert extract_external_job_id("https://www.indeed.com/viewjob?jk=abc123") == (
        "indeed_abc123"
    )


def test_external_job_id_extraction_rejects_invalid_or_unknown_urls():
    assert extract_external_job_id(None) is None
    assert extract_external_job_id("not a url") is None
    assert extract_external_job_id("https://example.com/jobs/123") is None


def test_provenance_label_precedence_matches_legacy_rules():
    assert derive_provenance_label("scheduled email alert") == "Scheduled Job-Site Match"
    assert derive_provenance_label("automated email alert") == "Automated Email Alert"
    assert derive_provenance_label("manual email") == "Manual Email"
    assert derive_provenance_label("manual URL", "https://example.com/job") == "Manual URL"
    assert derive_provenance_label(None) == "Quick Job-Site Match"


def test_external_job_id_does_not_trust_ats_shaped_paths_or_query_keys_on_other_domains():
    assert extract_external_job_id("https://example.com/jobs/12345") is None
    assert extract_external_job_id("https://example.com/view/12345") is None
    assert extract_external_job_id("https://example.com/apply?gh_jid=12345") is None
    assert extract_external_job_id("https://example.com/job?currentJobId=12345") is None
    assert extract_external_job_id("https://example.com/job?jk=abc123") is None
    assert extract_external_job_id(
        "https://example.com/123e4567-e89b-12d3-a456-426614174000"
    ) is None
