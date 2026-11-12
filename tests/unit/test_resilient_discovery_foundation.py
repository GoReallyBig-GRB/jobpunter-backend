from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.jobs.adaptive_dispatcher import (
    AdaptiveAcquisitionDispatcher,
    InMemoryAttemptLedger,
    RouteHealth,
)
from app.application.jobs.url_source_resolver import (
    ResolutionSignals,
    RetrievalRoute,
    SourceKind,
    UrlSourceResolver,
)
from app.application.jobs.web_discovery import (
    DiscoveryProviderRegistry,
    ProviderControl,
)
from app.infrastructure.discovery.sitemap import SitemapDiscoveryProvider
from app.ports.acquisition import (
    AcquisitionAdapterError,
    AcquisitionBatchResult,
    AcquisitionOutcome,
    RawAcquisition,
)
from app.ports.discovery import DiscoveryQuery


@pytest.mark.parametrize(
    "url, signals, expected_kind, expected_route",
    [
        (
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
            ResolutionSignals(),
            SourceKind.KNOWN_ATS,
            RetrievalRoute.JSON_API,
        ),
        (
            "https://api.lever.co/v0/postings/acme",
            ResolutionSignals(),
            SourceKind.KNOWN_ATS,
            RetrievalRoute.JSON_API,
        ),
        (
            "https://api.smartrecruiters.com/v1/companies/acme/postings",
            ResolutionSignals(),
            SourceKind.KNOWN_ATS,
            RetrievalRoute.JSON_API,
        ),
        (
            "https://jobs.example.org/feed",
            ResolutionSignals(content_type="application/rss+xml"),
            SourceKind.RSS_ATOM,
            RetrievalRoute.RSS_ATOM,
        ),
        (
            "https://jobs.example.org/api",
            ResolutionSignals(
                content_type="application/json", body_sample='{"jobs":[{"title":"PM","url":"/1"}]}'
            ),
            SourceKind.JSON_JOB_API,
            RetrievalRoute.JSON_API,
        ),
        (
            "https://jobs.example.org/pm",
            ResolutionSignals(
                body_sample='<script type="application/ld+json">{"@type":"JobPosting"}</script>'
            ),
            SourceKind.JSON_LD,
            RetrievalRoute.PUBLIC_HTTP,
        ),
        (
            "https://jobs.example.org/pm",
            ResolutionSignals(approved_static_html=True),
            SourceKind.APPROVED_STATIC_HTML,
            RetrievalRoute.PUBLIC_HTTP,
        ),
        (
            "https://jobs.example.org/pm",
            ResolutionSignals(javascript_required=True),
            SourceKind.JAVASCRIPT_RENDERED,
            RetrievalRoute.PLAYWRIGHT,
        ),
        (
            "https://aggregate.example.org/search",
            ResolutionSignals(aggregator_domains=frozenset({"aggregate.example.org"})),
            SourceKind.AGGREGATOR,
            RetrievalRoute.PUBLIC_HTTP,
        ),
    ],
)
def test_url_resolver_classifies_capabilities(url, signals, expected_kind, expected_route):
    result = UrlSourceResolver().resolve(url, signals)
    assert result.source_kind == expected_kind
    assert result.routes[0] == expected_route
    assert result.evidence


def test_url_resolver_sends_unknown_domains_to_review():
    result = UrlSourceResolver().resolve("https://new-source.example.org/jobs")
    assert result.source_kind == SourceKind.UNKNOWN_REVIEW_REQUIRED
    assert result.routes == (RetrievalRoute.REVIEW_REQUIRED,)
    assert result.confidence == 0.0


def _record(url: str) -> RawAcquisition:
    return RawAcquisition(
        source_id=uuid4(),
        external_record_id="1",
        source_url=url,
        raw_content={"title": "Product Manager"},
        content_hash="a" * 64,
        observed_at=datetime.now(UTC),
    )


def test_dispatcher_uses_lowest_cost_route_then_bounded_fallback_and_suppresses_repeat():
    recommendation = UrlSourceResolver().resolve(
        "https://aggregate.example.org/jobs",
        ResolutionSignals(aggregator_domains=frozenset({"aggregate.example.org"})),
    )
    ledger = InMemoryAttemptLedger()
    calls = []

    def run(route, url):
        calls.append(route)
        if route == RetrievalRoute.PUBLIC_HTTP:
            raise AcquisitionAdapterError(AcquisitionOutcome.UNAVAILABLE, "UPSTREAM_DOWN")
        return AcquisitionBatchResult(AcquisitionOutcome.SUCCEEDED, (_record(url),))

    dispatcher = AdaptiveAcquisitionDispatcher(ledger)
    first = dispatcher.dispatch(
        recommendation,
        runner=run,
        governance_guard=lambda _route, _url: None,
        operation_key="mission-1",
        max_attempts=2,
    )
    second = dispatcher.dispatch(
        recommendation,
        runner=run,
        governance_guard=lambda _route, _url: None,
        operation_key="mission-1",
        max_attempts=2,
    )

    assert calls == [RetrievalRoute.PUBLIC_HTTP, RetrievalRoute.SCRAPY, RetrievalRoute.PLAYWRIGHT]
    assert first.selected_route == RetrievalRoute.SCRAPY
    assert len(first.attempts) == 2
    assert second.selected_route == RetrievalRoute.PLAYWRIGHT


def test_dispatcher_never_bypasses_governance_or_open_circuit():
    recommendation = UrlSourceResolver().resolve(
        "https://aggregate.example.org/jobs",
        ResolutionSignals(aggregator_domains=frozenset({"aggregate.example.org"})),
    )
    calls = []

    def guard(route, _url):
        if route == RetrievalRoute.SCRAPY:
            raise PermissionError

    result = AdaptiveAcquisitionDispatcher(InMemoryAttemptLedger()).dispatch(
        recommendation,
        runner=lambda route, _url: (
            calls.append(route)
            or AcquisitionBatchResult(AcquisitionOutcome.FAILED, error_category="FAILED")
        ),
        governance_guard=guard,
        health={RetrievalRoute.PUBLIC_HTTP: RouteHealth(circuit_open=True)},
        operation_key="mission-2",
        max_attempts=2,
    )
    assert calls == [RetrievalRoute.PLAYWRIGHT]
    assert [attempt.status for attempt in result.attempts] == [
        AcquisitionOutcome.RESTRICTED,
        AcquisitionOutcome.FAILED,
    ]


def _approved_profile(domain: str):
    return SimpleNamespace(
        domain=domain,
        status="APPROVED",
        approved_by_user_id=uuid4(),
        approved_at=datetime.now(UTC),
        capabilities={"routes": ["PUBLIC_HTTP"]},
        permission_evidence={
            "access_method": "PUBLIC_SITEMAP",
            "terms_reference": f"https://{domain}/terms",
            "rate_limit_policy": "one request per second",
            "rate_limit_reference": f"https://{domain}/robots.txt",
        },
    )


def test_sitemap_provider_is_disabled_even_for_an_approved_source():
    provider = SitemapDiscoveryProvider()
    assert provider.enabled is False
    with pytest.raises(PermissionError, match="sitemap discovery is disabled"):
        provider.discover(
            DiscoveryQuery(("jobs",)),
            {
                "source_intelligence": _approved_profile("jobs.example.org"),
                "sitemap_url": "https://jobs.example.org/sitemap.xml",
            },
        )


def test_sitemap_provider_fails_closed_for_unapproved_source_intelligence():
    provider = SitemapDiscoveryProvider()
    profile = _approved_profile("jobs.example.org")
    profile.status = "REVIEW_REQUIRED"
    with pytest.raises(PermissionError, match="sitemap discovery is disabled"):
        provider.discover(
            DiscoveryQuery(("jobs",)),
            {
                "source_intelligence": profile,
                "sitemap_url": "https://jobs.example.org/sitemap.xml",
            },
        )


def test_provider_and_paid_capacity_remain_disabled_without_explicit_approval():
    class PaidProvider:
        provider_key = "PAID_SEARCH"
        cost_tier = "PAID"

        def discover(self, query, context):
            raise AssertionError("disabled paid provider must not execute")

    registry = DiscoveryProviderRegistry()
    registry.register(PaidProvider(), ProviderControl(enabled=True, approved=True))
    with pytest.raises(PermissionError, match="explicit run approval"):
        registry.discover("PAID_SEARCH", DiscoveryQuery(("jobs",)), {})
