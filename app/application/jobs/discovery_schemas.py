"""API contracts for candidate-owned discovery settings and missions."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DiscoverySettingsUpdate(BaseModel):
    status: str = Field(pattern="^(ENABLED|DISABLED)$")
    cadence_minutes: int = Field(ge=15)
    schedule_type: str = Field(default="INTERVAL", pattern="^(INTERVAL|DAILY|WEEKLY)$")
    schedule_time: str = Field(default="09:00", pattern=r"^(?:[01]\\d|2[0-3]):[0-5]\\d$")
    schedule_timezone: str = Field(default="UTC", min_length=1, max_length=64)
    schedule_weekday: int | None = Field(default=None, ge=0, le=6)
    target_opportunities: int = Field(ge=1)
    max_job_age_days: int = Field(ge=1)
    verification_required: bool = True
    evaluation_depth: str = Field(pattern="^(discovery_only|preliminary|full)$")
    source_filters: dict = Field(default_factory=dict)
    expected_revision: int | None = Field(default=None, ge=1)


class DiscoverySettingsResponse(BaseModel):
    id: UUID
    candidate_profile_id: UUID
    status: str
    cadence_minutes: int
    schedule_type: str
    schedule_time: str
    schedule_timezone: str
    schedule_weekday: int | None
    next_run_at: datetime | None
    target_opportunities: int
    max_job_age_days: int
    verification_required: bool
    evaluation_depth: str
    source_filters: dict
    last_scheduled_at: datetime | None
    revision: int


class DiscoveryMissionCreate(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=255)


class DiscoveryMissionResponse(BaseModel):
    id: UUID
    candidate_profile_id: UUID
    settings_id: UUID | None
    idempotency_key: str
    trigger_type: str
    status: str
    policy_snapshot: dict
    current_wave_key: str | None
    wave_index: int
    cumulative_usable_opportunities: int
    started_at: datetime | None
    completed_at: datetime | None
    next_attempt_at: datetime | None
    error_code: str | None
    revision: int
    created_at: datetime
