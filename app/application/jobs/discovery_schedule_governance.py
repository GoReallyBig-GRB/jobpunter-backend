"""Adapt general discovery schedules to the shared immutable configuration lifecycle."""
from uuid import UUID

from pydantic import Field, field_validator
from sqlalchemy import select

from app.application.jobs.discovery_schedules import (
    DiscoveryScheduleConfiguration,
    next_schedule_at,
)
from app.infrastructure.db.models.discovery_orchestration import (
    DiscoverySchedule,
    DiscoveryStrategyVersion,
)
from app.infrastructure.db.models.discovery_target import DiscoveryTarget
from app.infrastructure.db.models.source import Source

KEY_PREFIX = "discovery.schedule."


class GovernedDiscoveryScheduleConfiguration(DiscoveryScheduleConfiguration):
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("reason must contain at least three non-padding characters")
        return value


def schedule_id_from_key(key):
    if not key.startswith(KEY_PREFIX):
        return None
    value = key.removeprefix(KEY_PREFIX)
    schedule_id = UUID(value)
    if str(schedule_id) != value:
        raise ValueError("Schedule key must contain a canonical UUID")
    return schedule_id


def lock_schedule(db, key):
    schedule_id = schedule_id_from_key(key)
    if schedule_id is None:
        return None
    row = db.scalar(select(DiscoverySchedule).where(DiscoverySchedule.id == schedule_id)
                    .with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise ValueError("Discovery schedule not found")
    return row


def validate_references(db, config):
    strategy = db.get(DiscoveryStrategyVersion, config.strategy_version_id)
    if strategy is None or strategy.status != "PUBLISHED":
        raise ValueError("A published discovery strategy version is required")
    for model, scope in ((Source, config.source_scope), (DiscoveryTarget, config.target_scope)):
        if scope:
            ids = {UUID(value) for value in scope}
            found = set(db.scalars(select(model.id).where(model.id.in_(ids))))
            if found != ids:
                raise ValueError("Schedule references an unknown source or target")


def activate_version(db, version, actor_id, now):
    schedule_id = schedule_id_from_key(version.config_key)
    if schedule_id is None:
        return
    config = GovernedDiscoveryScheduleConfiguration.model_validate(version.payload)
    validate_references(db, config)
    row = db.get(DiscoverySchedule, schedule_id)
    if row is None or row.status == "RETIRED":
        raise ValueError("A retired or missing discovery schedule cannot be activated")
    if config.end_at is not None and config.end_at <= now:
        raise ValueError("The discovery schedule configuration has expired")
    for key, value in config.model_dump(mode="python", exclude={"reason"}).items():
        setattr(row, key, value)
    row.config_version_id = version.id
    row.updated_by_user_id = actor_id
    row.updated_at = now
    row.revision += 1
    row.next_run_at = next_schedule_at(config, after=now) if row.status == "ACTIVE" else None
    if row.next_run_at is not None and config.end_at is not None and row.next_run_at > config.end_at:
        raise ValueError("No eligible future occurrence before end date")
