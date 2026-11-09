import pytest

from app.application.jobs.job_verification import VerificationResult
from app.infrastructure.verification.http_application_verifier import PublicWebApplicationVerifier
from app.security.restricted_hosts import is_restricted_host


@pytest.mark.parametrize("host", [
    "indeed.com", "www.indeed.com", "ng.indeed.com", "INDEED.COM", "uk.linkedin.com",
    "www.glassdoor.com", "ziprecruiter.com", "indeed.com.",
])
def test_restricted_hosts_match_domain_and_subdomains(host):
    assert is_restricted_host(host)


@pytest.mark.parametrize("host", [
    None, "", "example.com", "notindeed.com", "indeed.com.evil.example", "greenhouse.io",
])
def test_lookalike_and_unrelated_hosts_are_not_restricted(host):
    assert not is_restricted_host(host)


class _NoNetwork:
    def __enter__(self):
        raise AssertionError("verifier must not open a connection to a restricted host")

    def __exit__(self, *a):
        return False


def test_verifier_never_requests_a_restricted_host():
    verifier = PublicWebApplicationVerifier(
        client_factory=lambda: _NoNetwork(), resolver=lambda host: ["93.184.216.34"],
    )
    result = verifier("https://ng.indeed.com/viewjob?jk=abc")
    assert isinstance(result, VerificationResult)
    assert result.status == "UNKNOWN"
    assert result.reason_code == "RESTRICTED_HOST_NOT_CHECKED"
    assert result.http_status_code is None


def test_verifier_stops_when_a_public_page_redirects_to_a_restricted_host():
    import httpx

    def handler(request):
        return httpx.Response(302, headers={"location": "https://www.indeed.com/viewjob?jk=1"})

    verifier = PublicWebApplicationVerifier(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=lambda host: ["93.184.216.34"],
    )
    result = verifier("https://careers.example.com/job/1")
    assert result.status == "UNKNOWN"
    assert result.reason_code == "RESTRICTED_HOST_NOT_CHECKED"
