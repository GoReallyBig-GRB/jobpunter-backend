import ipaddress

import pytest

from app.infrastructure.verification.http_application_verifier import (
    PublicWebApplicationVerifier,
)


class FakeResponse:
    def __init__(self, status_code, body="", headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.encoding = "utf-8"
        self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_bytes(self):
        yield self._body


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requested_urls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def stream(self, method, url):
        assert method == "GET"
        self.requested_urls.append(url)
        return self.responses.pop(0)


def make_verifier(client, resolver=None):
    def public_resolver(host):
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return ["93.184.216.34"]
        if not address.is_global:
            raise ValueError("URL resolves to a non-public address")
        return [str(address)]

    return PublicWebApplicationVerifier(
        client_factory=lambda: client,
        resolver=resolver or public_resolver,
    )


def test_active_requires_job_page_and_application_signals():
    client = FakeClient([
        FakeResponse(
            200,
            "Job Description Responsibilities Qualifications Apply Now",
        )
    ])
    result = make_verifier(client)("https://jobs.example.com/role/123")

    assert result.status == "ACTIVE"
    assert result.reason_code == "JOB_PAGE_AND_APPLICATION_SIGNAL_PRESENT"
    assert result.evidence["apply_marker_detected"] is True
    assert client.requested_urls == ["https://jobs.example.com/role/123"]


def test_http_410_is_explicit_closed_signal():
    client = FakeClient([FakeResponse(410, "Gone")])
    result = make_verifier(client)("https://jobs.example.com/role/123")

    assert result.status == "CLOSED"
    assert result.reason_code == "EXPLICIT_JOB_CLOSED_SIGNAL"


def test_http_404_is_unknown_not_assumed_closed():
    client = FakeClient([FakeResponse(404, "Not Found")])
    result = make_verifier(client)("https://jobs.example.com/role/123")

    assert result.status == "UNKNOWN"
    assert result.reason_code == "INSUFFICIENT_ACTIVE_OR_CLOSED_EVIDENCE"


def test_redirect_to_private_ip_is_rejected_before_following():
    client = FakeClient([
        FakeResponse(302, headers={"location": "http://127.0.0.1/admin"})
    ])
    verifier = make_verifier(client)

    with pytest.raises(ValueError, match="non-public"):
        verifier("https://jobs.example.com/role/123")

    assert client.requested_urls == ["https://jobs.example.com/role/123"]


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/job",
        "http://127.0.0.1/job",
        "file:///etc/passwd",
        "https://user:pass@jobs.example.com/job",
    ],
)
def test_unsafe_url_is_rejected_before_request(url):
    client = FakeClient([])
    verifier = make_verifier(client)

    with pytest.raises(ValueError):
        verifier(url)

    assert client.requested_urls == []


def test_redirect_is_checked_and_followed_when_public():
    client = FakeClient([
        FakeResponse(302, headers={"location": "/role/123/apply"}),
        FakeResponse(200, "Job Description Requirements Apply Now"),
    ])
    result = make_verifier(client)("https://jobs.example.com/start")

    assert result.status == "ACTIVE"
    assert result.final_url == "https://jobs.example.com/role/123/apply"
    assert result.evidence["redirect_count"] == 1
