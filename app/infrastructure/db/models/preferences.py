import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, RevisionMixin, UUIDPrimaryKeyMixin


class CandidatePreferences(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    """Versioned, candidate-owned job-search preferences, separate from factual evidence."""

    __tablename__ = "candidate_preferences"
    __table_args__ = (
        UniqueConstraint("candidate_profile_id", name="uq_candidate_preferences_profile"),
    )

    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    preferences: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
