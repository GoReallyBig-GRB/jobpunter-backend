import uuid
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, RevisionMixin, UUIDPrimaryKeyMixin

class Source(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "sources"
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    permission_review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    permission_review_reference: Mapped[str | None] = mapped_column(Text)
    permission_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operational_state: Mapped[str] = mapped_column(String(32), nullable=False, default="UNAVAILABLE")
    last_error_category: Mapped[str | None] = mapped_column(String(64))
    configuration: Mapped[dict | None] = mapped_column(JSON)
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class SourceConnector(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "source_connectors"
    __table_args__ = (UniqueConstraint("source_id","connector_type","connector_version",name="uq_source_connector_version"),)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(64), nullable=False)
    connector_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    configuration: Mapped[dict | None] = mapped_column(JSON)
    capabilities: Mapped[dict | None] = mapped_column(JSON)
    limits: Mapped[dict | None] = mapped_column(JSON)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
