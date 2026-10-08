import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKeyMixin


class AcquisitionRun(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "acquisition_runs"
    __table_args__ = (
        UniqueConstraint("source_id", "run_key", name="uq_acquisition_run_source_key"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    source_connector_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_connectors.id", ondelete="RESTRICT"), nullable=False
    )
    run_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class RawSourceRecord(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "raw_source_records"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "deduplication_key",
            "content_hash",
            name="uq_raw_source_dedup_key",
        ),
    )

    acquisition_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acquisition_runs.id", ondelete="RESTRICT"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    external_record_id: Mapped[str | None] = mapped_column(String(500))
    deduplication_key: Mapped[str] = mapped_column(String(600), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    raw_content: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACQUIRED")
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
