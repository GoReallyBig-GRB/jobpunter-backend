import json

import pytest

from app.application.jobs import ats_boards
from app.application.jobs.listing_freshness import is_full_listing_connector


def fetch_of(status, payload=None, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    return lambda url: (status, body)


@pytest.mark.parametrize("provider,payload", [
    ("lever", [{"id": "1"}]), ("ashby", {"jobs": [{"title": "x"}]}), ("greenhouse", {"jobs": [{"id": 1}]}),
    ("workable", {"name": "Acme", "jobs": [{"shortcode": "A1"}]}),
])
def test_probe_accepts_boards_with_open_jobs(provider, payload):
    result = ats_boards.probe_board(provider, "acme", fetch_of(200, payload))
    assert result.ok and result.job_count == 1


@pytest.mark.parametrize("fetch,reason", [
    (fetch_of(404, {}), "BOARD_NOT_FOUND"),
    (fetch_of(500, {}), "HTTP_500"),
    (fetch_of(200, {"jobs": []}), "NO_OPEN_JOBS"),
    (fetch_of(200, {"other": 1}), "UNEXPECTED_SHAPE"),
    (fetch_of(200, raw=b"<html>"), "NOT_JSON"),
    (fetch_of(200, raw=b""), "EMPTY_OR_TOO_LARGE"),
])
def test_probe_rejects_unusable_boards_with_a_reason(fetch, reason):
    assert ats_boards.probe_board("ashby", "acme", fetch).reason == reason


def test_probe_never_raises_and_rejects_bad_tokens_without_a_request():
    def boom(url):
        raise OSError("down")

    assert ats_boards.probe_board("lever", "acme", boom).reason == "REQUEST_FAILED"
    calls = []
    for token in ["../etc", "a/b", "", "a b", "x" * 101]:
        assert ats_boards.probe_board("lever", token, lambda u: calls.append(u)).reason == "INVALID_TOKEN"
    assert calls == [] and ats_boards.probe_board("nope", "a").reason == "UNKNOWN_PROVIDER"


def test_generated_connectors_are_known_complete_listings_so_freshness_applies():
    for provider in ats_boards.PROVIDERS:
        kind, config = ats_boards.connector_for(provider, "acme", "Acme")
        assert is_full_listing_connector(kind, config)


def test_display_name_and_class_evidence():
    assert ats_boards.display_name("my-company_x") == "My Company X"
    assert ats_boards.CLASS_APPROVAL_REFERENCE.startswith("class-approval:")


def test_every_generated_json_api_configuration_is_accepted_by_the_connector_validator(monkeypatch):
    """A config the connector rejects would register fine and then fail every scheduled run."""
    import socket

    from app.infrastructure.acquisition.json_api import JsonApiConnector

    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))],
    )

    for provider in ats_boards.PROVIDERS:
        kind, config = ats_boards.connector_for(provider, "acme", "Acme")
        if kind == "JSON_API":
            JsonApiConnector.validate_context(config)


def test_workable_configuration_requests_descriptions_without_a_query_in_the_target_url():
    kind, config = ats_boards.connector_for("workable", "acme", "Acme")
    assert kind == "JSON_API"
    assert config["endpoint_url"] == "https://apply.workable.com/api/v1/widget/accounts/acme"
    assert config["base_params"] == {"details": "true"} and config["items_path"] == "jobs"
