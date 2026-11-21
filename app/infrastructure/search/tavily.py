"""Minimal Tavily Search client for system-initiated company discovery.

Privacy contract: the request body is built only from the allow-listed fields below, so nothing
about a candidate can reach the provider. Queries come from a fixed system query plan.
The API key is read from JOBPUNTER_TAVILY_API_KEY (Render environment) and never logged.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import httpx

from app.infrastructure.acquisition.public_http import PublicAddressHTTPTransport

SEARCH_URL = "https://api.tavily.com/search"
TIMEOUT_SECONDS = 20.0
MAX_RESPONSE_BYTES = 2_000_000
ALLOWED_REQUEST_FIELDS = frozenset({"query", "search_depth", "max_results", "include_domains", "include_usage"})
MAX_QUERY_CHARS = 200


class TavilyNotConfigured(RuntimeError):
    """No API key is set."""


@dataclass(frozen=True)
class TavilyResult:
    ok: bool
    http_status: int | None
    outcome: str  # SUCCEEDED, FAILED, RATE_LIMITED, LIMIT_EXCEEDED, INVALID_KEY
    urls: list[str] = field(default_factory=list)
    credits: int = 0
    request_id: str | None = None
    retry_after_seconds: int | None = None


def api_key() -> str | None:
    key = os.getenv("JOBPUNTER_TAVILY_API_KEY", "").strip()
    return key or None


def build_search_payload(query: str, include_domains: list[str], max_results: int = 20) -> dict:
    """The only place a request body is created; every key must be on the allow-list."""
    query = " ".join(str(query).split())[:MAX_QUERY_CHARS]
    if not query:
        raise ValueError("query must not be empty")
    payload = {
        "query": query,
        "search_depth": "basic",  # 1 credit; advanced costs 2
        "max_results": max(1, min(int(max_results), 20)),
        "include_domains": [d for d in include_domains if isinstance(d, str) and d][:50],
        "include_usage": True,
    }
    assert set(payload) <= ALLOWED_REQUEST_FIELDS
    return payload


def _classify(status: int) -> str:
    if status == 401:
        return "INVALID_KEY"
    if status == 429:
        return "RATE_LIMITED"
    if status in (432, 433):
        return "LIMIT_EXCEEDED"
    return "FAILED"


def search(
    query: str, include_domains: list[str], *, max_results: int = 20, client: httpx.Client | None = None,
) -> TavilyResult:
    """One search call. Never raises for provider or network failures; returns an outcome."""
    key = api_key()
    if key is None:
        raise TavilyNotConfigured("JOBPUNTER_TAVILY_API_KEY is not set")
    payload = build_search_payload(query, include_domains, max_results)
    owns = client is None
    http = client or httpx.Client(
        timeout=TIMEOUT_SECONDS, follow_redirects=False, trust_env=False,
        transport=PublicAddressHTTPTransport(),
    )
    try:
        with http.stream(
            "POST", SEARCH_URL, json=payload,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        ) as response:
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    return TavilyResult(False, response.status_code, "FAILED")
            status = response.status_code
            retry_after = response.headers.get("retry-after")
    except httpx.HTTPError:
        return TavilyResult(False, None, "FAILED")
    finally:
        if owns:
            http.close()
    if status != 200:
        wait = int(retry_after) if retry_after and retry_after.isdigit() else None
        return TavilyResult(False, status, _classify(status), retry_after_seconds=wait)
    try:
        data = json.loads(body)
    except ValueError:
        return TavilyResult(False, status, "FAILED")
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return TavilyResult(False, status, "FAILED")
    urls = [r["url"] for r in results if isinstance(r, dict) and isinstance(r.get("url"), str)]
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    credits = usage.get("credits")
    return TavilyResult(
        True, status, "SUCCEEDED", urls=urls,
        credits=int(credits) if isinstance(credits, (int, float)) and credits >= 0 else 1,
        request_id=data.get("request_id") if isinstance(data.get("request_id"), str) else None,
    )
