from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest

from app.infrastructure.connectors.greenhouse import GreenhouseJobBoardConnector


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    @contextmanager
    def stream(self, method, url, *, params=None, headers=None):
        assert method == "GET"
        self.calls.append((url, params))
        yield self.responses.pop(0)


def response(payload, status_code=200):
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("GET", "https://boards-api.greenhouse.io/test"),
    )


def test_greenhouse_connector_rejects_oversized_stream_without_reading_remaining_chunks():
    consumed = []

    class ChunkedResponse:
        def raise_for_status(self):
            return None

        def iter_bytes(self, chunk_size):
            assert chunk_size == 64 * 1024
            for chunk in (b"1234", b"5678", b"9", b"never-read"):
                consumed.append(chunk)
                yield chunk

    class Client:
        @contextmanager
        def stream(self, method, url, *, params=None, headers=None):
            assert method == "GET"
            yield ChunkedResponse()

    connector = GreenhouseJobBoardConnector(http_client=Client())
    connector.MAX_RESPONSE_BYTES = 8

    with pytest.raises(RuntimeError, match="greenhouse_response_too_large"):
        connector._get_json(connector._http_client, "https://boards-api.greenhouse.io/test")

    assert consumed == [b"1234", b"5678", b"9"]


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

    items = GreenhouseJobBoardConnector(http_client=client).acquire(
        source_id=source_id,
        context={"board_token": "example"},
    )

    assert len(items) == 1
    item = items[0]
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


@pytest.mark.parametrize("board_token", ["", "../private", "board/token", "https://evil.test", "a b"])
def test_greenhouse_connector_rejects_invalid_board_tokens_before_network_access(board_token):
    client = FakeClient([])
    with pytest.raises(ValueError, match="board_token"):
        GreenhouseJobBoardConnector(http_client=client).acquire(
            source_id=uuid4(),
            context={"board_token": board_token},
        )
    assert client.calls == []


def test_greenhouse_connector_returns_empty_list_for_empty_board():
    client = FakeClient([
        response({"name": "Example Company"}),
        response({"jobs": []}),
    ])

    items = GreenhouseJobBoardConnector(http_client=client).acquire(
        source_id=uuid4(),
        context={"board_token": "example"},
    )

    assert items == []


def test_greenhouse_connector_sanitizes_http_failures():
    client = FakeClient([
        response({"error": "sensitive response body"}, status_code=503),
    ])

    with pytest.raises(RuntimeError, match="greenhouse_request_failed") as error:
        GreenhouseJobBoardConnector(http_client=client).acquire(
            source_id=uuid4(),
            context={"board_token": "example"},
        )
    assert "sensitive response body" not in str(error.value)


def test_greenhouse_connector_fails_closed_on_malformed_job_record():
    client = FakeClient([
        response({"name": "Example Company"}),
        response({"jobs": [{"id": 1, "title": "No URL"}]}),
    ])

    with pytest.raises(RuntimeError, match="greenhouse_invalid_job_record"):
        GreenhouseJobBoardConnector(http_client=client).acquire(
            source_id=uuid4(),
            context={"board_token": "example"},
        )


def test_greenhouse_health_check_verifies_board_and_jobs_without_acquiring_records():
    client = FakeClient([
        response({"name": "Example Company"}),
        response({"jobs": [{"id": 1}, {"id": 2}]}),
    ])

    result = GreenhouseJobBoardConnector(http_client=client).health_check(
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
    with pytest.raises(RuntimeError, match="greenhouse_invalid_jobs_response"):
        GreenhouseJobBoardConnector(http_client=client).health_check(
            context={"board_token": "example"},
        )


def test_greenhouse_health_check_sanitizes_upstream_failures():
    client = FakeClient([response({"error": "private upstream details"}, status_code=503)])
    with pytest.raises(RuntimeError, match="greenhouse_request_failed") as error:
        GreenhouseJobBoardConnector(http_client=client).health_check(
            context={"board_token": "example"},
        )
    assert "private upstream details" not in str(error.value)
