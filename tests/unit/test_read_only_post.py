from contextlib import contextmanager
import json

import httpx
import pytest

from app.infrastructure.acquisition.public_http import ResponseTooLargeError
from app.infrastructure.acquisition.read_only_post import (
    bounded_read_only_post_json,
    validate_read_only_post_body,
)


class FakePostClient:
    def __init__(self, response=None):
        self.response = response or httpx.Response(
            200,
            json={"jobs": []},
            headers={"content-type": "application/json"},
            request=httpx.Request("POST", "https://api.example.com/search"),
        )
        self.calls = []

    @contextmanager
    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        yield self.response


def test_read_only_post_serializes_json_object_and_uses_post():
    client = FakePostClient()
    result = bounded_read_only_post_json(
        client,
        "https://api.example.com/search",
        body={"query": "product manager", "limit": 10},
        headers={"Authorization": "Bearer test-secret"},
        timeout=3,
    )

    assert json.loads(result) == {"jobs": []}
    method, url, options = client.calls[0]
    assert method == "POST"
    assert url == "https://api.example.com/search"
    assert json.loads(options["content"]) == {"query": "product manager", "limit": 10}
    assert options["headers"]["Content-Type"] == "application/json"
    assert options["headers"]["Authorization"] == "Bearer test-secret"
    assert options["timeout"] == 3


@pytest.mark.parametrize("body", [None, [], "query", 1, True])
def test_read_only_post_rejects_non_object_body(body):
    with pytest.raises(ValueError, match="JSON object"):
        validate_read_only_post_body(body)


@pytest.mark.parametrize("body", [{"value": float("nan")}, {"value": float("inf")}])
def test_read_only_post_rejects_non_finite_numbers(body):
    with pytest.raises(ValueError, match="finite JSON values"):
        validate_read_only_post_body(body)


def test_read_only_post_rejects_oversized_request_body():
    with pytest.raises(ValueError, match="byte limit"):
        validate_read_only_post_body({"query": "x" * 100}, max_bytes=20)


def test_read_only_post_rejects_redirects():
    response = httpx.Response(
        200,
        json={"jobs": []},
        request=httpx.Request("POST", "https://api.example.com/search"),
    )
    response.history = [httpx.Response(
        302,
        headers={"location": "https://other.example.com/search"},
        request=httpx.Request("POST", "https://api.example.com/search"),
    )]
    client = FakePostClient(response)

    with pytest.raises(httpx.HTTPError, match="Redirects are not permitted"):
        bounded_read_only_post_json(
            client, "https://api.example.com/search", body={"query": "jobs"}
        )


def test_read_only_post_rejects_non_json_response():
    response = httpx.Response(
        200,
        content=b"<html>no</html>",
        headers={"content-type": "text/html"},
        request=httpx.Request("POST", "https://api.example.com/search"),
    )
    with pytest.raises(ValueError, match="unexpected content type"):
        bounded_read_only_post_json(
            FakePostClient(response),
            "https://api.example.com/search",
            body={"query": "jobs"},
        )


def test_read_only_post_bounds_response_size():
    response = httpx.Response(
        200,
        content=b"x" * 32,
        headers={"content-type": "application/json"},
        request=httpx.Request("POST", "https://api.example.com/search"),
    )
    with pytest.raises(ResponseTooLargeError):
        bounded_read_only_post_json(
            FakePostClient(response),
            "https://api.example.com/search",
            body={"query": "jobs"},
            max_response_bytes=16,
        )
