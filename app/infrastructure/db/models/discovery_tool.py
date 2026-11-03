"""Administrative activation control for code-owned discovery tools (connector types)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, RevisionMixin, UUIDPrimaryKeyMixin


class DiscoveryToolControl(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    """A row exists only once an administrator has changed a tool's activation; no row means ENABLED."""

    __tablename__ = "discovery_tool_controls"
    __table_args__ = (
        CheckConstraint("status IN ('ENABLED', 'DISABLED')", name="ck_discovery_tool_control_status"),
        CheckConstraint(
            "(max_runs IS NULL AND window_minutes IS NULL) OR "
            "(max_runs BETWEEN 1 AND 100000 AND window_minutes BETWEEN 1 AND 10080)",
            name="ck_discovery_tool_control_budget",
        ),
    )

    tool_key: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    # Optional run budget: at most `max_runs` acquisition runs per rolling `window_minutes`.
    # tool_key "ALL" is the global budget across every tool.
    max_runs: Mapped[int | None] = mapped_column(Integer)
    window_minutes: Mapped[int | None] = mapped_column(Integer)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
