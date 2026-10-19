from contextlib import contextmanager
import json

import httpx
import pytest

from app.infrastructure.acquisition.public_http import (
    PublicAddressHTTPTransport,
    ResponseTooLargeError,
)
from app.infrastructure.acquisition.read_only_post import (
    bounded_read_only_post_json,
    validate_read_only_post_body,
)


class FakePostClient:
    def __init__(self, response=None, *, pinned=True):
        self._transport = object.__new__(PublicAddressHTTPTransport) if pinned else object()
        self._mounts = {}
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
    assert options["follow_redirects"] is False


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


def test_authorized_read_only_post_requires_persisted_configuration_and_fresh_guard():
    from app.infrastructure.acquisition.read_only_post import authorized_read_only_post_json

    client = FakePostClient()
    checks = []
    result = authorized_read_only_post_json(
        client,
        "https://api.example.com/search",
        body={"query": "jobs"},
        action="search_jobs",
        connector_type="READ_ONLY_POST_JSON_API",
        persisted_configuration={
            "request_method": "POST",
            "read_only_post_action": "search_jobs",
        },
        persisted_capabilities={"read_only_post": True},
        policy_revalidator=lambda **kwargs: checks.append(kwargs),
    )

    assert json.loads(result) == {"jobs": []}
    assert checks == [{
        "url": "https://api.example.com/search",
        "connector_type": "READ_ONLY_POST_JSON_API",
        "method": "POST",
    }]
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    ("configuration", "capabilities", "action"),
    [
        ({"request_method": "GET", "read_only_post_action": "search_jobs"},
         {"read_only_post": True}, "search_jobs"),
        ({"request_method": "POST", "read_only_post_action": "search_jobs"},
         {"read_only_post": False}, "search_jobs"),
        ({"request_method": "POST", "read_only_post_action": "search_jobs"},
         {"read_only_post": True}, "different_action"),
        ({"request_method": "POST", "read_only_post_action": ""},
         {"read_only_post": True}, ""),
        (None, {"read_only_post": True}, "search_jobs"),
        ({"request_method": "POST", "read_only_post_action": "search_jobs"},
         None, "search_jobs"),
    ],
)
def test_authorized_read_only_post_rejects_missing_or_mismatched_persisted_policy(
    configuration, capabilities, action
):
    from app.infrastructure.acquisition.read_only_post import authorized_read_only_post_json

    client = FakePostClient()
    with pytest.raises(PermissionError, match="not authorized"):
        authorized_read_only_post_json(
            client,
            "https://api.example.com/search",
            body={"query": "jobs"},
            action=action,
            connector_type="READ_ONLY_POST_JSON_API",
            persisted_configuration=configuration,
            persisted_capabilities=capabilities,
            policy_revalidator=lambda **kwargs: None,
        )
    assert client.calls == []


def test_authorized_read_only_post_fails_closed_without_policy_revalidator():
    from app.infrastructure.acquisition.read_only_post import authorized_read_only_post_json

    client = FakePostClient()
    with pytest.raises(PermissionError, match="live policy revalidation"):
        authorized_read_only_post_json(
            client,
            "https://api.example.com/search",
            body={"query": "jobs"},
            action="search_jobs",
            connector_type="READ_ONLY_POST_JSON_API",
            persisted_configuration={
                "request_method": "POST",
                "read_only_post_action": "search_jobs",
            },
            persisted_capabilities={"read_only_post": True},
            policy_revalidator=None,
        )
    assert client.calls == []


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/search",
        "https://user:pass@api.example.com/search",
        "https://api.example.com:8443/search",
        "https://localhost/search",
        "https://service.internal/search",
        "https://api.example.test/search",
    ],
)
def test_read_only_post_rejects_unsafe_target_urls_before_network(url):
    client = FakePostClient()
    with pytest.raises(ValueError):
        bounded_read_only_post_json(client, url, body={"query": "jobs"})
    assert client.calls == []


def test_read_only_post_rejects_unpinned_http_client_before_network():
    client = FakePostClient(pinned=False)
    with pytest.raises(ValueError, match="public-address-pinned"):
        bounded_read_only_post_json(
            client, "https://api.example.com/search", body={"query": "jobs"}
        )
    assert client.calls == []


def test_read_only_post_rejects_alternate_transport_mounts():
    client = FakePostClient()
    client._mounts = {"https://api.example.com": object()}
    with pytest.raises(ValueError, match="alternate HTTP transport mounts"):
        bounded_read_only_post_json(
            client, "https://api.example.com/search", body={"query": "jobs"}
        )
    assert client.calls == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "text/plain"},
        {"content-type": "text/plain"},
        {"Accept": "text/html"},
        {"accept": "text/html"},
    ],
)
def test_read_only_post_rejects_headers_that_override_json_contract(headers):
    client = FakePostClient()
    with pytest.raises(ValueError, match="(Content-Type|Accept)"):
        bounded_read_only_post_json(
            client,
            "https://api.example.com/search",
            body={"query": "jobs"},
            headers=headers,
        )
    assert client.calls == []


def test_read_only_post_rejects_missing_response_content_type():
    response = httpx.Response(
        200,
        content=b"{}",
        request=httpx.Request("POST", "https://api.example.com/search"),
    )
    with pytest.raises(ValueError, match="unexpected content type"):
        bounded_read_only_post_json(
            FakePostClient(response),
            "https://api.example.com/search",
            body={"query": "jobs"},
        )
