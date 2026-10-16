import json
import socket
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest

from app.infrastructure.acquisition.json_api import JsonApiConnector
from app.ports.acquisition import AcquisitionOutcome


class FakeHTTPClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, *, params=None, headers=None):
        self.calls.append((url, params))
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, httpx.Response):
            return payload
        return httpx.Response(
            200,
            content=json.dumps(payload).encode("utf-8"),
            request=httpx.Request("GET", url),
        )

    @contextmanager
    def stream(self, method, url, *, params=None, headers=None):
        assert method == "GET"
        yield self.get(url, params=params, headers=headers)


def _public_dns(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))]


def test_json_api_maps_records_and_bounds_pagination(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    client = FakeHTTPClient([
        {"jobs": [{"id": 1, "title": "Product Manager", "company": {"name": "Example"},
                   "location": "Remote - Nigeria", "description": "Own product delivery",
                   "url": "https://jobs.example.com/1"}]},
        {"jobs": [{"id": 2, "title": "Technical Product Manager", "company": "Example",
                   "location": "Lagos", "description": "Lead technical delivery",
                   "url": "https://jobs.example.com/2"}]},
    ])
    source_id = uuid4()
    results = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=source_id,
        context={"endpoint_url": "https://api.example.com/jobs", "page_param": "page",
                 "page_start": 1, "max_pages": 2},
    )

    assert results.status == AcquisitionOutcome.PARTIAL
    assert results.error_category == "MAX_PAGES_LIMIT_REACHED"
    assert len(results.records) == 2
    assert [item.external_record_id for item in results.records] == ["1", "2"]
    assert results.records[0].source_id == source_id
    assert results.records[0].source_url == "https://jobs.example.com/1"
    assert results.records[0].raw_content["jobpunter_normalized"]["company"] == "Example"
    assert [call[1]["page"] for call in client.calls] == ["1", "2"]
    assert all(item.content_hash for item in results.records)


def test_json_api_rejects_non_public_endpoint_before_request(monkeypatch):
    client = FakeHTTPClient([])
    with pytest.raises(ValueError, match="private or non-public"):
        JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
            source_id=uuid4(), context={"endpoint_url": "https://127.0.0.1/jobs"},
        )
    assert client.calls == []


def test_json_api_rejects_invalid_items_shape(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    client = FakeHTTPClient([{"unexpected": "shape"}])
    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(), context={"endpoint_url": "https://api.example.com/jobs"},
    )
    assert result.status == AcquisitionOutcome.FAILED
    assert result.error_category == "JSON_API_INVALID_ITEMS"


def test_json_api_rejects_oversized_response(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)

    class OversizedClient:
        def get(self, url, *, params=None):
            return httpx.Response(
                200, content=b"x" * 32,
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(JsonApiConnector, "MAX_RESPONSE_BYTES", 16)
    result = JsonApiConnector(http_client=OversizedClient(), allow_unsafe_http_client=True).acquire(
        source_id=uuid4(), context={"endpoint_url": "https://api.example.com/jobs"},
    )
    assert result.status == AcquisitionOutcome.FAILED
    assert result.error_category == "JSON_API_RESPONSE_TOO_LARGE"



def test_json_api_preserves_partial_results_when_a_later_page_is_unavailable(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    first_page = {
        "jobs": [{
            "id": 1,
            "title": "Product Manager",
            "company": "Example",
            "url": "https://jobs.example.com/1",
        }],
    }
    request = httpx.Request("GET", "https://api.example.com/jobs?page=2")
    client = FakeHTTPClient([
        first_page,
        httpx.ConnectError("private network details", request=request),
    ])

    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={
            "endpoint_url": "https://api.example.com/jobs",
            "page_param": "page",
            "page_start": 1,
            "max_pages": 2,
        },
    )

    assert result.status == AcquisitionOutcome.PARTIAL
    assert len(result.records) == 1
    assert result.error_category == "UPSTREAM_UNAVAILABLE"
    assert result.failures[0].category == "UPSTREAM_UNAVAILABLE"


def test_json_api_reports_rate_limit_as_restricted(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    request = httpx.Request("GET", "https://api.example.com/jobs")
    client = FakeHTTPClient([
        httpx.Response(429, headers={"Retry-After": "15"}, request=request),
    ])

    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"endpoint_url": "https://api.example.com/jobs"},
    )

    assert result.status == AcquisitionOutcome.RESTRICTED
    assert result.error_category == "SOURCE_RESTRICTED"
    assert result.retry_after_seconds == 15


def test_json_api_empty_success_is_not_reported_as_failure(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    client = FakeHTTPClient([{"jobs": []}])

    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"endpoint_url": "https://api.example.com/jobs"},
    )

    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert result.records == ()



def test_json_api_caps_total_items_and_reports_partial_result(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    monkeypatch.setattr(JsonApiConnector, "MAX_ITEMS", 1)
    client = FakeHTTPClient([{
        "jobs": [
            {"id": 1, "title": "Product Manager", "url": "https://jobs.example.com/1"},
            {"id": 2, "title": "Technical Product Manager", "url": "https://jobs.example.com/2"},
        ],
    }])

    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"endpoint_url": "https://api.example.com/jobs"},
    )

    assert result.status == AcquisitionOutcome.PARTIAL
    assert len(result.records) == 1
    assert result.error_category == "MAX_ITEMS_LIMIT_REACHED"
