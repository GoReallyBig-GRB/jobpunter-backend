"""Shared configuration lifecycle adapter for source refresh cadence."""
from datetime import timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select

from app.application.jobs.schedule_window import (
    format_clock,
    next_window_open,
    parse_clock,
    validate_window,
)
from app.infrastructure.db.models.discovery_schedule import (
    SourceRefreshSchedule,
    SourceRefreshScheduleEvent,
)
from app.infrastructure.db.models.source import Source

KEY_PREFIX = "discovery.source_refresh."


class SourceRefreshConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval_minutes: int = Field(ge=15, le=10080)
    reason: str = Field(min_length=8, max_length=1000)
    window_start: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    window_end: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    window_timezone: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 8:
            raise ValueError("reason must contain at least eight non-padding characters")
        return value

    @model_validator(mode="after")
    def supported_window(self):
        fields = (self.window_start, self.window_end, self.window_timezone)
        if any(v is not None for v in fields):
            if any(v is None for v in fields):
                raise ValueError("window_start, window_end and window_timezone are required together")
            validate_window(parse_clock(self.window_start), parse_clock(self.window_end), self.window_timezone)
        return self


def source_id_from_key(key: str) -> UUID | None:
    if not key.startswith(KEY_PREFIX):
        return None
    value = key.removeprefix(KEY_PREFIX)
    source_id = UUID(value)
    if str(source_id) != value:
        raise ValueError("Source configuration key must contain a canonical UUID")
    return source_id


def lock_source(db, key):
    source_id = source_id_from_key(key)
    if source_id is None:
        return None
    source = db.scalar(select(Source).where(Source.id == source_id).with_for_update())
    if source is None:
        raise ValueError("Source not found")
    return source


def schedule_snapshot(schedule):
    return {
        "status": schedule.status, "interval_minutes": schedule.interval_minutes,
        "window_start": format_clock(schedule.window_start_minute),
        "window_end": format_clock(schedule.window_end_minute),
        "window_timezone": schedule.window_timezone,
        "config_version_id": str(schedule.config_version_id) if schedule.config_version_id else None,
    }


def record_schedule_event(db, schedule, action, actor_id, reason, before, now):
    db.add(SourceRefreshScheduleEvent(
        schedule_id=schedule.id, schedule_revision=int(schedule.revision), action=action,
        actor_user_id=actor_id, reason=reason.strip(), before=before, after=schedule_snapshot(schedule),
        created_at=now,
    ))


def activate_version(db, version, actor_id, now):
    """Apply an already validated version in the publisher's transaction; never commit here."""
    source_id = source_id_from_key(version.config_key)
    if source_id is None:
        return
    config = SourceRefreshConfiguration.model_validate(version.payload)
    schedule = db.scalar(select(SourceRefreshSchedule).where(
        SourceRefreshSchedule.source_id == source_id,
    ).with_for_update().execution_options(populate_existing=True))
    before = schedule_snapshot(schedule) if schedule else None
    if schedule is None:
        schedule = SourceRefreshSchedule(source_id=source_id, status="ACTIVE",
                                         created_by_user_id=actor_id)
        db.add(schedule)
    else:
        schedule.revision += 1
    schedule.interval_minutes = config.interval_minutes
    schedule.window_start_minute = parse_clock(config.window_start) if config.window_start else None
    schedule.window_end_minute = parse_clock(config.window_end) if config.window_end else None
    schedule.window_timezone = config.window_timezone
    schedule.next_run_at = now + timedelta(minutes=config.interval_minutes)
    if config.window_timezone:
        schedule.next_run_at = next_window_open(
            schedule.next_run_at, schedule.window_start_minute,
            schedule.window_end_minute, config.window_timezone,
        )
    schedule.config_version_id = version.id
    schedule.updated_by_user_id = actor_id
    schedule.updated_at = now
    db.flush()
    record_schedule_event(db, schedule, "CREATED" if before is None else "CHANGED",
                          actor_id, config.reason, before, now)
