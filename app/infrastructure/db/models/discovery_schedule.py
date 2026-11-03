"""Administrative, candidate-independent refresh schedule for one source."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, RevisionMixin, UUIDPrimaryKeyMixin


class DiscoverySchedule(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    """One recurring refresh cadence per source; no row means the source is never refreshed on a timer."""

    __tablename__ = "discovery_schedules"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','PAUSED')", name="ck_discovery_schedule_status"),
        CheckConstraint("interval_minutes BETWEEN 15 AND 10080", name="ck_discovery_schedule_interval"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    status_reason: Mapped[str | None] = mapped_column(String(500))
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
