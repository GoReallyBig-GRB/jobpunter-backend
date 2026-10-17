"""Candidate-controlled document import and review state."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, RevisionMixin, UUIDPrimaryKeyMixin


class DocumentImport(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "document_imports"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_document_import_document"),
        Index("ix_document_imports_candidate_status", "candidate_profile_id", "status", "created_at"),
    )

    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False
    )
    ai_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_executions.id", ondelete="RESTRICT", name="fk_document_imports_ai_execution_id_ai_executions")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UPLOADED")
    extracted_text: Mapped[str | None] = mapped_column(Text)
    extracted_fields: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    conflicts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    conflict_resolutions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    saved_evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence.id", ondelete="RESTRICT")
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
