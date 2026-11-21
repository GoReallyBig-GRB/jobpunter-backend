import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKeyMixin


class CandidatePoolEntry(Base, UUIDPrimaryKeyMixin):
    """A central-pool job copied into one candidate's own pool by a scheduled or manual sync.

    The central job stays the single source of truth; the entry records why it matched and its
    state for this candidate. External sources are never contacted when this table is written.
    """

    __tablename__ = "candidate_pool_entries"
    __table_args__ = (
        UniqueConstraint("candidate_profile_id", "job_id", name="uq_candidate_pool_candidate_job"),
        Index("ix_candidate_pool_candidate_status_matched", "candidate_profile_id", "status", "matched_at"),
        Index("ix_candidate_pool_job", "job_id"),
        CheckConstraint("status IN ('ACTIVE', 'CLOSED')", name="ck_candidate_pool_status"),
    )

    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    match_basis: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    matched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
