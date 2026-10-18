from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest

from app.infrastructure.connectors.greenhouse import GreenhouseJobBoardConnector
from app.infrastructure.acquisition.public_http import PublicAddressHTTPTransport
from app.ports.acquisition import AcquisitionAdapterError, AcquisitionOutcome


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    @contextmanager
    def stream(self, method, url, *, params=None, headers=None):
        assert method == "GET"
        self.calls.append((url, params))
        yield self.responses.pop(0)


class ChunkedResponse:
    history = ()

    def __init__(self, chunks):
        self.chunks = chunks
        self.consumed_chunks = 0

    def raise_for_status(self):
        return None

    def iter_bytes(self, chunk_size=65536):
        for chunk in self.chunks:
            self.consumed_chunks += 1
            yield chunk


class StreamingFakeClient:
    def __init__(self, response):
        self.response = response

    @contextmanager
    def stream(self, method, url, *, params=None, headers=None):
        assert method == "GET"
        yield self.response


def test_greenhouse_rejects_injected_client_without_explicit_opt_in():
    with pytest.raises(ValueError, match="bypass the pinned public-address transport"):
        GreenhouseJobBoardConnector(http_client=FakeClient([]))


def test_greenhouse_default_client_uses_pinned_public_transport(monkeypatch):
    options = {}

    class ClientFactory:
        def __init__(self, **kwargs):
            options.update(kwargs)

    monkeypatch.setattr(httpx, "Client", ClientFactory)
    GreenhouseJobBoardConnector._new_http_client()

    assert options["trust_env"] is False
    assert options["follow_redirects"] is False
    assert isinstance(options["transport"], PublicAddressHTTPTransport)


def response(payload, status_code=200):
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("GET", "https://boards-api.greenhouse.io/test"),
    )


def test_greenhouse_connector_maps_public_jobs_to_raw_acquisitions():
    client = FakeClient([
        response({"name": "Example Company"}),
        response({
            "jobs": [{
                "id": 1234,
                "title": "Technical Product Manager",
                "absolute_url": "https://boards.greenhouse.io/example/jobs/1234",
                "updated_at": "2026-10-01T12:00:00Z",
                "requisition_id": "REQ-1234",
                "language": "en",
                "location": {"name": "Remote - Nigeria"},
                "content": "<p>Lead technical product delivery.</p>",
                "departments": [{"id": 7, "name": "Product"}],
                "offices": [{"id": 8, "name": "Remote"}],
                "metadata": None,
            }]
        }),
    ])
    source_id = uuid4()
    policy_checks = []

    items = GreenhouseJobBoardConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=source_id,
        context={
            "board_token": "example",
            "acquisition_policy_revalidator": lambda **kwargs: policy_checks.append(kwargs),
        },
    )

    assert items.status == AcquisitionOutcome.SUCCEEDED
    assert len(items.records) == 1
    item = items.records[0]
    assert item.source_id == source_id
    assert item.external_record_id == "1234"
    assert item.source_url == "https://boards.greenhouse.io/example/jobs/1234"
    assert item.raw_content["title"] == "Technical Product Manager"
    assert item.raw_content["company"] == "Example Company"
    assert item.raw_content["description"] == "<p>Lead technical product delivery.</p>"
    assert item.raw_content["location"] == "Remote - Nigeria"
    assert item.raw_content["greenhouse_job_id"] == "1234"
    assert len(item.content_hash) == 64
    assert item.observed_at.tzinfo is not None
    assert client.calls == [
        ("https://boards-api.greenhouse.io/v1/boards/example", None),
        ("https://boards-api.greenhouse.io/v1/boards/example/jobs", {"content": "true"}),
    ]
    assert [check["method"] for check in policy_checks] == ["GET", "GET"]
    assert [check["url"] for check in policy_checks] == [
        "https://boards-api.greenhouse.io/v1/boards/example",
        "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true",
    ]


@pytest.mark.parametrize("board_token", ["", "../private", "board/token", "https://evil.test", "a b"])
def test_greenhouse_connector_rejects_invalid_board_tokens_before_network_access(board_token):
    client = FakeClient([])
    with pytest.raises(ValueError, match="board_token"):
        GreenhouseJobBoardConnector(http_client=client, allow_unsafe_http_client=True).acquire(
            source_id=uuid4(),
            context={"board_token": board_token},
        )
    assert client.calls == []



def test_greenhouse_stops_before_jobs_request_when_live_policy_changes():
    client = FakeClient([response({"name": "Example Company"})])
    checks = []

    def revalidate(**kwargs):
        checks.append(kwargs)
        if len(checks) == 2:
            raise AcquisitionAdapterError(
                AcquisitionOutcome.RESTRICTED, "ACQUISITION_CONFIGURATION_CHANGED"
            )

    connector = GreenhouseJobBoardConnector(http_client=client, allow_unsafe_http_client=True)
    with pytest.raises(AcquisitionAdapterError) as error:
        connector.acquire(
            source_id=uuid4(),
            context={"board_token": "example", "acquisition_policy_revalidator": revalidate},
        )

    assert error.value.error_category == "ACQUISITION_CONFIGURATION_CHANGED"
    assert len(checks) == 2
    assert len(client.calls) == 1
    assert client.calls[0][0] == "https://boards-api.greenhouse.io/v1/boards/example"



def test_greenhouse_connector_returns_successful_empty_result_for_empty_board():
    client = FakeClient([
        response({"name": "Example Company"}),
        response({"jobs": []}),
    ])

    items = GreenhouseJobBoardConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"board_token": "example"},
    )

    assert items.status == AcquisitionOutcome.SUCCEEDED
    assert items.records == ()


def test_greenhouse_connector_sanitizes_http_failures():
    client = FakeClient([
        response({"error": "sensitive response body"}, status_code=503),
    ])

    with pytest.raises(AcquisitionAdapterError) as error:
        GreenhouseJobBoardConnector(http_client=client, allow_unsafe_http_client=True).acquire(
            source_id=uuid4(),
            context={"board_token": "example"},
        )
    assert error.value.status == AcquisitionOutcome.UNAVAILABLE
    assert error.value.error_category == "UPSTREAM_UNAVAILABLE"
    assert "sensitive response body" not in str(error.value)


def test_greenhouse_connector_fails_closed_on_malformed_job_record():
    client = FakeClient([
        response({"name": "Example Company"}),
        response({"jobs": [{"id": 1, "title": "No URL"}]}),
    ])

    result = GreenhouseJobBoardConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"board_token": "example"},
    )
    assert result.status == AcquisitionOutcome.FAILED
    assert result.error_category == "GREENHOUSE_INVALID_JOB_RECORD"
    assert result.records == ()


def test_greenhouse_health_check_verifies_board_and_jobs_without_acquiring_records():
    client = FakeClient([
        response({"name": "Example Company"}),
        response({"jobs": [{"id": 1}, {"id": 2}]}),
    ])

    result = GreenhouseJobBoardConnector(http_client=client, allow_unsafe_http_client=True).health_check(
        context={"board_token": "example"},
    )

    assert result == {
        "status": "HEALTHY",
        "board_name": "Example Company",
        "job_count": 2,
    }
    assert client.calls == [
        ("https://boards-api.greenhouse.io/v1/boards/example", None),
        ("https://boards-api.greenhouse.io/v1/boards/example/jobs", {"content": "true"}),
    ]


@pytest.mark.parametrize("jobs_payload", [{}, {"jobs": None}, {"jobs": "not-a-list"}])
def test_greenhouse_health_check_rejects_malformed_jobs_response(jobs_payload):
    client = FakeClient([response({"name": "Example Company"}), response(jobs_payload)])
    with pytest.raises(RuntimeError, match="GREENHOUSE_INVALID_JOBS_RESPONSE"):
        GreenhouseJobBoardConnector(http_client=client, allow_unsafe_http_client=True).health_check(
            context={"board_token": "example"},
        )


def test_greenhouse_health_check_sanitizes_upstream_failures():
    client = FakeClient([response({"error": "private upstream details"}, status_code=503)])
    with pytest.raises(AcquisitionAdapterError) as error:
        GreenhouseJobBoardConnector(http_client=client, allow_unsafe_http_client=True).health_check(
            context={"board_token": "example"},
        )
    assert error.value.status == AcquisitionOutcome.UNAVAILABLE
    assert error.value.error_category == "UPSTREAM_UNAVAILABLE"
    assert "private upstream details" not in str(error.value)



def test_greenhouse_preserves_valid_jobs_when_a_record_is_malformed():
    client = FakeClient([
        response({"name": "Example Company"}),
        response({"jobs": [
            {
                "id": 1,
                "title": "Product Manager",
                "absolute_url": "https://boards.greenhouse.io/example/jobs/1",
            },
            {"id": 2, "title": "Missing URL"},
        ]}),
    ])

    result = GreenhouseJobBoardConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"board_token": "example"},
    )

    assert result.status == AcquisitionOutcome.PARTIAL
    assert len(result.records) == 1
    assert result.error_category == "GREENHOUSE_INVALID_JOB_RECORD"
    assert result.failures[0].category == "GREENHOUSE_INVALID_JOB_RECORD"


def test_greenhouse_caps_response_bytes_during_streaming_before_json_parsing(monkeypatch):
    monkeypatch.setattr(GreenhouseJobBoardConnector, "MAX_RESPONSE_BYTES", 10)
    streamed = ChunkedResponse([b'{"name":', b' "Example"', b' Company"}'])
    client = StreamingFakeClient(streamed)

    with pytest.raises(AcquisitionAdapterError) as error:
        GreenhouseJobBoardConnector(http_client=client, allow_unsafe_http_client=True).health_check(
            context={"board_token": "example"},
        )

    assert error.value.status == AcquisitionOutcome.FAILED
    assert error.value.error_category == "GREENHOUSE_RESPONSE_TOO_LARGE"
    assert streamed.consumed_chunks == 2
    assert streamed.consumed_chunks < len(streamed.chunks)


def test_greenhouse_caps_job_count_and_reports_partial_result(monkeypatch):
    monkeypatch.setattr(GreenhouseJobBoardConnector, "MAX_ITEMS", 1)
    client = FakeClient([
        response({"name": "Example Company"}),
        response({"jobs": [
            {
                "id": 1,
                "title": "Product Manager",
                "absolute_url": "https://boards.greenhouse.io/example/jobs/1",
            },
            {
                "id": 2,
                "title": "Technical Product Manager",
                "absolute_url": "https://boards.greenhouse.io/example/jobs/2",
            },
        ]}),
    ])

    result = GreenhouseJobBoardConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"board_token": "example"},
    )

    assert result.status == AcquisitionOutcome.PARTIAL
    assert len(result.records) == 1
    assert result.error_category == "MAX_ITEMS_LIMIT_REACHED"
