import uuid
from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, RevisionMixin, UUIDPrimaryKeyMixin

class Source(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(
            "operational_state IN ("
            "'CONFIGURED', 'CHECKED', 'HEALTHY', 'DEGRADED', 'RESTRICTED', 'FAILED', 'UNAVAILABLE'"
            ")",
            name="ck_sources_operational_state",
        ),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    permission_review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    permission_review_reference: Mapped[str | None] = mapped_column(Text)
    permission_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operational_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="CONFIGURED", server_default="CONFIGURED"
    )
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
    active_configuration_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class SourceConnectorConfigurationVersion(Base, UUIDPrimaryKeyMixin):
    """Immutable, audited snapshot of a connector's configuration."""
    __tablename__ = "source_connector_configuration_versions"
    __table_args__ = (
        UniqueConstraint(
            "source_connector_id", "version",
            name="uq_source_connector_configuration_version",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED')",
            name="ck_source_connector_config_version_status",
        ),
    )
    source_connector_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_connectors.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration: Mapped[dict] = mapped_column(JSON, nullable=False)
    configuration_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PUBLISHED")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
