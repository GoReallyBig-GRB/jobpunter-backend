import uuid
from datetime import datetime, timezone
from sqlalchemy import DateTime, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, RevisionMixin, UUIDPrimaryKeyMixin

class Job(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "jobs"
    identity_status: Mapped[str] = mapped_column(String(32), nullable=False, default="CANONICAL")
    lifecycle_status: Mapped[str] = mapped_column(String(32), nullable=False, default="DISCOVERED")
    freshness_status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    canonical_title: Mapped[str] = mapped_column(String(500), nullable=False)
    company_name: Mapped[str] = mapped_column(String(500), nullable=False)
    employment_type: Mapped[str | None] = mapped_column(String(64))
    salary_min: Mapped[float | None] = mapped_column(Numeric(18,2))
    salary_max: Mapped[float | None] = mapped_column(Numeric(18,2))
    salary_currency: Mapped[str | None] = mapped_column(String(16))
    location_country: Mapped[str | None] = mapped_column(String(128))
    location_region: Mapped[str | None] = mapped_column(String(128))
    location_city: Mapped[str | None] = mapped_column(String(128))
    work_mode: Mapped[str | None] = mapped_column(String(32))
    remote_eligibility: Mapped[str | None] = mapped_column(String(64))
    location_restrictions: Mapped[dict | None] = mapped_column(JSON)
    original_location_text: Mapped[str | None] = mapped_column(Text)
    application_url: Mapped[str | None] = mapped_column(Text)
    first_discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
