import socket
from uuid import uuid4

import httpx
import pytest

from app.infrastructure.acquisition.rss_atom import FeedConnectorError, RssAtomConnector
from app.ports.acquisition import AcquisitionOutcome


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


def _public_dns(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))]


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
    results = RssAtomConnector(http_client=client).acquire(
        source_id=source_id, context={"feed_url": "https://feeds.example.com/jobs"},
    )

    assert results.status == AcquisitionOutcome.SUCCEEDED
    assert len(results.records) == 1
    assert results.records[0].source_id == source_id
    assert results.records[0].external_record_id == "job-17"
    assert results.records[0].source_url == "https://jobs.example.com/17"
    assert results.records[0].raw_content["title"] == "Technical Product Manager"
    assert "technical product delivery" in results.records[0].raw_content["description"].lower()
    assert client.calls[0][1]["Accept"]


def test_rss_atom_rejects_non_public_feed_before_request():
    client = FakeHTTPClient(b"<rss/>")
    with pytest.raises(ValueError, match="private or non-public"):
        RssAtomConnector(http_client=client).acquire(
            source_id=uuid4(), context={"feed_url": "https://127.0.0.1/jobs.xml"},
        )
    assert client.calls == []


def test_rss_atom_rejects_malformed_xml(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    with pytest.raises(FeedConnectorError, match="RSS_ATOM_INVALID_XML"):
        RssAtomConnector(http_client=FakeHTTPClient(b"<feed><entry>")).acquire(
            source_id=uuid4(), context={"feed_url": "https://feeds.example.com/jobs"},
        )


def test_rss_atom_empty_feed_is_a_successful_empty_result(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    result = RssAtomConnector(http_client=FakeHTTPClient(b"<rss><channel /></rss>")).acquire(
        source_id=uuid4(), context={"feed_url": "https://feeds.example.com/jobs"},
    )
    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert result.records == ()



def test_rss_atom_reports_access_restriction_without_response_body(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    request = httpx.Request("GET", "https://feeds.example.com/jobs")
    response = httpx.Response(403, content=b"private upstream details", request=request)

    with pytest.raises(FeedConnectorError) as raised:
        RssAtomConnector(http_client=FakeHTTPClient(response)).acquire(
            source_id=uuid4(),
            context={"feed_url": "https://feeds.example.com/jobs"},
        )

    assert raised.value.status == AcquisitionOutcome.RESTRICTED
    assert raised.value.error_category == "SOURCE_RESTRICTED"
    assert "private upstream details" not in str(raised.value)
