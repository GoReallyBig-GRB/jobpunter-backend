import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, RevisionMixin, UUIDPrimaryKeyMixin


class Job(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "jobs"

    identity_status: Mapped[str] = mapped_column(String(32), nullable=False, default="CANONICAL")
    lifecycle_status: Mapped[str] = mapped_column(String(32), nullable=False, default="DISCOVERED")
    freshness_status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    canonical_title: Mapped[str] = mapped_column(String(500), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(500))
    employment_type: Mapped[str | None] = mapped_column(String(64))
    salary_min: Mapped[float | None] = mapped_column(Numeric(18, 2))
    salary_max: Mapped[float | None] = mapped_column(Numeric(18, 2))
    salary_currency: Mapped[str | None] = mapped_column(String(16))
    salary_period: Mapped[str | None] = mapped_column(String(32))
    salary_type: Mapped[str | None] = mapped_column(String(32))
    salary_confidence: Mapped[float | None] = mapped_column()
    disclosure_status: Mapped[str | None] = mapped_column(String(32))
    original_salary_text: Mapped[str | None] = mapped_column(Text)
    location_country: Mapped[str | None] = mapped_column(String(128))
    location_region: Mapped[str | None] = mapped_column(String(128))
    location_city: Mapped[str | None] = mapped_column(String(128))
    work_mode: Mapped[str | None] = mapped_column(String(32))
    remote_eligibility: Mapped[str | None] = mapped_column(String(64))
    location_restrictions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    original_location_text: Mapped[str | None] = mapped_column(Text)
    application_url: Mapped[str | None] = mapped_column(Text)
    first_discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class JobResolution(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "job_resolutions"

    raw_source_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("raw_source_records.id", ondelete="RESTRICT"), nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=True
    )
    resolution_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resolution_method: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    resolver_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    related_resolution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("job_resolutions.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class JobResolutionCandidate(Base):
    __tablename__ = "job_resolution_candidates"

    resolution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_resolutions.id", ondelete="CASCADE"), primary_key=True
    )
    candidate_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), primary_key=True
    )
    confidence: Mapped[float] = mapped_column(nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class JobProvenance(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "job_provenance"

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    raw_source_record_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_source_records.id")
    )
    source_url: Mapped[str | None] = mapped_column(Text)
    observation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    asserted_by: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence: Mapped[float | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class JobDiscoveryEvent(Base, UUIDPrimaryKeyMixin):
    """Immutable record of one source observation, including unchanged rediscoveries."""

    __tablename__ = "job_discovery_events"
    __table_args__ = (
        CheckConstraint(
            "observation_outcome IN ('NEW', 'CHANGED', 'UNCHANGED')",
            name="ck_job_discovery_events_outcome",
        ),
        CheckConstraint(
            "processing_status IN ('PENDING', 'CANONICALIZED', 'REQUIRES_REVIEW', 'FAILED')",
            name="ck_job_discovery_events_processing_status",
        ),
        Index("ix_job_discovery_events_source_observed", "source_id", "observed_at"),
        Index("ix_job_discovery_events_run", "acquisition_run_id", "created_at"),
        Index("ix_job_discovery_events_raw_observed", "raw_source_record_id", "observed_at"),
        Index("ix_job_discovery_events_job_observed", "canonical_job_id", "observed_at"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    acquisition_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acquisition_runs.id", ondelete="RESTRICT"), nullable=False
    )
    raw_source_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("raw_source_records.id", ondelete="RESTRICT"), nullable=False
    )
    canonical_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT")
    )
    external_record_id: Mapped[str | None] = mapped_column(String(500))
    source_url: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observation_outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    processing_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    processing_error: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class JobDescriptionVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "job_description_versions"
    __table_args__ = (
        UniqueConstraint("job_id", "version_number", name="uq_jd_job_version"),
        Index(
            "ix_job_description_requirement_extraction",
            "requirement_extraction_status",
            "requirement_extraction_retry_at",
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    source_record_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("raw_source_records.id"))
    completeness_status: Mapped[str] = mapped_column(String(32), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requirement_extraction_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING"
    )
    requirement_extraction_attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    requirement_extraction_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requirement_extraction_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requirement_extraction_error: Mapped[str | None] = mapped_column(String(64))
    requirement_extraction_version: Mapped[str | None] = mapped_column(String(64))
    requirements_extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class JobRequirement(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "job_requirements"

    job_description_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_description_versions.id", ondelete="RESTRICT"), nullable=False
    )
    requirement_type: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[str] = mapped_column(String(32), nullable=False)
    hard_constraint: Mapped[bool] = mapped_column(nullable=False)
    extraction_status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_excerpt: Mapped[str | None] = mapped_column(Text)
    extraction_confidence: Mapped[float | None] = mapped_column()
    extraction_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
