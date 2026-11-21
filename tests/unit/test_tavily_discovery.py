"""Tavily client contract, privacy allow-list and ATS token extraction (no network, no database)."""
import json

import httpx
import pytest

from app.application.jobs import tavily_discovery as td
from app.infrastructure.search import tavily


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("JOBPUNTER_TAVILY_API_KEY", "tvly-test-key")


def test_payload_contains_only_allow_listed_fields_and_basic_depth():
    payload = tavily.build_search_payload("retail jobs Nigeria", td.INCLUDE_DOMAINS, 99)
    assert set(payload) <= tavily.ALLOWED_REQUEST_FIELDS
    assert payload["search_depth"] == "basic" and payload["max_results"] == 20
    assert payload["include_domains"] == td.INCLUDE_DOMAINS


def test_every_planned_query_is_a_fixed_system_string_without_personal_markers():
    keys = [k for k, _t in td.QUERY_PLAN]
    assert len(keys) == len(set(keys)) == len(td.SECTORS) * len(td.MARKETS)
    for _key_, text in td.QUERY_PLAN:
        assert "@" not in text and len(text) <= tavily.MAX_QUERY_CHARS


def test_search_sends_bearer_key_posts_json_and_parses_urls_and_usage():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        seen["url"] = str(request.url)
        return httpx.Response(200, json={
            "results": [{"url": "https://jobs.lever.co/acme/1"}, {"title": "no url"}],
            "usage": {"credits": 1}, "request_id": "req-1",
        })

    result = tavily.search("sales jobs Lagos", td.INCLUDE_DOMAINS, client=_client(handler))
    assert result.ok and result.urls == ["https://jobs.lever.co/acme/1"]
    assert result.credits == 1 and result.request_id == "req-1"
    assert seen["auth"] == "Bearer tvly-test-key" and seen["url"] == tavily.SEARCH_URL
    assert set(seen["body"]) <= tavily.ALLOWED_REQUEST_FIELDS


@pytest.mark.parametrize("status,outcome", [
    (401, "INVALID_KEY"), (429, "RATE_LIMITED"), (432, "LIMIT_EXCEEDED"), (433, "LIMIT_EXCEEDED"),
    (500, "FAILED"), (422, "FAILED"),
])
def test_provider_errors_are_classified_without_raising(status, outcome):
    result = tavily.search(
        "x jobs", td.INCLUDE_DOMAINS,
        client=_client(lambda r: httpx.Response(status, headers={"retry-after": "30"}, json={"detail": {}})),
    )
    assert not result.ok and result.outcome == outcome and result.http_status == status


def test_network_failure_and_malformed_body_do_not_raise():
    def boom(request):
        raise httpx.ConnectError("down")

    assert tavily.search("x", td.INCLUDE_DOMAINS, client=_client(boom)).outcome == "FAILED"
    bad = tavily.search("x", td.INCLUDE_DOMAINS, client=_client(lambda r: httpx.Response(200, content=b"not json")))
    assert bad.outcome == "FAILED"
    shape = tavily.search("x", td.INCLUDE_DOMAINS, client=_client(lambda r: httpx.Response(200, json={"results": 5})))
    assert shape.outcome == "FAILED"


def test_search_refuses_when_no_key_is_configured(monkeypatch):
    monkeypatch.delenv("JOBPUNTER_TAVILY_API_KEY")
    assert tavily.api_key() is None
    with pytest.raises(tavily.TavilyNotConfigured):
        tavily.search("x", td.INCLUDE_DOMAINS)


@pytest.mark.parametrize("url,expected", [
    ("https://boards.greenhouse.io/acme/jobs/123", ("greenhouse", "acme")),
    ("https://job-boards.greenhouse.io/acme", ("greenhouse", "acme")),
    ("https://boards.eu.greenhouse.io/euco/jobs/9", ("greenhouse", "euco")),
    ("https://jobs.lever.co/spotify/abc-123", ("lever", "spotify")),
    ("https://jobs.ashbyhq.com/Ramp/xyz", ("ashby", "Ramp")),
    ("https://apply.workable.com/kuda/j/ABC123/", ("workable", "kuda")),
    ("https://apply.workable.com/j/ABC123/", None),  # short job link carries no account token
    ("https://boards.greenhouse.io/embed/job_app?token=1", None),
    ("https://example.com/acme", None),
    ("https://jobs.lever.co/", None),
    ("ftp://jobs.lever.co/acme", None),
    ("https://jobs.lever.co/../x", None),
    ("not a url", None),
])
def test_token_extraction(url, expected):
    assert td.extract_board(url) == expected
