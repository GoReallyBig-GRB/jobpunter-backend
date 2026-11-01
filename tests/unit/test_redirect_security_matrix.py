"""L1.5 cross-adapter matrix: no HTTP connector ever follows or contacts a redirect target.

For each connector x redirect status x hostile Location, the connector must issue exactly one request
(the configured one, with follow_redirects disabled), never request the Location, and report a
non-success outcome instead of records.
"""
import socket
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest

from app.infrastructure.acquisition.json_api import JsonApiConnector
from app.infrastructure.acquisition.rss_atom import FeedConnectorError, RssAtomConnector
from app.infrastructure.connectors.greenhouse import GreenhouseJobBoardConnector
from app.ports.acquisition import AcquisitionAdapterError, AcquisitionOutcome

HOSTILE_LOCATIONS = [
    "http://127.0.0.1/admin",
    "http://[::1]/admin",
    "http://10.0.0.5/internal",
    "http://169.254.169.254/latest/meta-data/",
    "http://192.168.1.1/",
    "file:///etc/passwd",
    "//127.0.0.1/protocol-relative",
    "https://forbidden-but-public.example.net/jobs",
]
STATUSES = [301, 302, 303, 307, 308]


def _public_dns(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))]


class RedirectingClient:
    """Answers every request with a redirect and records every URL it is asked to fetch."""

    def __init__(self, status, location):
        self.status = status
        self.location = location
        self.requested = []
        self.follow_flags = []

    def _response(self, url):
        return httpx.Response(self.status, headers={"location": self.location},
                              request=httpx.Request("GET", url))

    def get(self, url, *, params=None, headers=None):
        self.requested.append(url)
        return self._response(url)

    @contextmanager
    def stream(self, method, url, *, params=None, headers=None, timeout=None, follow_redirects=None):
        self.requested.append(url)
        self.follow_flags.append(follow_redirects)
        yield self._response(url)


def _run_json_api(client):
    result = JsonApiConnector(http_client=client, allow_unsafe_http_client=True).acquire(
        source_id=uuid4(),
        context={"endpoint_url": "https://api.example.com/jobs",
                 "acquisition_policy_revalidator": lambda **kwargs: None},
    )
    return result.status, result.error_category, bool(result.records)


def _run_rss(client):
    try:
        RssAtomConnector(http_client=client, allow_unsafe_http_client=True).acquire(
            source_id=uuid4(), context={"feed_url": "https://feeds.example.com/jobs"})
    except FeedConnectorError as exc:
        return exc.status, exc.error_category, False
    return AcquisitionOutcome.SUCCEEDED, None, True


def _run_greenhouse_acquire(client):
    try:
        result = GreenhouseJobBoardConnector(http_client=client, allow_unsafe_http_client=True).acquire(
            source_id=uuid4(), context={"board_token": "example-board"})
    except AcquisitionAdapterError as exc:
        return exc.status, exc.error_category, False
    return result.status, result.error_category, bool(result.records)


def _run_greenhouse_health(client):
    try:
        GreenhouseJobBoardConnector(http_client=client, allow_unsafe_http_client=True).health_check(
            context={"board_token": "example-board"})
    except AcquisitionAdapterError as exc:
        return exc.status, exc.error_category, False
    return AcquisitionOutcome.SUCCEEDED, None, True


RUNNERS = {
    "JSON_API": _run_json_api,
    "RSS_ATOM": _run_rss,
    "GREENHOUSE_acquire": _run_greenhouse_acquire,
    "GREENHOUSE_health": _run_greenhouse_health,
}


@pytest.mark.parametrize("location", HOSTILE_LOCATIONS)
@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("adapter", sorted(RUNNERS))
def test_connector_never_contacts_a_redirect_target(monkeypatch, adapter, status, location):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    client = RedirectingClient(status, location)

    outcome, category, produced_records = RUNNERS[adapter](client)

    assert len(client.requested) == 1, client.requested  # only the configured URL, never the Location
    assert location not in client.requested
    assert all(flag is False for flag in client.follow_flags)
    assert not produced_records
    assert outcome != AcquisitionOutcome.SUCCEEDED
    assert category == "REDIRECT_BLOCKED"
