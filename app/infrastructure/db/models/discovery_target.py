"""Canonical normalized discovery targets and append-only lifecycle history."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, UUIDPrimaryKeyMixin


class DiscoveryTarget(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "discovery_targets"
    __table_args__ = (
        CheckConstraint("target_kind IN ('CRAWL', 'SEARCH')", name="ck_discovery_target_kind"),
        CheckConstraint("status IN ('DRAFT', 'ACTIVE', 'PAUSED', 'RETIRED')", name="ck_discovery_target_status"),
        UniqueConstraint("source_connector_id", "url", "target_kind", "purpose",
                         name="uq_discovery_target_connector_url_kind_purpose"),
        Index("ix_discovery_targets_status_kind", "status", "target_kind"),
        Index("ix_discovery_targets_source_status", "source_id", "status"),
    )
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False)
    source_connector_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_connectors.id", ondelete="RESTRICT"), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT", server_default="DRAFT")
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class DiscoveryTargetEvent(Base, UUIDPrimaryKeyMixin):
    """Append-only record of target creation and every accepted lifecycle transition."""
    __tablename__ = "discovery_target_events"
    __table_args__ = (
        CheckConstraint("action IN ('CREATED', 'ACTIVATED', 'PAUSED', 'RETIRED', 'BLOCKED')", name="ck_discovery_target_event_action"),
        Index("ix_discovery_target_events_target_created", "target_id", "created_at"),
    )
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("discovery_targets.id", ondelete="RESTRICT"), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(16))
    new_status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
