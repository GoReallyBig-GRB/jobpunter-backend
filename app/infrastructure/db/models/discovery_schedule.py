"""Administrative, candidate-independent refresh schedule for one source."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, RevisionMixin, UUIDPrimaryKeyMixin


class SourceRefreshSchedule(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    """One recurring refresh cadence per source; no row means the source is never refreshed on a timer."""

    __tablename__ = "source_refresh_schedules"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','PAUSED')", name="ck_source_refresh_schedule_status"),
        CheckConstraint("interval_minutes BETWEEN 15 AND 10080", name="ck_source_refresh_schedule_interval"),
        CheckConstraint(
            "(window_start_minute IS NULL AND window_end_minute IS NULL AND window_timezone IS NULL) OR "
            "(window_start_minute BETWEEN 0 AND 1439 AND window_end_minute BETWEEN 0 AND 1439 "
            "AND window_start_minute <> window_end_minute AND window_timezone IS NOT NULL)",
            name="ck_source_refresh_schedule_window",
        ),
    )

    config_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("governance_config_versions.id", ondelete="RESTRICT"), nullable=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    status_reason: Mapped[str | None] = mapped_column(String(500))
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Optional daily run window in local minutes-of-day; end < start means it crosses midnight.
    window_start_minute: Mapped[int | None] = mapped_column(Integer)
    window_end_minute: Mapped[int | None] = mapped_column(Integer)
    window_timezone: Mapped[str | None] = mapped_column(String(64))
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


class SourceRefreshScheduleEvent(Base, UUIDPrimaryKeyMixin):
    """Append-only history of administrative schedule changes (E4.7)."""

    __tablename__ = "source_refresh_schedule_events"
    __table_args__ = (
        Index("ix_source_refresh_schedule_events_schedule_created", "schedule_id", "created_at"),
        UniqueConstraint("schedule_id", "schedule_revision", name="uq_source_refresh_schedule_revision"),
    )

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_refresh_schedules.id", ondelete="RESTRICT"), nullable=False
    )
    schedule_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # CREATED, CHANGED, PAUSED, RESUMED
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
