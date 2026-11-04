"""Versioned discovery-orchestration configuration owned by administrators."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKeyMixin


class DiscoveryStrategy(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "discovery_strategies"
    __table_args__ = (
        UniqueConstraint("strategy_key", name="uq_discovery_strategy_key"),
        CheckConstraint("status IN ('ENABLED', 'DISABLED', 'RETIRED')", name="ck_discovery_strategy_status"),
        Index("ix_discovery_strategies_status_key", "status", "strategy_key"),
    )

    strategy_key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ENABLED")
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class DiscoveryStrategyVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "discovery_strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version", name="uq_discovery_strategy_version"),
        CheckConstraint(
            "status IN ('DRAFT', 'VALIDATED', 'PUBLISHED', 'SUPERSEDED', 'RETIRED')",
            name="ck_discovery_strategy_version_status",
        ),
        CheckConstraint(
            "validation_status IN ('NOT_RUN', 'VALID', 'INVALID')",
            name="ck_discovery_strategy_validation_status",
        ),
        Index("ix_discovery_strategy_versions_strategy_status", "strategy_id", "status"),
        Index(
            "uq_discovery_strategy_one_published",
            "strategy_id",
            unique=True,
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
    )

    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("discovery_strategies.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(24), nullable=False, default="v1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    validation_status: Mapped[str] = mapped_column(String(16), nullable=False, default="NOT_RUN")
    validation_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    published_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("discovery_strategy_versions.id", ondelete="RESTRICT")
    )
    rollback_of_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("discovery_strategy_versions.id", ondelete="RESTRICT")
    )
    audit_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_audit_events.id", ondelete="RESTRICT")
    )

