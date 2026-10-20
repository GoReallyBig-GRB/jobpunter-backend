import socket
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest

from app.infrastructure.acquisition.rss_atom import FeedConnectorError, RssAtomConnector
from app.ports.acquisition import AcquisitionAdapterError, AcquisitionOutcome


class FakeHTTPClient:
    def __init__(self, body):
        self.body = body
        self.calls = []

    def get(self, url, *, headers=None):
        self.calls.append((url, headers))
        if isinstance(self.body, httpx.Response):
            return self.body
        return httpx.Response(
            200, content=self.body,
            request=httpx.Request("GET", url),
        )

    @contextmanager
    def stream(self, method, url, *, params=None, headers=None, timeout=None, follow_redirects=None):
        assert method == "GET"
        yield self.get(url, headers=headers)


def _public_dns(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))]



def test_rss_atom_requires_explicit_opt_in_for_injected_clients():
    with pytest.raises(ValueError, match="bypass the pinned public-address transport"):
        RssAtomConnector(http_client=object())

def test_rss_atom_parses_atom_entries_and_preserves_source_provenance(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    body = b"""<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><id>job-17</id><title>Technical Product Manager</title>
        <link href="https://jobs.example.com/17" />
        <updated>2026-10-14T10:00:00Z</updated>
        <summary>Lead technical product delivery and coordinate engineering teams.</summary>
      </entry>
    </feed>"""
    client = FakeHTTPClient(body)
    source_id = uuid4()
    policy_checks = []
    results = RssAtomConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=source_id,
        context={
            "feed_url": "https://feeds.example.com/jobs",
            "acquisition_policy_revalidator": lambda **kwargs: policy_checks.append(kwargs),
        },
    )

    assert results.status == AcquisitionOutcome.SUCCEEDED
    assert len(results.records) == 1
    assert results.records[0].source_id == source_id
    assert results.records[0].external_record_id == "job-17"
    assert results.records[0].source_url == "https://jobs.example.com/17"
    assert results.records[0].raw_content["title"] == "Technical Product Manager"
    assert results.records[0].raw_content["source_url"] == "https://jobs.example.com/17"
    assert results.records[0].raw_content["application_url"] is None
    assert results.records[0].raw_content["url"] is None
    assert "technical product delivery" in results.records[0].raw_content["description"].lower()
    assert client.calls[0][1]["Accept"]
    expected_policy_check = {
        "url": "https://feeds.example.com/jobs",
        "connector_type": "RSS_ATOM",
        "method": "GET",
    }
    assert policy_checks == [expected_policy_check, expected_policy_check]


def test_rss_atom_rejects_non_public_feed_before_request():
    client = FakeHTTPClient(b"<rss/>")
    with pytest.raises(ValueError, match="private or non-public"):
        RssAtomConnector(http_client=client, allow_unsafe_http_client=True).acquire(
            source_id=uuid4(), context={"feed_url": "https://127.0.0.1/jobs.xml"},
        )
    assert client.calls == []


def test_rss_atom_rejects_malformed_xml(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    with pytest.raises(FeedConnectorError, match="RSS_ATOM_INVALID_XML"):
        RssAtomConnector(http_client=FakeHTTPClient(b"<feed><entry>"), allow_unsafe_http_client=True).acquire(
            source_id=uuid4(), context={"feed_url": "https://feeds.example.com/jobs"},
        )


def test_rss_atom_empty_feed_is_a_successful_empty_result(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    result = RssAtomConnector(http_client=FakeHTTPClient(b"<rss><channel /></rss>"), allow_unsafe_http_client=True).acquire(
        source_id=uuid4(), context={"feed_url": "https://feeds.example.com/jobs"},
    )
    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert result.records == ()



def test_rss_atom_reports_access_restriction_without_response_body(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    request = httpx.Request("GET", "https://feeds.example.com/jobs")
    response = httpx.Response(403, content=b"private upstream details", request=request)

    with pytest.raises(FeedConnectorError) as raised:
        RssAtomConnector(http_client=FakeHTTPClient(response), allow_unsafe_http_client=True).acquire(
            source_id=uuid4(),
            context={"feed_url": "https://feeds.example.com/jobs"},
        )

    assert raised.value.status == AcquisitionOutcome.RESTRICTED
    assert raised.value.error_category == "SOURCE_RESTRICTED"
    assert "private upstream details" not in str(raised.value)



def test_rss_atom_caps_entries_and_reports_partial_result(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    monkeypatch.setattr(RssAtomConnector, "MAX_ITEMS", 1)
    body = b"""<rss><channel>
      <item><guid>1</guid><title>Product Manager</title><link>https://jobs.example.com/1</link></item>
      <item><guid>2</guid><title>Technical Product Manager</title><link>https://jobs.example.com/2</link></item>
    </channel></rss>"""

    result = RssAtomConnector(http_client=FakeHTTPClient(body), allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"feed_url": "https://feeds.example.com/jobs", "max_items": 1},
    )

    assert result.status == AcquisitionOutcome.PARTIAL
    assert len(result.records) == 1
    assert result.error_category == "MAX_ITEMS_LIMIT_REACHED"



def test_rss_atom_reports_skipped_malformed_entries_as_partial(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    body = b"""<rss><channel>
      <item><guid>1</guid><title>Product Manager</title><link>https://jobs.example.com/1</link></item>
      <item><guid>2</guid><link>https://jobs.example.com/2</link></item>
    </channel></rss>"""

    result = RssAtomConnector(http_client=FakeHTTPClient(body), allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"feed_url": "https://feeds.example.com/jobs"},
    )

    assert result.status == AcquisitionOutcome.PARTIAL
    assert len(result.records) == 1
    assert result.error_category == "RSS_ATOM_INVALID_ENTRY"
    assert result.failures[0].category == "RSS_ATOM_INVALID_ENTRY"


def test_atom_explicit_apply_link_is_separate_from_posting_url(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    body = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom"><title>Careers</title>
      <entry><id>job-2</id><title>Engineer</title>
        <link rel="alternate" href="https://jobs.example.com/roles/2" />
        <link rel="apply" href="https://apply.example.com/2" />
        <company>Example Inc</company><location>Remote</location>
        <summary>Build reliable systems.</summary>
      </entry>
    </feed>"""
    result = RssAtomConnector(
        http_client=FakeHTTPClient(body), allow_unsafe_http_client=True
    ).acquire(source_id=uuid4(), context={"feed_url": "https://feeds.example.com/atom.xml"})
    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert result.records[0].source_url == "https://jobs.example.com/roles/2"
    assert result.records[0].raw_content["source_url"] == "https://jobs.example.com/roles/2"
    assert result.records[0].raw_content["application_url"] == "https://apply.example.com/2"
    assert result.records[0].raw_content["url"] == "https://apply.example.com/2"
    assert result.records[0].raw_content["company"] == "Example Inc"


@pytest.mark.parametrize("feed_url", [
    "https://feeds.example.com/jobs?access_token=secret",
    "https://feeds.example.com:8443/jobs",
])
def test_rss_atom_rejects_credentials_and_nonstandard_ports(feed_url, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    with pytest.raises(ValueError):
        RssAtomConnector.validate_context({"feed_url": feed_url})


def test_rss_atom_rejects_html_content_type(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    response = httpx.Response(
        200,
        content=b"<html><body>not a feed</body></html>",
        headers={"content-type": "text/html"},
        request=httpx.Request("GET", "https://feeds.example.com/jobs"),
    )
    with pytest.raises(FeedConnectorError) as raised:
        RssAtomConnector(
            http_client=FakeHTTPClient(response), allow_unsafe_http_client=True
        ).acquire(source_id=uuid4(), context={"feed_url": "https://feeds.example.com/jobs"})
    assert raised.value.error_category == "RSS_ATOM_INVALID_CONTENT_TYPE"


def test_rss_atom_does_not_use_feed_endpoint_as_job_posting_url(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    body = b"""<rss version="2.0"><channel><title>Careers</title>
      <item><guid>job-no-link</guid><title>Engineer</title><description>Build systems.</description></item>
    </channel></rss>"""
    result = RssAtomConnector(
        http_client=FakeHTTPClient(body), allow_unsafe_http_client=True
    ).acquire(source_id=uuid4(), context={"feed_url": "https://feeds.example.com/jobs"})
    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert result.records[0].source_url is None
    assert result.records[0].raw_content["source_url"] is None
    assert result.records[0].raw_content["feed_url"] == "https://feeds.example.com/jobs"


def test_rss_atom_redirect_response_is_not_followed_by_connector(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    feed_url = "https://feeds.example.com/jobs"
    response = httpx.Response(
        302,
        headers={"location": "http://127.0.0.1/admin"},
        request=httpx.Request("GET", feed_url),
    )
    calls = []

    class RedirectClient:
        @contextmanager
        def stream(self, method, url, *, params=None, headers=None, timeout=None, follow_redirects=None):
            calls.append((method, url, follow_redirects))
            yield response

    with pytest.raises(FeedConnectorError):
        RssAtomConnector(
            http_client=RedirectClient(), allow_unsafe_http_client=True
        ).acquire(source_id=uuid4(), context={"feed_url": feed_url})

    assert calls == [("GET", feed_url, False)]
    assert response.history == []


def test_rss_atom_blocks_request_when_live_policy_revalidation_fails(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    client = FakeHTTPClient(b"<rss><channel /></rss>")

    def deny_request(**kwargs):
        assert kwargs == {
            "url": "https://feeds.example.com/jobs.xml",
            "connector_type": "RSS_ATOM",
            "method": "GET",
        }
        raise AcquisitionAdapterError(
            AcquisitionOutcome.RESTRICTED,
            "FORBIDDEN_TARGET_RULE_MATCHED",
        )

    with pytest.raises(AcquisitionAdapterError) as raised:
        RssAtomConnector(http_client=client, allow_unsafe_http_client=True).acquire(
            source_id=uuid4(),
            context={
                "feed_url": "https://feeds.example.com/jobs.xml",
                "acquisition_policy_revalidator": deny_request,
            },
        )
    assert raised.value.error_category == "FORBIDDEN_TARGET_RULE_MATCHED"
    assert client.calls == []
