from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.v1 import admin_discovery
from app.application.jobs.discovery_intake import (
    approve_discovery_candidate,
    intake_discovered_urls,
    mark_discovery_candidate_acquired,
    run_provider_intake,
)
from app.application.jobs.web_discovery import DiscoveryProviderRegistry, ProviderControl
from app.infrastructure.db.models.source import Source
from app.infrastructure.db.models.source_intelligence import (
    DiscoveryCoverageObservation,
    SourceDiscoveryCandidate,
    SourceIntelligenceProfile,
)
from app.ports.discovery import DiscoveredUrl, DiscoveryQuery


class _MemorySession:
    def __init__(self):
        self.profiles = {}
        self.candidates = {}
        self.coverage = []
        self.sources = {}

    def scalar(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        value = next(iter(statement.compile().params.values()))
        if entity is SourceIntelligenceProfile:
            return self.profiles.get(value)
        if entity is SourceDiscoveryCandidate:
            return self.candidates.get(value)
        raise AssertionError(f"unexpected select entity: {entity}")

    def add(self, row):
        row.id = getattr(row, "id", None) or uuid4()
        if hasattr(row, "revision") and row.revision is None:
            row.revision = 1
        if isinstance(row, SourceIntelligenceProfile):
            row.status = row.status or "REVIEW_REQUIRED"
            row.capabilities = row.capabilities or {}
            row.market_coverage = row.market_coverage or {}
            row.permission_evidence = row.permission_evidence or {}
            row.health_evidence = row.health_evidence or {}
            row.yield_evidence = row.yield_evidence or {}
            self.profiles[row.domain] = row
        elif isinstance(row, SourceDiscoveryCandidate):
            self.candidates[row.normalized_url] = row
        elif isinstance(row, DiscoveryCoverageObservation):
            self.coverage.append(row)

    def flush(self):
        return None

    def commit(self):
        return None

    def get(self, model, identity):
        if model is Source:
            return self.sources.get(identity)
        raise AssertionError(f"unexpected get model: {model}")


def _discovery(url="https://jobs.lever.co/acme/123"):
    return DiscoveredUrl(
        url=url,
        provider_key="sitemap",
        source_domain="jobs.lever.co",
        market_scope="INTERNATIONAL",
        nigeria_eligible=True,
        evidence=("approved_sitemap",),
    )


def test_intake_deduplicates_and_refreshes_durable_candidates():
    db = _MemorySession()
    observed = datetime(2026, 11, 5, tzinfo=UTC)

    first = intake_discovered_urls(db, (_discovery(), _discovery()), observed_at=observed)
    second = intake_discovered_urls(db, (_discovery(),), observed_at=observed)

    assert first.created_count == 1
    assert len(first.candidates) == 1
    assert second.refreshed_count == 1
    candidate = second.candidates[0]
    assert candidate.status == "REVIEW_REQUIRED"
    assert candidate.source_kind == "KNOWN_ATS"
    assert candidate.recommended_routes == ["PUBLIC_HTTP", "SCRAPY", "PLAYWRIGHT"]
    assert candidate.revision == 2


def test_intake_rejects_provider_domain_mismatch():
    db = _MemorySession()
    discovery = _discovery("https://evil.example/jobs/1")

    with pytest.raises(ValueError, match="source domain"):
        intake_discovered_urls(db, (discovery,))


def test_provider_intake_persists_coverage_and_candidates():
    class Provider:
        provider_key = "TEST"
        cost_tier = "ZERO_COST"

        def discover(self, query, context):
            return (_discovery(),)

    db = _MemorySession()
    registry = DiscoveryProviderRegistry()
    registry.register(Provider(), ProviderControl(enabled=True, approved=True))

    result = run_provider_intake(
        db,
        registry,
        provider_key="test",
        query=DiscoveryQuery(("engineer",), max_urls=10),
        context={},
    )

    assert result.created_count == 1
    assert result.coverage is not None
    assert result.coverage.international_source_count == 1
    assert result.coverage.eligible_url_count == 1
    assert len(db.coverage) == 1


def test_candidate_approval_requires_executable_profile_and_approved_route():
    db = _MemorySession()
    reviewer = uuid4()
    profile = SimpleNamespace(
        id=uuid4(),
        status="APPROVED",
        approved_by_user_id=reviewer,
        approved_at=datetime.now(UTC),
        permission_evidence={
            "access_method": "public sitemap",
            "terms_reference": "https://jobs.lever.co/terms",
            "rate_limit_policy": "bounded",
            "rate_limit_reference": "https://jobs.lever.co/robots.txt",
        },
        capabilities={"routes": ["PUBLIC_HTTP"]},
    )
    candidate = SimpleNamespace(
        profile_id=profile.id,
        recommended_routes=["PUBLIC_HTTP", "SCRAPY"],
        status="REVIEW_REQUIRED",
        approved_by_user_id=None,
        approved_at=None,
        acquired_at=None,
        revision=1,
    )

    approve_discovery_candidate(db, candidate, profile, reviewer_user_id=reviewer)
    mark_discovery_candidate_acquired(db, candidate)

    assert candidate.status == "ACQUIRED"
    assert candidate.approved_by_user_id == reviewer
    assert candidate.acquired_at is not None


def test_candidate_approval_fails_closed_for_unapproved_profile():
    db = _MemorySession()
    profile = SimpleNamespace(
        id=uuid4(),
        status="REVIEW_REQUIRED",
        approved_by_user_id=None,
        approved_at=None,
        permission_evidence={},
        capabilities={"routes": ["PUBLIC_HTTP"]},
    )
    candidate = SimpleNamespace(
        profile_id=profile.id,
        recommended_routes=["PUBLIC_HTTP"],
        revision=1,
    )

    with pytest.raises(ValueError, match="source intelligence"):
        approve_discovery_candidate(db, candidate, profile, reviewer_user_id=uuid4())


def test_candidate_approval_binds_matching_governed_source():
    db = _MemorySession()
    source_id = uuid4()
    db.sources[source_id] = SimpleNamespace(id=source_id, base_url="https://jobs.example.org/")
    reviewer = uuid4()
    profile = SimpleNamespace(
        id=uuid4(),
        status="APPROVED",
        approved_by_user_id=reviewer,
        approved_at=datetime.now(UTC),
        permission_evidence={
            "access_method": "public page",
            "terms_reference": "https://jobs.example.org/terms",
            "rate_limit_policy": "bounded",
            "rate_limit_reference": "https://jobs.example.org/robots.txt",
        },
        capabilities={"routes": ["PUBLIC_HTTP"]},
    )
    candidate = SimpleNamespace(
        profile_id=profile.id,
        source_id=None,
        source_domain="jobs.example.org",
        recommended_routes=["PUBLIC_HTTP"],
        status="REVIEW_REQUIRED",
        revision=1,
    )

    approve_discovery_candidate(
        db, candidate, profile, reviewer_user_id=reviewer, source_id=source_id
    )

    assert candidate.status == "APPROVED"
    assert candidate.source_id == source_id


def test_admin_intake_endpoint_uses_guarded_durable_intake(monkeypatch):
    db = _MemorySession()
    audited = []
    monkeypatch.setattr(admin_discovery, "_require_admin", lambda *_args: None)
    monkeypatch.setattr(
        admin_discovery, "_audit", lambda *_args, **_kwargs: audited.append(_args[2])
    )
    payload = admin_discovery.DiscoveryIntakeRequest(
        provider_key="sitemap",
        reason="Approved provider result",
        items=[
            {
                "url": "https://jobs.lever.co/acme/123",
                "source_domain": "jobs.lever.co",
                "market_scope": "INTERNATIONAL",
                "nigeria_eligible": True,
                "evidence": ["approved_sitemap"],
            }
        ],
    )

    response = admin_discovery.intake_source_candidates(
        payload,
        SimpleNamespace(),
        SimpleNamespace(user_id=uuid4()),
        db,
    )

    assert response["summary"] == {"created_count": 1, "refreshed_count": 0}
    assert response["data"][0]["status"] == "REVIEW_REQUIRED"
    assert audited == ["SOURCE_DISCOVERY_CANDIDATES_INTAKED"]
