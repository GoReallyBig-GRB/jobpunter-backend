from datetime import datetime, timezone
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKeyMixin


class Notification(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_notifications_event_key"),
        Index("ix_notifications_user_created_at", "user_id", "created_at"),
        Index("ix_notifications_user_read_created", "user_id", "read_at", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationEmailPreference(Base):
    __tablename__ = "notification_email_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class NotificationEmailDelivery(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "notification_email_deliveries"
    __table_args__ = (
        UniqueConstraint("notification_id", name="uq_notification_email_delivery_notification"),
        Index("ix_notification_email_deliveries_status_updated", "status", "updated_at"),
    )

    notification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(64))
    smtp_config_version: Mapped[int | None] = mapped_column()
    smtp_fallback_used: Mapped[bool] = mapped_column(nullable=False, default=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class SMTPProviderConfiguration(Base, UUIDPrimaryKeyMixin):
    """Versioned SMTP provider configuration; the password is encrypted at rest."""

    __tablename__ = "smtp_provider_configurations"
    __table_args__ = (
        UniqueConstraint("version", name="uq_smtp_provider_configuration_version"),
        Index(
            "uq_smtp_provider_configuration_published",
            "status",
            unique=True,
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
        Index("ix_smtp_provider_configuration_status_created", "status", "created_at"),
    )

    version: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="DRAFT")
    provider_name: Mapped[str] = mapped_column(String(80), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(nullable=False, default=587)
    use_tls: Mapped[bool] = mapped_column(nullable=False, default=True)
    use_ssl: Mapped[bool] = mapped_column(nullable=False, default=False)
    username: Mapped[str | None] = mapped_column(String(320))
    encrypted_password: Mapped[str | None] = mapped_column(Text)
    from_email: Mapped[str] = mapped_column(String(320), nullable=False)
    event_policy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    retry_limit: Mapped[int] = mapped_column(nullable=False, default=5)
    retry_backoff_seconds: Mapped[int] = mapped_column(nullable=False, default=60)
    config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    published_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("smtp_provider_configurations.id", ondelete="RESTRICT"))
