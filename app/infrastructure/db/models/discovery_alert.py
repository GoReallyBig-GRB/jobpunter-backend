"""Durable operational alerts raised from discovery metrics (E5.5)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, RevisionMixin, UUIDPrimaryKeyMixin


class DiscoveryAlert(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    """One row per episode of a condition; at most one unresolved row per `alert_key`."""

    __tablename__ = "discovery_alerts"
    __table_args__ = (
        CheckConstraint("kind IN ('STALE_SOURCE','TOOL_FAILURE_RATE')", name="ck_discovery_alert_kind"),
        CheckConstraint("status IN ('OPEN','ACKNOWLEDGED','RESOLVED')", name="ck_discovery_alert_status"),
        Index(
            "uq_discovery_alerts_unresolved_key", "alert_key", unique=True,
            postgresql_where=text("status <> 'RESOLVED'"),
        ),
        Index("ix_discovery_alerts_status_opened", "status", "opened_at"),
    )

    alert_key: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)  # source id for STALE_SOURCE
    tool_key: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledgement_reason: Mapped[str | None] = mapped_column(String(1000))
