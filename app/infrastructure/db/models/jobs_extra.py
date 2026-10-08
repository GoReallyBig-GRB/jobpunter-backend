import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKeyMixin


class JobResolution(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "job_resolutions"

    raw_source_record_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("raw_source_records.id", ondelete="RESTRICT"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False)
    resolution_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resolution_method: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    resolver_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    related_resolution_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_resolutions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class JobProvenance(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "job_provenance"

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False)
    raw_source_record_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("raw_source_records.id"))
    source_url: Mapped[str | None] = mapped_column(Text)
    observation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    asserted_by: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence: Mapped[float | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class JobDescriptionVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "job_description_versions"
    __table_args__ = (UniqueConstraint("job_id", "version_number", name="uq_jd_job_version"),)

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False)
    version_number: Mapped[int] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    source_record_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("raw_source_records.id"))
    completeness_status: Mapped[str] = mapped_column(String(32), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class JobRequirement(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "job_requirements"

    job_description_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_description_versions.id", ondelete="RESTRICT"), nullable=False)
    requirement_type: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[str] = mapped_column(String(32), nullable=False)
    hard_constraint: Mapped[bool] = mapped_column(nullable=False, default=False)
    extraction_status: Mapped[str] = mapped_column(String(32), nullable=False)
