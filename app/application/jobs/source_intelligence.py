"""Workflow for accumulating evidence and approving dynamic source intelligence."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.jobs.source_governance import normalize_public_target_url
from app.application.jobs.url_source_resolver import RetrievalRoute
from app.infrastructure.db.models.source_intelligence import (
    SourceIntelligenceObservation,
    SourceIntelligenceProfile,
)

EVIDENCE_FIELDS = {
    "CAPABILITY": "capabilities",
    "MARKET": "market_coverage",
    "PERMISSION": "permission_evidence",
    "HEALTH": "health_evidence",
    "YIELD": "yield_evidence",
}
REQUIRED_PERMISSION_FIELDS = (
    "access_method",
    "terms_reference",
    "rate_limit_policy",
    "rate_limit_reference",
)


def register_discovered_source(db: Session, url: str) -> SourceIntelligenceProfile:
    from urllib.parse import urlsplit

    domain = urlsplit(normalize_public_target_url(url)).hostname or ""
    row = db.scalar(
        select(SourceIntelligenceProfile).where(SourceIntelligenceProfile.domain == domain)
    )
    if row is None:
        row = SourceIntelligenceProfile(domain=domain, status="REVIEW_REQUIRED")
        db.add(row)
        db.flush()
    return row


def record_source_evidence(
    db: Session,
    profile: SourceIntelligenceProfile,
    observation_type: str,
    evidence: dict,
) -> SourceIntelligenceObservation:
    kind = observation_type.strip().upper()
    if kind not in EVIDENCE_FIELDS:
        raise ValueError("unsupported source intelligence observation type")
    if not isinstance(evidence, dict) or not evidence:
        raise ValueError("source intelligence evidence must be a non-empty object")
    current = dict(getattr(profile, EVIDENCE_FIELDS[kind]) or {})
    current.update(evidence)
    setattr(profile, EVIDENCE_FIELDS[kind], current)
    profile.revision += 1
    observation = SourceIntelligenceObservation(
        profile_id=profile.id, observation_type=kind, evidence=dict(evidence)
    )
    db.add(observation)
    db.flush()
    return observation


def approve_source_intelligence(
    db: Session,
    profile: SourceIntelligenceProfile,
    *,
    reviewer_user_id: UUID,
) -> None:
    permission = profile.permission_evidence or {}
    if not all(
        isinstance(permission.get(key), str) and permission[key].strip()
        for key in REQUIRED_PERMISSION_FIELDS
    ):
        raise ValueError("source permission evidence is incomplete")
    routes = (profile.capabilities or {}).get("routes")
    executable_routes = {
        route.value for route in RetrievalRoute if route != RetrievalRoute.REVIEW_REQUIRED
    }
    if (
        not isinstance(routes, list)
        or not routes
        or any(not isinstance(route, str) or route not in executable_routes for route in routes)
    ):
        raise ValueError("source capability evidence does not include executable routes")
    profile.status = "APPROVED"
    profile.approved_by_user_id = reviewer_user_id
    profile.approved_at = datetime.now(UTC)
    profile.revision += 1
    db.flush()


def source_intelligence_is_executable(profile: SourceIntelligenceProfile) -> bool:
    permission = profile.permission_evidence or {}
    routes = (profile.capabilities or {}).get("routes")
    executable_routes = {
        route.value for route in RetrievalRoute if route != RetrievalRoute.REVIEW_REQUIRED
    }
    return bool(
        profile.status == "APPROVED"
        and profile.approved_by_user_id is not None
        and profile.approved_at is not None
        and all(
            isinstance(permission.get(key), str) and permission[key].strip()
            for key in REQUIRED_PERMISSION_FIELDS
        )
        and isinstance(routes, list)
        and bool(routes)
        and all(isinstance(route, str) and route in executable_routes for route in routes)
    )
