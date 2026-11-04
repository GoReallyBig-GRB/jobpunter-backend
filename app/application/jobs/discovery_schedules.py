"""Durable, administrator-owned scheduling for non-candidate discovery work."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.application.jobs.discovery_missions import next_calendar_schedule_at
from app.application.jobs.discovery_orchestration import ExecutionLimits, RetryPolicy
from app.infrastructure.db.models.discovery_orchestration import (
    DiscoveryRun,
    DiscoverySchedule,
    DiscoveryStrategyVersion,
)
from app.infrastructure.db.models.work_item import WorkItem


class FrequencyDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval_minutes: int | None = Field(default=None, ge=1, le=525_600)
    time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    weekday: int | None = Field(default=None, ge=0, le=6)


class DiscoveryScheduleConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    schedule_type: Literal[
        "GLOBAL_SWEEP", "SOURCE_REFRESH", "TARGET_REFRESH", "MARKET_DISCOVERY",
        "HEALTH_CHECK", "STALENESS_REVALIDATION",
    ]
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    frequency_type: Literal["INTERVAL", "DAILY", "WEEKLY"]
    frequency_definition: FrequencyDefinition
    window_definition: dict[str, Any] = Field(default_factory=dict)
    source_scope: list[str] = Field(default_factory=list, max_length=1000)
    target_scope: list[str] = Field(default_factory=list, max_length=1000)
    market_scope: list[str] = Field(default_factory=list, max_length=250)
    strategy_version_id: UUID
    priority: int = Field(default=100, ge=1, le=1000)
    overlap_policy: Literal["SKIP", "QUEUE"] = "SKIP"
    maximum_concurrent_runs: int = Field(default=1, ge=1, le=100)
    limits: ExecutionLimits
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    manual_execution_enabled: bool = True
    start_at: datetime | None = None
    end_at: datetime | None = None

    @model_validator(mode="after")
    def validate_frequency_and_scope(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        frequency = self.frequency_definition
        if self.frequency_type == "INTERVAL":
            if frequency.interval_minutes is None or frequency.time is not None or frequency.weekday is not None:
                raise ValueError("INTERVAL requires only interval_minutes")
        elif self.frequency_type == "DAILY":
            if frequency.time is None or frequency.interval_minutes is not None or frequency.weekday is not None:
                raise ValueError("DAILY requires only time")
        elif frequency.time is None or frequency.weekday is None or frequency.interval_minutes is not None:
            raise ValueError("WEEKLY requires time and weekday")
        if self.schedule_type == "SOURCE_REFRESH" and not self.source_scope:
            raise ValueError("SOURCE_REFRESH requires source_scope")
        if self.schedule_type == "TARGET_REFRESH" and not self.target_scope:
            raise ValueError("TARGET_REFRESH requires target_scope")
        if any(value is not None and value.tzinfo is None for value in (self.start_at, self.end_at)):
            raise ValueError("start_at and end_at must include a timezone")
        if self.end_at is not None and self.start_at is not None and self.end_at <= self.start_at:
            raise ValueError("end_at must be later than start_at")
        return self


def next_schedule_at(config: DiscoveryScheduleConfiguration, *, after: datetime) -> datetime:
    after = after.astimezone(UTC)
    frequency = config.frequency_definition
    if config.frequency_type == "INTERVAL":
        candidate = after + timedelta(minutes=frequency.interval_minutes or 0)
    else:
        candidate = next_calendar_schedule_at(
            schedule_type=config.frequency_type,
            schedule_time=frequency.time or "00:00",
            schedule_timezone=config.timezone,
            schedule_weekday=frequency.weekday,
            after=after,
        )
    if config.start_at is not None and candidate < config.start_at.astimezone(UTC):
        candidate = config.start_at.astimezone(UTC)
    return candidate


def schedule_configuration(row: DiscoverySchedule) -> DiscoveryScheduleConfiguration:
    return DiscoveryScheduleConfiguration.model_validate({
        "name": row.name,
        "schedule_type": row.schedule_type,
        "timezone": row.timezone,
        "frequency_type": row.frequency_type,
        "frequency_definition": row.frequency_definition,
        "window_definition": row.window_definition,
        "source_scope": row.source_scope,
        "target_scope": row.target_scope,
        "market_scope": row.market_scope,
        "strategy_version_id": row.strategy_version_id,
        "priority": row.priority,
        "overlap_policy": row.overlap_policy,
        "maximum_concurrent_runs": row.maximum_concurrent_runs,
        "limits": row.limits,
        "retry_policy": row.retry_policy,
        "manual_execution_enabled": row.manual_execution_enabled,
        "start_at": row.start_at,
        "end_at": row.end_at,
    })


def _canonical_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def create_discovery_run(
    db: Session, *, schedule: DiscoverySchedule, trigger_type: str, run_key: str,
    requested_by_user_id: UUID | None = None, reason: str | None = None,
    now: datetime | None = None, enqueue: bool = True,
) -> DiscoveryRun:
    """Create an exact-snapshot run and durable work item; replay by run key is safe."""
    existing = db.scalar(select(DiscoveryRun).where(DiscoveryRun.run_key == run_key))
    if existing is not None:
        return existing
    strategy = db.get(DiscoveryStrategyVersion, schedule.strategy_version_id)
    if strategy is None or strategy.status != "PUBLISHED":
        raise ValueError("schedule requires a published discovery strategy version")
    current = (now or datetime.now(UTC)).astimezone(UTC)
    config = schedule_configuration(schedule).model_dump(mode="json")
    snapshot = {
        "schedule": config,
        "schedule_revision": schedule.revision,
        "strategy": strategy.payload,
        "strategy_configuration_hash": strategy.configuration_hash,
    }
    run_id = uuid4()
    created_id = db.scalar(
        insert(DiscoveryRun).values(
            id=run_id, run_key=run_key, run_type=schedule.schedule_type,
            trigger_type=trigger_type, schedule_id=schedule.id, strategy_version_id=strategy.id,
            configuration_snapshot=snapshot, configuration_hash=_canonical_hash(snapshot),
            scope={"sources": schedule.source_scope, "targets": schedule.target_scope,
                   "markets": schedule.market_scope},
            status="PENDING" if enqueue else "BLOCKED", operation_counts={}, record_counts={},
            budget_outcome={}, retry_information={},
            error_details={} if enqueue else {
                "code": "OVERLAP_LIMIT", "message": "Active run limit reached."
            },
            correlation_id=str(uuid4()), requested_by_user_id=requested_by_user_id, reason=reason,
            next_attempt_at=current if enqueue else None, created_at=current, revision=1,
        ).on_conflict_do_nothing(constraint="uq_discovery_run_key").returning(DiscoveryRun.id)
    )
    if created_id is None:
        replay = db.scalar(select(DiscoveryRun).where(DiscoveryRun.run_key == run_key))
        if replay is None:
            raise RuntimeError("discovery run could not be retrieved after idempotent insert")
        return replay
    if enqueue:
        db.execute(insert(WorkItem).values(
            id=uuid4(), work_type="DISCOVERY_RUN", resource_type="DiscoveryRun",
            resource_id=created_id, status="PENDING",
            idempotency_key=f"discovery-run:{created_id}", attempt_count=0,
            error_details={}, created_at=current, updated_at=current, revision=1,
        ).on_conflict_do_nothing(constraint="uq_work_items_type_idempotency"))
    run = db.get(DiscoveryRun, created_id)
    if run is None:
        raise RuntimeError("created discovery run could not be retrieved")
    return run


def schedule_due_discovery_runs(
    db: Session, *, now: datetime | None = None, limit: int = 25,
) -> list[UUID]:
    """Claim due global schedules without blocking peers and durably advance every slot."""
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be from 1 to 1000")
    current = (now or datetime.now(UTC)).astimezone(UTC)
    due = db.scalars(
        select(DiscoverySchedule)
        .where(DiscoverySchedule.status == "ACTIVE", DiscoverySchedule.next_run_at <= current)
        .order_by(DiscoverySchedule.priority, DiscoverySchedule.next_run_at, DiscoverySchedule.id)
        .limit(limit).with_for_update(skip_locked=True)
    ).all()
    run_ids: list[UUID] = []
    for schedule in due:
        slot = schedule.next_run_at
        if slot is None:
            continue
        active = db.scalar(select(func.count()).select_from(DiscoveryRun).where(
            DiscoveryRun.schedule_id == schedule.id,
            DiscoveryRun.status.in_(("PENDING", "RUNNING")),
        )) or 0
        enqueue = schedule.overlap_policy == "QUEUE" or active < schedule.maximum_concurrent_runs
        run = create_discovery_run(
            db, schedule=schedule, trigger_type="SCHEDULED",
            run_key=f"scheduled:{schedule.id}:{slot.astimezone(UTC).isoformat()}",
            reason="Scheduled discovery slot", now=current, enqueue=enqueue,
        )
        run_ids.append(run.id)
        schedule.last_run_at = current
        schedule.next_run_at = next_schedule_at(schedule_configuration(schedule), after=current)
        if schedule.end_at is not None and schedule.next_run_at > schedule.end_at:
            schedule.status = "RETIRED"
            schedule.next_run_at = None
        schedule.revision += 1
        schedule.updated_at = current
    db.commit()
    return run_ids
