"""Versioned discovery-orchestration configuration owned by administrators."""

import uuid
from datetime import UTC, datetime
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, RevisionMixin, UUIDPrimaryKeyMixin


class DiscoveryStrategy(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "discovery_strategies"
    __table_args__ = (
        UniqueConstraint("strategy_key", name="uq_discovery_strategy_key"),
        CheckConstraint(
            "status IN ('ENABLED', 'DISABLED', 'RETIRED')", name="ck_discovery_strategy_status"
        ),
        Index("ix_discovery_strategies_status_key", "status", "strategy_key"),
    )

    strategy_key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ENABLED")
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class DiscoveryStrategyVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "discovery_strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version", name="uq_discovery_strategy_version"),
        CheckConstraint(
            "status IN ('DRAFT', 'VALIDATED', 'PUBLISHED', 'SUPERSEDED', 'RETIRED')",
            name="ck_discovery_strategy_version_status",
        ),
        CheckConstraint(
            "validation_status IN ('NOT_RUN', 'VALID', 'INVALID')",
            name="ck_discovery_strategy_validation_status",
        ),
        Index("ix_discovery_strategy_versions_strategy_status", "strategy_id", "status"),
        Index(
            "uq_discovery_strategy_one_published",
            "strategy_id",
            unique=True,
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
    )

    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("discovery_strategies.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(24), nullable=False, default="v1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    validation_status: Mapped[str] = mapped_column(String(16), nullable=False, default="NOT_RUN")
    validation_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    published_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("discovery_strategy_versions.id", ondelete="RESTRICT")
    )
    rollback_of_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("discovery_strategy_versions.id", ondelete="RESTRICT")
    )
    # Soft reference avoids making immutable audit tables untruncatable before
    # their own append-only trigger can enforce and report the security rule.
    audit_event_id: Mapped[uuid.UUID | None] = mapped_column()


class DiscoverySchedule(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "discovery_schedules"
    __table_args__ = (
        CheckConstraint(
            "schedule_type IN ('GLOBAL_SWEEP', 'SOURCE_REFRESH', 'TARGET_REFRESH', "
            "'MARKET_DISCOVERY', 'HEALTH_CHECK', 'STALENESS_REVALIDATION')",
            name="ck_discovery_schedule_type",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'PAUSED', 'RETIRED')",
            name="ck_discovery_schedule_status",
        ),
        CheckConstraint(
            "frequency_type IN ('INTERVAL', 'DAILY', 'WEEKLY')",
            name="ck_discovery_schedule_frequency_type",
        ),
        CheckConstraint(
            "overlap_policy IN ('SKIP', 'QUEUE')",
            name="ck_discovery_schedule_overlap_policy",
        ),
        Index("ix_discovery_schedules_due", "status", "next_run_at", "priority"),
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PAUSED")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    frequency_type: Mapped[str] = mapped_column(String(16), nullable=False)
    frequency_definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    window_definition: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_scope: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    target_scope: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    market_scope: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("discovery_strategy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    overlap_policy: Mapped[str] = mapped_column(String(16), nullable=False, default="SKIP")
    maximum_concurrent_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    limits: Mapped[dict] = mapped_column(JSON, nullable=False)
    retry_policy: Mapped[dict] = mapped_column(JSON, nullable=False)
    manual_execution_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class DiscoveryRun(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "discovery_runs"
    __table_args__ = (
        UniqueConstraint("run_key", name="uq_discovery_run_key"),
        CheckConstraint(
            "trigger_type IN ('SCHEDULED', 'MANUAL', 'RETRY')",
            name="ck_discovery_run_trigger_type",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED', "
            "'BLOCKED', 'CANCELLED')",
            name="ck_discovery_run_status",
        ),
        Index("ix_discovery_runs_status_created", "status", "created_at"),
        Index("ix_discovery_runs_schedule_created", "schedule_id", "created_at"),
    )

    run_key: Mapped[str] = mapped_column(String(255), nullable=False)
    run_type: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("discovery_schedules.id", ondelete="RESTRICT")
    )
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("discovery_strategy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    configuration_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    operation_counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    record_counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    budget_outcome: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    retry_information: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    reason: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class DiscoveryRoleExecution(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "discovery_role_executions"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "role_key", "attempt_number", name="uq_discovery_role_run_attempt"
        ),
        CheckConstraint(
            "outcome IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'SKIPPED', 'FAILED', 'BLOCKED')",
            name="ck_discovery_role_execution_outcome",
        ),
        Index("ix_discovery_role_executions_run_order", "run_id", "role_order"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("discovery_runs.id", ondelete="RESTRICT"), nullable=False
    )
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("discovery_strategy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    role_key: Mapped[str] = mapped_column(String(64), nullable=False)
    role_order: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    eligibility: Mapped[dict] = mapped_column(JSON, nullable=False)
    selected_tool_key: Mapped[str | None] = mapped_column(String(80))
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT")
    )
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("discovery_targets.id", ondelete="RESTRICT")
    )
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    input_scope: Mapped[dict] = mapped_column(JSON, nullable=False)
    record_counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    budget_usage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    fallback_decision: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reason_code: Mapped[str | None] = mapped_column(String(80))
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
