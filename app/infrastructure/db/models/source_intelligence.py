"""Review-gated intelligence for sources discovered at runtime."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, RevisionMixin, UUIDPrimaryKeyMixin


class SourceIntelligenceProfile(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "source_intelligence_profiles"
    __table_args__ = (
        UniqueConstraint("domain", name="uq_source_intelligence_domain"),
        CheckConstraint(
            "status IN ('REVIEW_REQUIRED', 'APPROVED', 'REJECTED', 'PAUSED')",
            name="ck_source_intelligence_status",
        ),
        CheckConstraint(
            "status <> 'APPROVED' OR (approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_source_intelligence_approval_actor_time",
        ),
    )

    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="REVIEW_REQUIRED")
    capabilities: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    market_coverage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    permission_evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    health_evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    yield_evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class SourceIntelligenceObservation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "source_intelligence_observations"
    __table_args__ = (
        CheckConstraint(
            "observation_type IN ('CAPABILITY', 'MARKET', 'PERMISSION', 'HEALTH', 'YIELD')",
            name="ck_source_intelligence_observation_type",
        ),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_intelligence_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    observation_type: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class AcquisitionDispatchAttempt(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "acquisition_dispatch_attempts"
    __table_args__ = (
        UniqueConstraint("attempt_key", name="uq_acquisition_dispatch_attempt_key"),
        CheckConstraint(
            "status IN ('SUCCEEDED', 'PARTIAL', 'RESTRICTED', 'UNAVAILABLE', 'FAILED')",
            name="ck_acquisition_dispatch_attempt_status",
        ),
        CheckConstraint(
            "route IN ('JSON_API', 'RSS_ATOM', 'PUBLIC_HTTP', 'SCRAPY', 'PLAYWRIGHT')",
            name="ck_acquisition_dispatch_attempt_route",
        ),
        CheckConstraint("acquired_count >= 0", name="ck_acquisition_dispatch_acquired_count"),
    )

    attempt_key: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_intelligence_profiles.id", ondelete="RESTRICT")
    )
    url_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    route: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(64))
    acquired_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class DiscoveryCoverageObservation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "discovery_coverage_observations"
    __table_args__ = (
        CheckConstraint(
            "local_source_count >= 0 AND international_source_count >= 0 "
            "AND eligible_url_count >= 0 AND total_url_count >= 0",
            name="ck_discovery_coverage_nonnegative_counts",
        ),
        CheckConstraint(
            "eligible_url_count <= total_url_count",
            name="ck_discovery_coverage_eligible_total",
        ),
        CheckConstraint(
            "coverage_ratio >= 0 AND coverage_ratio <= 1",
            name="ck_discovery_coverage_ratio",
        ),
    )

    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)
    query_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    local_source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    international_source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    eligible_url_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_url_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coverage_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class SourceDiscoveryCandidate(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "source_discovery_candidates"
    __table_args__ = (
        UniqueConstraint("normalized_url", name="uq_source_discovery_candidate_url"),
        CheckConstraint(
            "status IN ('REVIEW_REQUIRED', 'APPROVED', 'PROCESSING', 'REJECTED', "
            "'PAUSED', 'FAILED', 'ACQUIRED')",
            name="ck_source_discovery_candidate_status",
        ),
        CheckConstraint(
            "market_scope IN ('LOCAL', 'INTERNATIONAL')",
            name="ck_source_discovery_candidate_market_scope",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_source_discovery_candidate_confidence",
        ),
        CheckConstraint(
            "status NOT IN ('APPROVED', 'PROCESSING', 'ACQUIRED') OR "
            "(approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_source_discovery_candidate_approval_actor_time",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_source_discovery_candidate_attempt_count"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_intelligence_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT")
    )
    normalized_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_domain: Mapped[str] = mapped_column(String(253), nullable=False)
    market_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    nigeria_eligible: Mapped[bool | None] = mapped_column(Boolean)
    source_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    recommended_routes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evidence: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="REVIEW_REQUIRED")
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_category: Mapped[str | None] = mapped_column(String(64))
    first_discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    last_discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
