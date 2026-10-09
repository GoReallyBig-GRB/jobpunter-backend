from app.application.jobs.normalization import normalize_url


def test_normalize_url_removes_legacy_tracking_parameters_case_insensitively():
    assert normalize_url(
        "HTTPS://Example.com/jobs/123?ref=source&refId=campaign&trackingId=abc&trk=li"
        "&utm_source=mail&fbclid=click&jk=real-job#apply"
    ) == "https://example.com/jobs/123?jk=real-job"


def test_normalize_url_preserves_non_tracking_job_identity_parameters():
    assert normalize_url(
        "https://example.com/jobs/123?gh_jid=456&currentJobId=789"
    ) == "https://example.com/jobs/123?currentJobId=789&gh_jid=456"
