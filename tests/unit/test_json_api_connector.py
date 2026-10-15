import json
import socket
from uuid import uuid4

import httpx
import pytest

from app.infrastructure.acquisition.json_api import JsonApiConnector, JsonApiConnectorError


class FakeHTTPClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, *, params=None):
        self.calls.append((url, params))
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return httpx.Response(
            200,
            content=json.dumps(payload).encode("utf-8"),
            request=httpx.Request("GET", url),
        )


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
    results = JsonApiConnector(http_client=client).acquire(
        source_id=source_id,
        context={"endpoint_url": "https://api.example.com/jobs", "page_param": "page",
                 "page_start": 1, "max_pages": 2},
    )

    assert len(results) == 2
    assert [item.external_record_id for item in results] == ["1", "2"]
    assert results[0].source_id == source_id
    assert results[0].source_url == "https://jobs.example.com/1"
    assert results[0].raw_content["jobpunter_normalized"]["company"] == "Example"
    assert [call[1]["page"] for call in client.calls] == ["1", "2"]
    assert all(item.content_hash for item in results)


def test_json_api_rejects_non_public_endpoint_before_request(monkeypatch):
    client = FakeHTTPClient([])
    with pytest.raises(ValueError, match="private or non-public"):
        JsonApiConnector(http_client=client).acquire(
            source_id=uuid4(), context={"endpoint_url": "https://127.0.0.1/jobs"},
        )
    assert client.calls == []


def test_json_api_rejects_invalid_items_shape(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    client = FakeHTTPClient([{"unexpected": "shape"}])
    with pytest.raises(JsonApiConnectorError, match="json_api_invalid_items"):
        JsonApiConnector(http_client=client).acquire(
            source_id=uuid4(), context={"endpoint_url": "https://api.example.com/jobs"},
        )


def test_json_api_rejects_oversized_response(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)

    class OversizedClient:
        def get(self, url, *, params=None):
            return httpx.Response(
                200, content=b"x" * 32,
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(JsonApiConnector, "MAX_RESPONSE_BYTES", 16)
    with pytest.raises(JsonApiConnectorError, match="json_api_response_too_large"):
        JsonApiConnector(http_client=OversizedClient()).acquire(
            source_id=uuid4(), context={"endpoint_url": "https://api.example.com/jobs"},
        )
