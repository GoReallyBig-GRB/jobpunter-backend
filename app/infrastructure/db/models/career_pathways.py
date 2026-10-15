"""Candidate-owned career pathways and their append-only review history."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, RevisionMixin, UUIDPrimaryKeyMixin


class CareerPathway(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "career_pathways"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('USER_DEFINED', 'AI_SUGGESTED')",
            name="ck_career_pathways_source_type",
        ),
        CheckConstraint(
            "approval_status IN ('DRAFT', 'PENDING_REVIEW', 'APPROVED', 'REJECTED', 'RETIRED')",
            name="ck_career_pathways_approval_status",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_career_pathways_confidence",
        ),
        Index(
            "ix_career_pathways_candidate_status",
            "candidate_profile_id", "approval_status", "updated_at",
        ),
    )

    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    role_family: Mapped[str] = mapped_column(String(255), nullable=False)
    target_roles: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    target_seniority: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    domains: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    supporting_evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    gaps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    approval_status: Mapped[str] = mapped_column(String(32), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class CareerPathwayEvent(Base, UUIDPrimaryKeyMixin):
    """Append-only record of candidate pathway edits and approval decisions."""

    __tablename__ = "career_pathway_events"
    __table_args__ = (
        Index(
            "ix_career_pathway_events_pathway_created",
            "career_pathway_id", "created_at", "id",
        ),
        Index(
            "ix_career_pathway_events_candidate_created",
            "candidate_profile_id", "created_at",
        ),
    )

    career_pathway_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("career_pathways.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(32))
    new_status: Mapped[str | None] = mapped_column(String(32))
    pathway_revision: Mapped[int] = mapped_column(nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
