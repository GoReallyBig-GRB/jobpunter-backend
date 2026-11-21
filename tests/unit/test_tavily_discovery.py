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
    texts = [t for _k, t in td.QUERY_PLAN]
    assert len(texts) == len(set(texts))
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


def test_plan_covers_nigerian_and_international_markets_and_alternates_them():
    texts = [t for _k, t in td.QUERY_PLAN]
    assert any(t.endswith(" Nigeria") for t in texts) and any(t.endswith(" Lagos") for t in texts)
    for place in ("United Kingdom", "United States", "Canada", "Germany", "remote worldwide", "Kenya"):
        assert any(t.endswith(" " + place) for t in texts), place
    # The first ten searches of a day mix local and international employers.
    first = texts[:10]
    local = [t for t in first if t.rsplit(" jobs ", 1)[1] in td.LOCAL_MARKETS]
    assert 4 <= len(local) <= 6
    assert {"oil and gas", "agriculture and agribusiness", "consumer goods"} <= set(td.SECTORS)


def test_default_budget_allows_one_search_an_hour_and_stays_under_the_hard_stop():
    from app.config.settings import Settings

    s = Settings()
    assert s.tavily_daily_search_cap == 24
    assert 24 * 31 <= int(s.tavily_monthly_credit_limit * s.tavily_hard_stop_fraction)


def test_usage_is_read_from_tavily_normalised_and_cached():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["authorization"])
        return httpx.Response(200, json={
            "key": {"usage": 12, "limit": None, "search_usage": 12},
            "account": {"current_plan": "Researcher", "plan_usage": 40, "plan_limit": 1000,
                        "paygo_usage": 0, "paygo_limit": 0, "search_usage": 38},
        })

    tavily._usage_cache.update(at=0.0, value=None)
    first = tavily.fetch_usage(client=_client(handler), now=1000.0)
    assert first["available"] and first["plan"] == "Researcher"
    assert (first["plan_credits_used"], first["plan_credit_limit"], first["key_credits_used"]) == (40, 1000, 12)
    assert first["key_credit_limit"] is None
    assert tavily.fetch_usage(client=_client(handler), now=1100.0) == first and len(calls) == 1  # cached
    tavily.fetch_usage(client=_client(handler), now=1400.0)
    assert len(calls) == 2 and calls[0] == "Bearer tvly-test-key"
    tavily._usage_cache.update(at=0.0, value=None)


def test_usage_failures_are_reported_not_raised(monkeypatch):
    tavily._usage_cache.update(at=0.0, value=None)
    assert tavily.fetch_usage(client=_client(lambda r: httpx.Response(401)), use_cache=False)["error"] == "HTTP_401"
    assert tavily.fetch_usage(client=_client(lambda r: httpx.Response(200, content=b"x")), use_cache=False)["error"] == "NOT_JSON"
    monkeypatch.delenv("JOBPUNTER_TAVILY_API_KEY")
    assert tavily.fetch_usage(use_cache=False) == {"available": False, "error": "NO_API_KEY"}
