"""Persist bounded, sanitized integration health observations."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKeyMixin


class IntegrationHealthObservation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "integration_health_observations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('HEALTHY', 'DEGRADED', 'FAILED', 'UNCONFIGURED', 'UNSUPPORTED')",
            name="ck_integration_health_observation_status",
        ),
        CheckConstraint("duration_ms >= 0", name="ck_integration_health_observation_duration"),
        Index(
            "ix_integration_health_observation_key_time",
            "integration_key",
            "observed_at",
        ),
    )

    integration_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    check_type: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    checked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    error_category: Mapped[str | None] = mapped_column(String(96))
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default=text("'{}'"))
