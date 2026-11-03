"""Scheduled, candidate-independent source refresh (E4.1-E4.3, E4.8).

A due `DiscoverySchedule` enqueues exactly one idempotent SOURCE_REFRESH work item per
occurrence and never while the previous occurrence is unfinished. Execution runs each of the
source's connector/target scopes through the same policy path as candidate missions
(`execute_source_scope`) and records schedule-owned steps; it creates no candidate missions
or notifications.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.application.jobs.registered_acquisition import RegisteredAcquisitionService
from app.application.jobs.schedule_window import in_window, next_window_open
from app.application.jobs.source_scope_execution import execute_source_scope
from app.infrastructure.db.models import (
    DiscoveryMissionSourceRun, DiscoverySchedule, DiscoveryTarget, Source, SourceConnector, WorkItem,
)

WORK_TYPE = "SOURCE_REFRESH"
OPEN_STATES = ("PENDING", "PROCESSING")


def enqueue_due_source_refreshes(db: Session, *, now: datetime | None = None, limit: int = 25) -> list[UUID]:
    """Enqueue one occurrence per due, active schedule of an active source; return schedule ids."""
    if limit < 1:
        raise ValueError("limit must be positive")
    current = now or datetime.now(timezone.utc)
    due = db.scalars(
        select(DiscoverySchedule)
        .join(Source, Source.id == DiscoverySchedule.source_id)
        .where(
            DiscoverySchedule.status == "ACTIVE",
            DiscoverySchedule.next_run_at <= current,
            Source.status == "ACTIVE",
        )
        .order_by(DiscoverySchedule.next_run_at, DiscoverySchedule.id)
        .limit(limit)
        .with_for_update(skip_locked=True, of=DiscoverySchedule)
    ).all()
    enqueued: list[UUID] = []
    for schedule in due:
        in_flight = db.scalar(select(WorkItem.id).where(
            WorkItem.work_type == WORK_TYPE,
            WorkItem.resource_id == schedule.id,
            WorkItem.status.in_(OPEN_STATES),
        ).limit(1))
        if in_flight is not None:
            continue  # overlap policy: skip; the slot stays due and is picked up once finished
        if schedule.window_timezone is not None and not in_window(
            current, schedule.window_start_minute, schedule.window_end_minute, schedule.window_timezone,
        ):
            # Outside the allowed window: do nothing now and wait for the window to open.
            schedule.next_run_at = next_window_open(
                current, schedule.window_start_minute, schedule.window_end_minute, schedule.window_timezone,
            )
            continue
        slot = schedule.next_run_at.astimezone(timezone.utc)
        key = f"{schedule.id}:{slot.isoformat()}"
        exists = db.scalar(select(WorkItem.id).where(
            WorkItem.work_type == WORK_TYPE, WorkItem.idempotency_key == key,
        ))
        if exists is None:
            db.add(WorkItem(
                work_type=WORK_TYPE, resource_type="DiscoverySchedule", resource_id=schedule.id,
                status="PENDING", idempotency_key=key,
            ))
            enqueued.append(schedule.id)
        # Refreshes are not slot-sensitive: anchor the next run to now so downtime never causes a burst.
        schedule.next_run_at = current + timedelta(minutes=schedule.interval_minutes)
        schedule.revision += 1
    db.commit()
    return enqueued


def process_source_refresh(db: Session, work_item_id: UUID) -> str:
    """Run one claimed occurrence; return the persisted work-item status."""
    item = db.scalar(select(WorkItem).where(WorkItem.id == work_item_id).with_for_update())
    if item is None:
        return "MISSING"
    if item.status in {"COMPLETED", "CANCELLED", "FAILED"}:
        return item.status
    now = datetime.now(timezone.utc)
    schedule = db.get(DiscoverySchedule, item.resource_id) if item.resource_id else None
    source = db.get(Source, schedule.source_id) if schedule is not None else None
    if schedule is None or source is None or schedule.status != "ACTIVE" or source.status != "ACTIVE":
        item.status = "CANCELLED"
        item.completed_at = now
        item.result = {"outcome": "CANCELLED", "reason": "SCHEDULE_OR_SOURCE_NOT_ACTIVE"}
        db.commit()
        return "CANCELLED"

    occurrence = item.idempotency_key
    scopes = db.execute(
        select(SourceConnector, DiscoveryTarget)
        .outerjoin(DiscoveryTarget, and_(
            DiscoveryTarget.source_id == source.id,
            DiscoveryTarget.source_connector_id == SourceConnector.id,
        ))
        .where(SourceConnector.source_id == source.id, SourceConnector.status == "ACTIVE")
        .order_by(SourceConnector.connector_type, SourceConnector.id, DiscoveryTarget.created_at, DiscoveryTarget.id)
    ).all()
    for connector, target in scopes:
        run_key = f"schedule:{occurrence}:connector:{connector.id}:target:{target.id if target is not None else 'none'}"
        # Duplicate delivery or crash recovery never repeats a scope that already has a step.
        if db.scalar(select(DiscoveryMissionSourceRun.id).where(DiscoveryMissionSourceRun.run_key == run_key)):
            continue
        step = DiscoveryMissionSourceRun(
            id=uuid4(), mission_id=None, schedule_id=schedule.id, source_id=source.id,
            source_connector_id=connector.id, wave_key="scheduled", attempt_number=1,
            run_key=run_key, status="RUNNING", acquired_count=0, persisted_count=0,
            canonicalized_count=0, usable_count=0,
            error_details=(
                {
                    "target_id": str(target.id), "target_kind": target.target_kind,
                    "target_url_sha256": hashlib.sha256(target.url.encode("utf-8")).hexdigest(),
                } if target is not None else {"scope_outcome": "SKIPPED"}
            ),
            started_at=now, created_at=now,
        )
        db.add(step)
        db.commit()
        execute_source_scope(
            db, source=source, connector=connector, target=target, source_run=step, run_key=run_key,
            service_factory=lambda repository, registry: RegisteredAcquisitionService(repository, registry),
        )

    steps = db.scalars(select(DiscoveryMissionSourceRun).where(
        DiscoveryMissionSourceRun.schedule_id == schedule.id,
        DiscoveryMissionSourceRun.run_key.like(f"schedule:{occurrence}:%"),
    )).all()
    skipped = sum(1 for s in steps if (s.error_details or {}).get("scope_outcome") == "SKIPPED")
    failed = sum(1 for s in steps if s.status in {"FAILED", "PARTIAL"})
    executed = len(steps) - skipped
    schedule.last_run_at = now
    item.status = "COMPLETED"
    item.completed_at = now
    item.result = {
        "outcome": "EXECUTED" if executed > 0 else "NOTHING_EXECUTED",
        "steps": len(steps), "executed": executed, "skipped": skipped, "failed": failed,
    }
    db.commit()
    return "COMPLETED"
