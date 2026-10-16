"""Durable candidate discovery settings, missions, and per-source execution history."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, RevisionMixin, UUIDPrimaryKeyMixin


class CandidateDiscoverySettings(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "candidate_discovery_settings"
    __table_args__ = (
        UniqueConstraint("candidate_profile_id", name="uq_discovery_settings_candidate"),
        Index("ix_discovery_settings_due", "status", "next_run_at"),
        CheckConstraint(
            "schedule_type IN ('INTERVAL', 'DAILY', 'WEEKLY')",
            name="ck_discovery_settings_schedule_type",
        ),
        CheckConstraint(
            "schedule_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]
            "(schedule_type = 'WEEKLY' AND schedule_weekday IS NOT NULL "
            "AND schedule_weekday BETWEEN 0 AND 6) "
            "OR (schedule_type IN ('INTERVAL', 'DAILY') AND schedule_weekday IS NULL)",
            name="ck_discovery_settings_schedule_weekday",
        ),
    )

    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DISABLED")
    cadence_minutes: Mapped[int] = mapped_column(nullable=False, default=1440)
    schedule_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="INTERVAL", server_default="INTERVAL"
    )
    schedule_time: Mapped[str] = mapped_column(
        String(5), nullable=False, default="09:00", server_default="09:00"
    )
    schedule_timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC"
    )
    schedule_weekday: Mapped[int | None] = mapped_column(Integer)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    target_opportunities: Mapped[int] = mapped_column(nullable=False, default=20)
    max_job_age_days: Mapped[int] = mapped_column(nullable=False, default=21)
    verification_required: Mapped[bool] = mapped_column(nullable=False, default=True)
    evaluation_depth: Mapped[str] = mapped_column(String(32), nullable=False, default="preliminary")
    source_filters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    last_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class DiscoveryMission(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "discovery_missions"
    __table_args__ = (
        UniqueConstraint(
            "candidate_profile_id", "idempotency_key", name="uq_discovery_mission_candidate_key"
        ),
        Index("ix_discovery_missions_status_created", "status", "created_at"),
    )

    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    settings_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidate_discovery_settings.id", ondelete="RESTRICT")
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    policy_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    current_wave_key: Mapped[str | None] = mapped_column(String(64))
    wave_index: Mapped[int] = mapped_column(nullable=False, default=0)
    cumulative_usable_opportunities: Mapped[int] = mapped_column(nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class DiscoveryMissionSourceRun(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "discovery_mission_source_runs"
    __table_args__ = (
        UniqueConstraint(
            "mission_id", "source_id", "wave_key", "attempt_number",
            name="uq_mission_source_wave_attempt",
        ),
        UniqueConstraint("run_key", name="uq_discovery_mission_source_run_key"),
        Index("ix_mission_source_runs_status", "status"),
    )

    mission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("discovery_missions.id", ondelete="RESTRICT"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    source_connector_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_connectors.id", ondelete="RESTRICT"), nullable=False
    )
    acquisition_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("acquisition_runs.id", ondelete="RESTRICT")
    )
    wave_key: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_number: Mapped[int] = mapped_column(nullable=False, default=1)
    run_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    acquired_count: Mapped[int] = mapped_column(nullable=False, default=0)
    persisted_count: Mapped[int] = mapped_column(nullable=False, default=0)
    canonicalized_count: Mapped[int] = mapped_column(nullable=False, default=0)
    usable_count: Mapped[int] = mapped_column(nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
",
            name="ck_discovery_settings_schedule_time",
        ),
        CheckConstraint(
            "(schedule_type = 'WEEKLY' AND schedule_weekday IS NOT NULL "
            "AND schedule_weekday BETWEEN 0 AND 6) "
            "OR (schedule_type IN ('INTERVAL', 'DAILY') AND schedule_weekday IS NULL)",
            name="ck_discovery_settings_schedule_weekday",
        ),
    )

    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DISABLED")
    cadence_minutes: Mapped[int] = mapped_column(nullable=False, default=1440)
    schedule_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="INTERVAL", server_default="INTERVAL"
    )
    schedule_time: Mapped[str] = mapped_column(
        String(5), nullable=False, default="09:00", server_default="09:00"
    )
    schedule_timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC"
    )
    schedule_weekday: Mapped[int | None] = mapped_column(Integer)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    target_opportunities: Mapped[int] = mapped_column(nullable=False, default=20)
    max_job_age_days: Mapped[int] = mapped_column(nullable=False, default=21)
    verification_required: Mapped[bool] = mapped_column(nullable=False, default=True)
    evaluation_depth: Mapped[str] = mapped_column(String(32), nullable=False, default="preliminary")
    source_filters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    last_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class DiscoveryMission(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "discovery_missions"
    __table_args__ = (
        UniqueConstraint(
            "candidate_profile_id", "idempotency_key", name="uq_discovery_mission_candidate_key"
        ),
        Index("ix_discovery_missions_status_created", "status", "created_at"),
    )

    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    settings_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidate_discovery_settings.id", ondelete="RESTRICT")
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    policy_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    current_wave_key: Mapped[str | None] = mapped_column(String(64))
    wave_index: Mapped[int] = mapped_column(nullable=False, default=0)
    cumulative_usable_opportunities: Mapped[int] = mapped_column(nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class DiscoveryMissionSourceRun(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "discovery_mission_source_runs"
    __table_args__ = (
        UniqueConstraint(
            "mission_id", "source_id", "wave_key", "attempt_number",
            name="uq_mission_source_wave_attempt",
        ),
        UniqueConstraint("run_key", name="uq_discovery_mission_source_run_key"),
        Index("ix_mission_source_runs_status", "status"),
    )

    mission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("discovery_missions.id", ondelete="RESTRICT"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    source_connector_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_connectors.id", ondelete="RESTRICT"), nullable=False
    )
    acquisition_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("acquisition_runs.id", ondelete="RESTRICT")
    )
    wave_key: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_number: Mapped[int] = mapped_column(nullable=False, default=1)
    run_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    acquired_count: Mapped[int] = mapped_column(nullable=False, default=0)
    persisted_count: Mapped[int] = mapped_column(nullable=False, default=0)
    canonicalized_count: Mapped[int] = mapped_column(nullable=False, default=0)
    usable_count: Mapped[int] = mapped_column(nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
