import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.application.jobs.adaptive_dispatcher import AdaptiveAcquisitionDispatcher
from app.application.jobs.discovery_intake import intake_discovered_urls
from app.application.jobs.source_intelligence import (
    approve_source_intelligence,
    record_source_evidence,
    register_discovered_source,
    source_intelligence_is_executable,
)
from app.application.jobs.url_source_resolver import ResolutionSignals, UrlSourceResolver
from app.application.jobs.web_discovery import CoverageMeasurement, persist_coverage_measurement
from app.infrastructure.acquisition.dispatch_ledger import SqlAlchemyDispatchAttemptLedger
from app.infrastructure.db.models.identity import User
from app.infrastructure.db.models.source_intelligence import (
    AcquisitionDispatchAttempt,
    DiscoveryCoverageObservation,
    SourceDiscoveryCandidate,
    SourceIntelligenceObservation,
)
from app.ports.acquisition import AcquisitionBatchResult, AcquisitionOutcome
from app.ports.discovery import DiscoveredUrl
from tests.integration.test_admin_governance_postgres import db  # noqa: F401

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


def test_dynamic_source_remains_non_executable_until_evidence_review_and_approval(db):  # noqa: F811
    reviewer = User(display_name=f"Source reviewer {uuid4()}")
    db.add(reviewer)
    db.flush()
    profile = register_discovered_source(db, "https://new-jobs.example.org/openings")
    assert profile.status == "REVIEW_REQUIRED"
    assert not source_intelligence_is_executable(profile)

    record_source_evidence(db, profile, "CAPABILITY", {"routes": ["PUBLIC_HTTP", "SCRAPY"]})
    record_source_evidence(db, profile, "MARKET", {"scope": ["NIGERIA", "INTERNATIONAL"]})
    record_source_evidence(
        db,
        profile,
        "PERMISSION",
        {
            "access_method": "PUBLIC_JOB_PAGES",
            "terms_reference": "https://new-jobs.example.org/terms",
            "rate_limit_policy": "one request per second",
            "rate_limit_reference": "https://new-jobs.example.org/robots.txt",
        },
    )
    record_source_evidence(db, profile, "HEALTH", {"success_ratio": 1.0})
    record_source_evidence(db, profile, "YIELD", {"job_count": 12})
    assert not source_intelligence_is_executable(profile)

    approve_source_intelligence(db, profile, reviewer_user_id=reviewer.id)
    db.flush()
    assert source_intelligence_is_executable(profile)
    assert (
        db.scalar(
            select(func.count())
            .select_from(SourceIntelligenceObservation)
            .where(SourceIntelligenceObservation.profile_id == profile.id)
        )
        == 5
    )


def test_adaptive_dispatch_attempts_are_durable_and_repeat_suppressed(db):  # noqa: F811
    profile = register_discovered_source(db, "https://aggregate.example.org/jobs")
    recommendation = UrlSourceResolver().resolve(
        "https://aggregate.example.org/jobs",
        ResolutionSignals(aggregator_domains=frozenset({"aggregate.example.org"})),
    )
    runner_calls = []
    dispatcher = AdaptiveAcquisitionDispatcher(
        SqlAlchemyDispatchAttemptLedger(db, profile_id=profile.id)
    )

    def runner(route, _url):
        runner_calls.append(route)
        return AcquisitionBatchResult(
            AcquisitionOutcome.UNAVAILABLE, error_category="UPSTREAM_UNAVAILABLE"
        )

    first = dispatcher.dispatch(
        recommendation,
        runner=runner,
        governance_guard=lambda _route, _url: None,
        max_attempts=2,
        operation_key="durable-operation",
    )
    second = dispatcher.dispatch(
        recommendation,
        runner=runner,
        governance_guard=lambda _route, _url: None,
        max_attempts=2,
        operation_key="durable-operation",
    )
    assert len(first.attempts) == 2
    assert len(second.attempts) == 1
    assert len(runner_calls) == 3
    assert (
        db.scalar(
            select(func.count())
            .select_from(AcquisitionDispatchAttempt)
            .where(AcquisitionDispatchAttempt.profile_id == profile.id)
        )
        == 3
    )


def test_discovery_coverage_measurement_is_durable(db):  # noqa: F811
    measurement = CoverageMeasurement(
        provider_key="SITEMAP",
        query_fingerprint="a" * 64,
        local_source_count=1,
        international_source_count=1,
        eligible_url_count=2,
        total_url_count=2,
    )
    row = persist_coverage_measurement(
        db, measurement, details={"markets": ["NIGERIA", "INTERNATIONAL"]}
    )
    db.flush()
    persisted = db.get(DiscoveryCoverageObservation, row.id)
    assert persisted.coverage_ratio == 1.0
    assert persisted.details["markets"] == ["NIGERIA", "INTERNATIONAL"]


def test_discovered_urls_are_deduplicated_into_review_queue(db):  # noqa: F811
    discovery = DiscoveredUrl(
        url="https://jobs.lever.co/example/role-1",
        provider_key="SITEMAP",
        source_domain="jobs.lever.co",
        market_scope="INTERNATIONAL",
        nigeria_eligible=True,
        evidence=("approved_sitemap",),
    )

    first = intake_discovered_urls(db, (discovery, discovery))
    second = intake_discovered_urls(db, (discovery,))
    db.flush()

    assert first.created_count == 1
    assert second.refreshed_count == 1
    assert (
        db.scalar(
            select(func.count())
            .select_from(SourceDiscoveryCandidate)
            .where(SourceDiscoveryCandidate.normalized_url == discovery.url)
        )
        == 1
    )
    assert second.candidates[0].status == "REVIEW_REQUIRED"
