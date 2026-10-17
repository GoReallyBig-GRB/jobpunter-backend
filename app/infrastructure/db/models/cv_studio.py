"""Immutable candidate-owned CV versions and explicit candidate approvals."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String,
    Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKeyMixin


class CandidateCVVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "candidate_cv_versions"
    __table_args__ = (
        UniqueConstraint(
            "candidate_profile_id", "cv_key", "version_number",
            name="uq_candidate_cv_version_key",
        ),
        CheckConstraint(
            "cv_type IN ('MASTER', 'VARIANT')",
            name="ck_candidate_cv_version_type",
        ),
        CheckConstraint(
            "(cv_type = 'MASTER' AND cv_key = 'master') OR "
            "(cv_type = 'VARIANT' AND cv_key <> 'master')",
            name="ck_candidate_cv_master_key",
        ),
        CheckConstraint("version_number >= 1", name="ck_candidate_cv_version_positive"),
        CheckConstraint("source_profile_revision >= 1", name="ck_candidate_cv_profile_revision"),
        Index(
            "ix_candidate_cv_versions_lookup",
            "candidate_profile_id", "cv_key", "version_number",
        ),
    )

    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    cv_key: Mapped[str] = mapped_column(String(64), nullable=False)
    cv_type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_profile_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_profile_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_master_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidate_cv_versions.id", ondelete="RESTRICT")
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class CandidateCVApproval(Base, UUIDPrimaryKeyMixin):
    """Append-only record that the candidate explicitly approved one CV version."""

    __tablename__ = "candidate_cv_approvals"
    __table_args__ = (
        UniqueConstraint("cv_version_id", name="uq_candidate_cv_approval_version"),
        Index("ix_candidate_cv_approvals_candidate", "candidate_profile_id", "approved_at"),
    )

    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    cv_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_cv_versions.id", ondelete="RESTRICT"), nullable=False
    )
    approved_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    notes: Mapped[str | None] = mapped_column(Text)
