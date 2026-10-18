"""Operational heartbeat state for long-running worker processes."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Index, JSON, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKeyMixin


class WorkerHeartbeat(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "worker_heartbeats"
    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING', 'IDLE', 'STOPPED', 'FAILED')",
            name="ck_worker_heartbeat_status",
        ),
        UniqueConstraint("worker_name", "instance_id", name="uq_worker_heartbeat_instance"),
        Index("ix_worker_heartbeats_name_seen", "worker_name", "last_seen_at"),
        Index("ix_worker_heartbeats_seen", "last_seen_at"),
    )

    worker_name: Mapped[str] = mapped_column(String(64), nullable=False)
    instance_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_category: Mapped[str | None] = mapped_column(String(96))
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default=text("'{}'::json"))
