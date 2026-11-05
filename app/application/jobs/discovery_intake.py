"""Persist provider discoveries as review-gated, deduplicated acquisition candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.jobs.source_governance import normalize_public_target_url
from app.application.jobs.source_intelligence import (
    register_discovered_source,
    source_intelligence_is_executable,
)
from app.application.jobs.url_source_resolver import RetrievalRoute, UrlSourceResolver
from app.application.jobs.web_discovery import (
    CoverageMeasurement,
    DiscoveryProviderRegistry,
    measure_coverage,
    persist_coverage_measurement,
)
from app.infrastructure.db.models.source import Source
from app.infrastructure.db.models.source_intelligence import (
    SourceDiscoveryCandidate,
    SourceIntelligenceProfile,
)
from app.ports.discovery import DiscoveredUrl, DiscoveryQuery


@dataclass(frozen=True)
class DiscoveryIntakeResult:
    candidates: tuple[SourceDiscoveryCandidate, ...]
    created_count: int
    refreshed_count: int
    coverage: CoverageMeasurement | None = None


def discovery_candidate_is_executable(
    profile: SourceIntelligenceProfile | None, candidate: SourceDiscoveryCandidate
) -> bool:
    if profile is None:
        return False
    approved_routes = set((getattr(profile, "capabilities", None) or {}).get("routes", []))
    candidate_routes = set(getattr(candidate, "recommended_routes", None) or [])
    executable_routes = {
        route.value for route in RetrievalRoute if route != RetrievalRoute.REVIEW_REQUIRED
    }
    return bool(
        source_intelligence_is_executable(profile)
        and getattr(candidate, "profile_id", None) == getattr(profile, "id", None)
        and getattr(candidate, "status", None) in {"APPROVED", "PROCESSING"}
        and getattr(candidate, "approved_by_user_id", None) is not None
        and getattr(candidate, "approved_at", None) is not None
        and getattr(candidate, "source_id", None) is not None
        and candidate_routes
        and candidate_routes.issubset(executable_routes)
        and candidate_routes.intersection(approved_routes)
    )


def invalidate_discovery_candidate_approval(candidate: SourceDiscoveryCandidate) -> None:
    candidate.status = "REVIEW_REQUIRED"
    candidate.source_id = None
    candidate.approved_by_user_id = None
    candidate.approved_at = None
    candidate.acquired_at = None
    candidate.attempt_count = 0
    candidate.next_attempt_at = None
    candidate.lease_expires_at = None
    candidate.last_attempt_at = None
    candidate.last_error_category = None


def intake_discovered_urls(
    db: Session,
    discoveries: tuple[DiscoveredUrl, ...],
    *,
    resolver: UrlSourceResolver | None = None,
    observed_at: datetime | None = None,
) -> DiscoveryIntakeResult:
    """Normalize and upsert provider results without granting execution permission."""
    resolver = resolver or UrlSourceResolver()
    observed_at = observed_at or datetime.now(UTC)
    candidates: list[SourceDiscoveryCandidate] = []
    created_count = 0
    refreshed_count = 0
    seen_urls: set[str] = set()

    for discovery in discoveries:
        normalized_url = normalize_public_target_url(discovery.url, require_https=True)
        domain = urlsplit(normalized_url).hostname or ""
        advertised_domain = discovery.source_domain.strip().lower().rstrip(".")
        if domain != advertised_domain:
            raise ValueError("discovery source domain does not match its URL")
        provider_key = discovery.provider_key.strip().upper()
        if not provider_key:
            raise ValueError("discovery provider key must not be blank")
        market_scope = discovery.market_scope.strip().upper()
        if market_scope not in {"LOCAL", "INTERNATIONAL"}:
            raise ValueError("discovery market scope must be LOCAL or INTERNATIONAL")
        if discovery.nigeria_eligible not in {True, False, None}:
            raise ValueError("nigeria eligibility must be true, false, or null")
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)

        profile = register_discovered_source(db, normalized_url)
        recommendation = resolver.resolve(normalized_url)
        evidence = [
            {"code": "PROVIDER_EVIDENCE", "value": item}
            for item in discovery.evidence
            if isinstance(item, str) and item.strip()
        ]
        evidence.extend(
            {"code": item.code, "value": item.value} for item in recommendation.evidence
        )
        recommended_routes = [route.value for route in recommendation.routes]
        candidate = db.scalar(
            select(SourceDiscoveryCandidate)
            .where(SourceDiscoveryCandidate.normalized_url == normalized_url)
            .with_for_update()
        )
        if candidate is None:
            candidate = SourceDiscoveryCandidate(
                profile_id=profile.id,
                normalized_url=normalized_url,
                provider_key=provider_key,
                source_domain=domain,
                market_scope=market_scope,
                nigeria_eligible=discovery.nigeria_eligible,
                source_kind=recommendation.source_kind.value,
                recommended_routes=recommended_routes,
                confidence=recommendation.confidence,
                evidence=evidence,
                status="REVIEW_REQUIRED",
                first_discovered_at=observed_at,
                last_discovered_at=observed_at,
            )
            db.add(candidate)
            db.flush()
            created_count += 1
        else:
            governance_values = {
                "profile_id": profile.id,
                "provider_key": provider_key,
                "source_domain": domain,
                "market_scope": market_scope,
                "nigeria_eligible": discovery.nigeria_eligible,
                "source_kind": recommendation.source_kind.value,
                "recommended_routes": recommended_routes,
                "confidence": recommendation.confidence,
                "evidence": evidence,
            }
            if any(
                getattr(candidate, field, None) != value
                for field, value in governance_values.items()
            ):
                invalidate_discovery_candidate_approval(candidate)
                candidate.last_error_category = "CANDIDATE_METADATA_CHANGED"
            candidate.profile_id = profile.id
            candidate.provider_key = provider_key
            candidate.source_domain = domain
            candidate.market_scope = market_scope
            candidate.nigeria_eligible = discovery.nigeria_eligible
            candidate.source_kind = recommendation.source_kind.value
            candidate.recommended_routes = recommended_routes
            candidate.confidence = recommendation.confidence
            candidate.evidence = evidence
            candidate.last_discovered_at = observed_at
            candidate.revision += 1
            db.flush()
            refreshed_count += 1
        candidates.append(candidate)

    return DiscoveryIntakeResult(tuple(candidates), created_count, refreshed_count)


def run_provider_intake(
    db: Session,
    registry: DiscoveryProviderRegistry,
    *,
    provider_key: str,
    query: DiscoveryQuery,
    context: dict,
    resolver: UrlSourceResolver | None = None,
) -> DiscoveryIntakeResult:
    discoveries = registry.discover(provider_key, query, context)
    bounded = tuple(discoveries[: query.max_urls])
    coverage = measure_coverage(query, bounded, provider_key)
    persist_coverage_measurement(
        db,
        coverage,
        details={"terms": list(query.terms), "nigeria_eligible_only": query.nigeria_eligible_only},
    )
    result = intake_discovered_urls(db, bounded, resolver=resolver)
    return DiscoveryIntakeResult(
        result.candidates,
        result.created_count,
        result.refreshed_count,
        coverage,
    )


def approve_discovery_candidate(
    db: Session,
    candidate: SourceDiscoveryCandidate,
    profile: SourceIntelligenceProfile,
    *,
    reviewer_user_id: UUID,
    source_id: UUID | None = None,
) -> None:
    if candidate.profile_id != profile.id:
        raise ValueError("candidate does not belong to the source intelligence profile")
    if not source_intelligence_is_executable(profile):
        raise ValueError("source intelligence must be approved before its candidates")
    approved_routes = set((profile.capabilities or {}).get("routes", []))
    candidate_routes = set(candidate.recommended_routes or [])
    executable_routes = {
        route.value for route in RetrievalRoute if route != RetrievalRoute.REVIEW_REQUIRED
    }
    if (
        not candidate_routes
        or not candidate_routes.issubset(executable_routes)
        or not candidate_routes.intersection(approved_routes)
    ):
        raise ValueError("candidate has no approved executable retrieval route")
    if source_id is not None:
        source = db.get(Source, source_id)
        if source is None:
            raise ValueError("candidate source does not exist")
        base_url = getattr(source, "base_url", None)
        source_domain = (
            urlsplit(normalize_public_target_url(base_url)).hostname
            if isinstance(base_url, str) and base_url.strip()
            else None
        )
        if source_domain != candidate.source_domain:
            raise ValueError("candidate source domain does not match the governed source")
    candidate.status = "APPROVED"
    candidate.source_id = source_id or getattr(candidate, "source_id", None)
    candidate.approved_by_user_id = reviewer_user_id
    candidate.approved_at = datetime.now(UTC)
    candidate.acquired_at = None
    candidate.next_attempt_at = None
    candidate.lease_expires_at = None
    candidate.last_error_category = None
    candidate.revision += 1
    db.flush()


def mark_discovery_candidate_acquired(
    db: Session, candidate: SourceDiscoveryCandidate, *, acquired_at: datetime | None = None
) -> None:
    if candidate.status not in {"APPROVED", "PROCESSING"}:
        raise ValueError("only approved or processing discovery candidates can be marked acquired")
    candidate.status = "ACQUIRED"
    candidate.acquired_at = acquired_at or datetime.now(UTC)
    candidate.next_attempt_at = None
    candidate.lease_expires_at = None
    candidate.last_error_category = None
    candidate.revision += 1
    db.flush()
