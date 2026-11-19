import json

import pytest

from app.application.jobs import ats_boards
from app.application.jobs.listing_freshness import is_full_listing_connector


def fetch_of(status, payload=None, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    return lambda url: (status, body)


@pytest.mark.parametrize("provider,payload", [
    ("lever", [{"id": "1"}]), ("ashby", {"jobs": [{"title": "x"}]}), ("greenhouse", {"jobs": [{"id": 1}]}),
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
