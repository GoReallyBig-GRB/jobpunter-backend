"""Persisted authorization and versioned system-governance records."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, UUIDPrimaryKeyMixin

class AdminGrant(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "admin_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "role", name="uq_admin_grant_user_role"),
        Index("ix_admin_grants_active_role", "active", "role"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="ADMIN")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class GovernanceConfigVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "governance_config_versions"
    __table_args__ = (
        UniqueConstraint("config_key", "version", name="uq_governance_config_key_version"),
        Index("ix_governance_config_key_status", "config_key", "status"),
        Index("uq_governance_one_published_per_key", "config_key", unique=True,
              postgresql_where=text("status = 'PUBLISHED'")),
    )
    config_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="DRAFT")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    published_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("governance_config_versions.id", ondelete="RESTRICT"))

class AdminAuditEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "admin_audit_events"
    __table_args__ = (
        Index("ix_admin_audit_actor_created", "actor_user_id", "created_at"),
        Index("ix_admin_audit_resource", "resource_type", "resource_id"),
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column()
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class AuditEvent(Base, UUIDPrimaryKeyMixin):
    """Unified append-only audit projection for application and privileged actions."""
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_resource_occurred", "resource_type", "resource_id", "occurred_at"),
        Index("ix_audit_events_actor_occurred", "actor_type", "actor_id", "occurred_at"),
        Index("ix_audit_events_event_occurred", "event_type", "occurred_at"),
    )

    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column()
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column()
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class AIExecution(Base, UUIDPrimaryKeyMixin):
    """Minimal operational trace; candidate prompt and output are deliberately not stored."""
    __tablename__ = "ai_executions"
    __table_args__ = (
        Index("ix_ai_executions_user_created", "user_id", "created_at"),
        Index("ix_ai_executions_task_created", "task", "created_at"),
        Index("ix_ai_executions_status_created", "status", "created_at"),
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    task: Mapped[str] = mapped_column(String(80), nullable=False)
    task_policy_version: Mapped[str] = mapped_column(String(40), nullable=False, default="v1", server_default="v1")
    system_policy_version: Mapped[str] = mapped_column(String(40), nullable=False, default="v1", server_default="v1")
    input_contract_version: Mapped[str] = mapped_column(String(40), nullable=False, default="v2", server_default="v2")
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="legacy-unavailable", server_default="legacy-unavailable")
    output_contract_version: Mapped[str] = mapped_column(
        String(40), nullable=False, default="v1", server_default="v1"
    )
    config_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, default="unversioned", server_default="unversioned"
    )
    contract_snapshot: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=lambda: {"status": "LEGACY_UNAVAILABLE"},
        server_default=text("'{\"status\":\"LEGACY_UNAVAILABLE\"}'"),
    )
    output_fingerprint: Mapped[str | None] = mapped_column(String(64))
    config_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("governance_config_versions.id", ondelete="RESTRICT"))
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="RUNNING")
    fallback_attempted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))
    validation_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=lambda: {"status": "NOT_RUN"}, server_default=text("'{\"status\":\"NOT_RUN\"}'"))
    request_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
