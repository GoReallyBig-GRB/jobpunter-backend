"""Administrative per-source acquisition run-rate limit."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, RevisionMixin, UUIDPrimaryKeyMixin


class DiscoverySourceLimit(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    """At most `max_runs` acquisition runs per rolling `window_minutes`; no row means no admin limit."""

    __tablename__ = "discovery_source_limits"
    __table_args__ = (
        CheckConstraint("max_runs BETWEEN 1 AND 1000", name="ck_discovery_source_limit_max_runs"),
        CheckConstraint("window_minutes BETWEEN 1 AND 10080", name="ck_discovery_source_limit_window"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    max_runs: Mapped[int] = mapped_column(Integer, nullable=False)
    window_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
