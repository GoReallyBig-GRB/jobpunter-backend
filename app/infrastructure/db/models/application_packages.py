"""Versioned, candidate-owned application packages and their artifacts."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKeyMixin


class ApplicationPackage(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "application_packages"
    __table_args__ = (
        UniqueConstraint("application_id", "version_number", name="uq_application_package_version"),
        Index("ix_application_packages_application_created", "application_id", "created_at"),
    )

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="DRAFT")
    source_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class ApplicationPackageItem(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "application_package_items"
    __table_args__ = (
        UniqueConstraint("package_id", "artifact_type", name="uq_application_package_item_type"),
        Index("ix_application_package_items_package", "package_id"),
    )

    package_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("application_packages.id", ondelete="RESTRICT"), nullable=False
    )
    artifact_type: Mapped[str] = mapped_column(String(40), nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    unsupported_claims: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    ai_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_executions.id", ondelete="RESTRICT")
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
