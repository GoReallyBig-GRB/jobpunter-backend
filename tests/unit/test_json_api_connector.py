import json
import socket
import time
from email.utils import formatdate
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
        self.stream_options = []

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
    def stream(self, method, url, *, params=None, headers=None, timeout=None):
        assert method == "GET"
        self.stream_options.append({"headers": headers, "timeout": timeout})
        yield self.get(url, params=params, headers=headers)


def _public_dns(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))]



def test_json_api_requires_explicit_opt_in_for_injected_clients():
    with pytest.raises(ValueError, match="bypass the pinned public-address transport"):
        JsonApiConnector(http_client=object())

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
    policy_checks = []
    results = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=source_id,
        context={
            "endpoint_url": "https://api.example.com/jobs", "page_param": "page",
            "page_start": 1, "max_pages": 2,
            "acquisition_policy_revalidator": lambda **kwargs: policy_checks.append(kwargs),
        },
    )

    assert results.status == AcquisitionOutcome.PARTIAL
    assert results.error_category == "MAX_PAGES_LIMIT_REACHED"
    assert len(results.records) == 2
    assert [item.external_record_id for item in results.records] == ["1", "2"]
    assert results.records[0].source_id == source_id
    assert results.records[0].source_url == "https://jobs.example.com/1"
    assert results.records[0].raw_content["jobpunter_normalized"]["company"] == "Example"
    assert results.records[0].raw_content["source_url"] == "https://jobs.example.com/1"
    assert results.records[0].raw_content["application_url"] is None
    assert results.records[0].raw_content["url"] is None
    assert results.records[0].raw_content["source_record"]["url"] == "https://jobs.example.com/1"
    assert [call[1]["page"] for call in client.calls] == ["1", "2"]
    assert [check["method"] for check in policy_checks] == ["GET", "GET", "GET"]
    assert [check["url"] for check in policy_checks] == [
        "https://api.example.com/jobs",
        "https://api.example.com/jobs?page=1",
        "https://api.example.com/jobs?page=2",
    ]
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
        @contextmanager
        def stream(self, method, url, *, params=None, headers=None, timeout=None):
            assert method == "GET"
            yield httpx.Response(
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
        httpx.ConnectError("private network details", request=request),
        httpx.ConnectError("private network details", request=request),
    ])

    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={
            "endpoint_url": "https://api.example.com/jobs",
            "items_path": "jobs",
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


def test_json_api_pagination_stops_on_short_page_and_honors_timeout(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    client = FakeHTTPClient([{
        "jobs": [{"id": 1, "title": "Product Manager", "company": "Acme", "description": "D",
                  "url": "https://jobs.example.com/1"}],
    }])
    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={
            "endpoint_url": "https://api.example.com/jobs",
            "items_path": "jobs",
            "query_params": {"locale": "en"},
            "page_param": "page",
            "page_size_param": "limit",
            "page_size": 10,
            "max_pages": 3,
            "timeout_seconds": 23,
        },
    )
    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert len(result.records) == 1
    assert client.calls[0][1] == {"locale": "en", "page": "1", "limit": "10"}
    assert client.stream_options[0]["timeout"] == 23
    assert client.stream_options[0]["headers"]["Accept"] == "application/json"


def test_json_api_does_not_duplicate_a_repeated_page(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    page = {"jobs": [{"id": 1, "title": "Product Manager", "company": "Acme",
                      "description": "Lead product delivery", "url": "https://jobs.example.com/1"}]}
    client = FakeHTTPClient([page, page])
    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={
            "endpoint_url": "https://api.example.com/jobs",
            "items_path": "jobs",
            "page_param": "page",
            "page_size_param": "limit",
            "page_size": 1,
            "max_pages": 5,
        },
    )
    assert result.status == AcquisitionOutcome.PARTIAL
    assert len(result.records) == 1
    assert result.error_category == "JSON_API_REPEATED_PAGE"
    assert len(client.calls) == 2


def test_json_api_uses_bearer_token_from_environment_without_storing_it(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    monkeypatch.setenv("JOBPUNTER_TEST_API_TOKEN", "secret-value")
    client = FakeHTTPClient([{"jobs": []}])
    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={
            "endpoint_url": "https://api.example.com/jobs",
            "items_path": "jobs",
            "bearer_token_env": "JOBPUNTER_TEST_API_TOKEN",
        },
    )
    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert client.stream_options[0]["headers"]["Authorization"] == "Bearer secret-value"
    assert "secret-value" not in repr(result)


@pytest.mark.parametrize("context", [
    {"endpoint_url": "https://api.example.com/jobs?api_key=secret", "items_path": "jobs"},
    {"endpoint_url": "https://api.example.com:8443/jobs", "items_path": "jobs"},
    {"endpoint_url": "https://api.example.com/jobs", "items_path": "jobs", "query_params": {"api_key": "not-stored"}},
    {"endpoint_url": "https://api.example.com/jobs", "items_path": "jobs", "bearer_token_env": "Bearer secret"},
    {"endpoint_url": "https://api.example.com/jobs", "items_path": "jobs", "page_size_param": "limit", "page_size": 100, "max_pages": 20},
])
def test_json_api_rejects_credentials_and_unbounded_configuration(context, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    with pytest.raises(ValueError):
        JsonApiConnector.validate_context(context)


def test_json_api_does_not_use_endpoint_as_job_posting_url(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    client = FakeHTTPClient([{
        "jobs": [{"id": 2, "title": "Product Manager", "company": "Acme", "description": "Lead product delivery"}],
    }])
    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"endpoint_url": "https://api.example.com/jobs", "items_path": "jobs"},
    )
    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert result.records[0].source_url is None
    assert result.records[0].raw_content["source_url"] is None
    assert result.records[0].raw_content["url"] is None


def test_json_api_maps_explicit_application_url_without_promoting_posting_url(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    client = FakeHTTPClient([{
        "jobs": [{
            "id": 3,
            "title": "Product Manager",
            "company": "Acme",
            "description": "Lead product delivery",
            "url": "https://jobs.example.com/3",
            "apply_url": "https://apply.example.com/3",
        }],
    }])
    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={
            "endpoint_url": "https://api.example.com/jobs",
            "items_path": "jobs",
            "field_mapping": {"job_url": "url", "application_url": "apply_url"},
        },
    )
    assert result.status == AcquisitionOutcome.SUCCEEDED
    record = result.records[0]
    assert record.source_url == "https://jobs.example.com/3"
    assert record.raw_content["source_url"] == "https://jobs.example.com/3"
    assert record.raw_content["application_url"] == "https://apply.example.com/3"
    assert record.raw_content["url"] == "https://apply.example.com/3"



def test_json_api_retries_transient_get_failure_and_recovers(monkeypatch, caplog):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    monkeypatch.setattr("app.infrastructure.acquisition.json_api.time.sleep", lambda _delay: None)
    request = httpx.Request("GET", "https://api.example.com/jobs")
    client = FakeHTTPClient([
        httpx.ConnectError("sensitive host detail must not be logged", request=request),
        {"jobs": [{"id": "recovered", "title": "Product Manager", "company": "Example"}]},
    ])

    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"endpoint_url": "https://api.example.com/jobs"},
    )

    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert len(result.records) == 1
    assert len(client.calls) == 2
    assert "acquisition_request_recovered" in caplog.text
    assert "sensitive host detail" not in caplog.text
    assert "api.example.com" not in caplog.text


def test_json_api_stops_after_bounded_transient_get_retries(monkeypatch, caplog):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    monkeypatch.setattr("app.infrastructure.acquisition.json_api.time.sleep", lambda _delay: None)
    request = httpx.Request("GET", "https://api.example.com/jobs")
    client = FakeHTTPClient([
        httpx.ConnectError("private detail", request=request),
        httpx.ConnectError("private detail", request=request),
        httpx.ConnectError("private detail", request=request),
    ])

    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"endpoint_url": "https://api.example.com/jobs"},
    )

    assert result.status == AcquisitionOutcome.UNAVAILABLE
    assert result.error_category == "UPSTREAM_UNAVAILABLE"
    assert len(client.calls) == 3
    assert "private detail" not in caplog.text
    assert "api.example.com" not in caplog.text


def test_json_api_revalidates_policy_before_each_retry(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    monkeypatch.setattr("app.infrastructure.acquisition.json_api.time.sleep", lambda _delay: None)
    request = httpx.Request("GET", "https://api.example.com/jobs")
    client = FakeHTTPClient([
        httpx.ConnectError("transient", request=request),
        {"jobs": [{"id": "1", "title": "Product Manager"}]},
    ])
    checks = []

    def revalidate(**kwargs):
        checks.append(kwargs)

    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={
            "endpoint_url": "https://api.example.com/jobs",
            "acquisition_policy_revalidator": revalidate,
        },
    )

    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert len(client.calls) == 2
    assert len(checks) == 3
    assert all(check["method"] == "GET" for check in checks)



def test_json_api_honors_short_retry_after_within_bound(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    delays = []
    monkeypatch.setattr(
        "app.infrastructure.acquisition.json_api.time.sleep",
        lambda delay: delays.append(delay),
    )
    request = httpx.Request("GET", "https://api.example.com/jobs")
    client = FakeHTTPClient([
        httpx.Response(503, headers={"Retry-After": "1"}, request=request),
        {"jobs": [{"id": "1", "title": "Product Manager"}]},
    ])

    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"endpoint_url": "https://api.example.com/jobs"},
    )

    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert len(client.calls) == 2
    assert delays == [1.0]


def test_json_api_does_not_retry_when_retry_after_exceeds_wait_bound(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    delays = []
    monkeypatch.setattr(
        "app.infrastructure.acquisition.json_api.time.sleep",
        lambda delay: delays.append(delay),
    )
    request = httpx.Request("GET", "https://api.example.com/jobs")
    client = FakeHTTPClient([
        httpx.Response(503, headers={"Retry-After": "15"}, request=request),
        {"jobs": [{"id": "1", "title": "Product Manager"}]},
    ])

    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"endpoint_url": "https://api.example.com/jobs"},
    )

    assert result.status == AcquisitionOutcome.UNAVAILABLE
    assert len(client.calls) == 1
    assert delays == []



def test_json_api_respects_http_date_retry_after_beyond_wait_bound(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    delays = []
    monkeypatch.setattr(
        "app.infrastructure.acquisition.json_api.time.sleep",
        lambda delay: delays.append(delay),
    )
    request = httpx.Request("GET", "https://api.example.com/jobs")
    retry_after = formatdate(time.time() + 60, usegmt=True)
    client = FakeHTTPClient([
        httpx.Response(503, headers={"Retry-After": retry_after}, request=request),
        {"jobs": [{"id": "1", "title": "Product Manager"}]},
    ])

    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"endpoint_url": "https://api.example.com/jobs"},
    )

    assert result.status == AcquisitionOutcome.UNAVAILABLE
    assert len(client.calls) == 1
    assert delays == []


def test_json_api_ignores_malformed_retry_after_and_uses_bounded_backoff(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    delays = []
    monkeypatch.setattr(
        "app.infrastructure.acquisition.json_api.time.sleep",
        lambda delay: delays.append(delay),
    )
    request = httpx.Request("GET", "https://api.example.com/jobs")
    client = FakeHTTPClient([
        httpx.Response(503, headers={"Retry-After": "not-a-date"}, request=request),
        {"jobs": [{"id": "1", "title": "Product Manager"}]},
    ])

    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"endpoint_url": "https://api.example.com/jobs"},
    )

    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert len(client.calls) == 2
    assert delays == [0.25]



@pytest.mark.parametrize(
    ("status_code", "expected_outcome"),
    [
        (400, AcquisitionOutcome.FAILED),
        (401, AcquisitionOutcome.RESTRICTED),
        (403, AcquisitionOutcome.RESTRICTED),
        (429, AcquisitionOutcome.RESTRICTED),
    ],
)
def test_json_api_does_not_retry_non_transient_http_statuses(
    monkeypatch, status_code, expected_outcome
):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    delays = []
    monkeypatch.setattr(
        "app.infrastructure.acquisition.json_api.time.sleep",
        lambda delay: delays.append(delay),
    )
    request = httpx.Request("GET", "https://api.example.com/jobs")
    client = FakeHTTPClient([
        httpx.Response(status_code, request=request),
        {"jobs": [{"id": "must-not-be-read", "title": "Product Manager"}]},
    ])

    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"endpoint_url": "https://api.example.com/jobs"},
    )

    assert result.status == expected_outcome
    assert len(client.calls) == 1
    assert delays == []


def test_json_api_retries_only_transient_server_statuses_with_bounded_backoff(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    delays = []
    monkeypatch.setattr(
        "app.infrastructure.acquisition.json_api.time.sleep",
        lambda delay: delays.append(delay),
    )
    request = httpx.Request("GET", "https://api.example.com/jobs")
    client = FakeHTTPClient([
        httpx.Response(503, request=request),
        httpx.Response(502, request=request),
        {"jobs": [{"id": "recovered", "title": "Product Manager"}]},
    ])

    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"endpoint_url": "https://api.example.com/jobs"},
    )

    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert len(client.calls) == 3
    assert delays == [0.25, 0.5]


def test_json_api_stops_before_retry_if_policy_revalidation_fails(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    monkeypatch.setattr("app.infrastructure.acquisition.json_api.time.sleep", lambda _delay: None)
    request = httpx.Request("GET", "https://api.example.com/jobs")
    client = FakeHTTPClient([
        httpx.ConnectError("transient", request=request),
        {"jobs": [{"id": "must-not-be-read", "title": "Product Manager"}]},
    ])
    checks = []

    def revalidate(**kwargs):
        checks.append(kwargs)
        if len(checks) == 3:
            from app.ports.acquisition import AcquisitionAdapterError
            raise AcquisitionAdapterError(
                AcquisitionOutcome.RESTRICTED, "CONNECTOR_POLICY_CHANGED"
            )

    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={
            "endpoint_url": "https://api.example.com/jobs",
            "acquisition_policy_revalidator": revalidate,
        },
    )

    assert result.status == AcquisitionOutcome.RESTRICTED
    assert result.error_category == "CONNECTOR_POLICY_CHANGED"
    assert len(client.calls) == 1
    assert len(checks) == 3



def test_json_api_stops_at_total_acquisition_duration_limit(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    clock_values = iter([0.0, 0.0, 1.0, 121.0])
    monkeypatch.setattr(
        "app.infrastructure.acquisition.json_api.time.monotonic",
        lambda: next(clock_values),
    )
    client = FakeHTTPClient([
        {"jobs": [{"id": "first", "title": "Product Manager"}]},
        {"jobs": [{"id": "must-not-be-read", "title": "Engineer"}]},
    ])

    result = JsonApiConnector(
        http_client=client, allow_unsafe_http_client=True
    ).acquire(
        source_id=uuid4(),
        context={
            "endpoint_url": "https://api.example.com/jobs",
            "page_param": "page",
            "max_pages": 2,
        },
    )

    assert result.status == AcquisitionOutcome.PARTIAL
    assert result.error_category == "ACQUISITION_DURATION_LIMIT_REACHED"
    assert len(result.records) == 1
    assert len(client.calls) == 1
