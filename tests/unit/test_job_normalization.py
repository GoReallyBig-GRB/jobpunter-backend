from app.application.jobs.normalization import normalize_url


def test_normalize_url_removes_tracking_and_default_port_without_losing_job_identity():
    assert normalize_url(
        "HTTPS://Jobs.Example.com:443/roles/123/?utm_source=email&gh_jid=456#details"
    ) == "https://jobs.example.com/roles/123?gh_jid=456"


def test_normalize_url_preserves_meaningful_query_values_in_stable_order():
    assert normalize_url("https://jobs.example.com/role?id=123&lang=en") == (
        "https://jobs.example.com/role?id=123&lang=en"
    )
    assert normalize_url("https://jobs.example.com/role?lang=en&id=123") == (
        "https://jobs.example.com/role?id=123&lang=en"
    )


def test_normalize_url_keeps_ipv6_literal_bracketed():
    assert normalize_url("HTTPS://[2001:4860:4860::8888]:443/jobs/") == (
        "https://[2001:4860:4860::8888]/jobs"
    )
