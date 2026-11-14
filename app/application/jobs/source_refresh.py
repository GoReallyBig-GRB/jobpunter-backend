"""Scheduled, candidate-independent source refresh (E4.1-E4.3, E4.8).

A due `SourceRefreshSchedule` enqueues exactly one idempotent SOURCE_REFRESH work item per
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
from app.application.jobs.source_refresh_governance import KEY_PREFIX, SourceRefreshConfiguration
from app.application.jobs.schedule_window import parse_clock
from app.infrastructure.db.models import (
    AcquisitionRun, DiscoveryMissionSourceRun, DiscoveryTarget, Source, SourceConnector,
    SourceRefreshSchedule, WorkItem, GovernanceConfigVersion,
)
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository

WORK_TYPE = "SOURCE_REFRESH"
OPEN_STATES = ("PENDING", "PROCESSING")


def enqueue_due_source_refreshes(db: Session, *, now: datetime | None = None, limit: int = 25) -> list[UUID]:
    """Enqueue one occurrence per due, active schedule of an active source; return schedule ids."""
    if limit < 1:
        raise ValueError("limit must be positive")
    current = now or datetime.now(timezone.utc)
    due = db.scalars(
        select(SourceRefreshSchedule)
        .join(Source, Source.id == SourceRefreshSchedule.source_id)
        .where(
            SourceRefreshSchedule.status == "ACTIVE",
            SourceRefreshSchedule.config_version_id.is_not(None),
            SourceRefreshSchedule.next_run_at <= current,
            Source.status == "ACTIVE",
        )
        .order_by(SourceRefreshSchedule.next_run_at, SourceRefreshSchedule.id)
        .limit(limit)
        .with_for_update(skip_locked=True, of=SourceRefreshSchedule)
    ).all()
    enqueued: list[UUID] = []
    for schedule in due:
        version = db.get(GovernanceConfigVersion, schedule.config_version_id)
        if (version is None or version.status != "PUBLISHED"
                or version.config_key != KEY_PREFIX + str(schedule.source_id)):
            continue
        config = SourceRefreshConfiguration.model_validate(version.payload)
        window_start = parse_clock(config.window_start) if config.window_start else None
        window_end = parse_clock(config.window_end) if config.window_end else None
        in_flight = db.scalar(select(WorkItem.id).where(
            WorkItem.work_type == WORK_TYPE,
            WorkItem.resource_id == schedule.id,
            WorkItem.status.in_(OPEN_STATES),
        ).limit(1))
        if in_flight is not None:
            continue  # overlap policy: skip; the slot stays due and is picked up once finished
        if config.window_timezone is not None and not in_window(
            current, window_start, window_end, config.window_timezone,
        ):
            # Outside the allowed window: do nothing now and wait for the window to open.
            schedule.next_run_at = next_window_open(
                current, window_start, window_end, config.window_timezone,
            )
            continue
        slot = schedule.next_run_at.astimezone(timezone.utc)
        key = f"{schedule.id}:{slot.isoformat()}"
        exists = db.scalar(select(WorkItem.id).where(
            WorkItem.work_type == WORK_TYPE, WorkItem.idempotency_key == key,
        ))
        if exists is None:
            db.add(WorkItem(
                work_type=WORK_TYPE, resource_type="SourceRefreshSchedule", resource_id=schedule.id,
                status="PENDING", idempotency_key=key,
                source_refresh_config_version_id=schedule.config_version_id,
            ))
            enqueued.append(schedule.id)
        # Refreshes are not slot-sensitive: anchor the next run to now so downtime never causes a burst.
        schedule.next_run_at = current + timedelta(minutes=config.interval_minutes)
        schedule.revision += 1
    db.commit()
    return enqueued


def process_source_refresh(db: Session, work_item_id: UUID, *, execution_id: UUID | None = None) -> str:
    """Run an occurrence, resuming incomplete scopes without repeating terminal acquisitions.

    The optional execution token fences durable worker claims. Direct callers without
    a claim token retain the legacy administrative/test path.
    """

    def claimed_item() -> WorkItem | None:
        # Force a fresh read even when this Session cached an older claim.
        row = db.scalar(
            select(WorkItem).where(WorkItem.id == work_item_id)
            .with_for_update().execution_options(populate_existing=True)
        )
        if execution_id is not None and (
            row is None or row.status != "PROCESSING" or row.execution_id != execution_id
        ):
            db.rollback()
            return None
        return row

    item = claimed_item()
    if item is None:
        return "STALE_CLAIM" if execution_id is not None else "MISSING"
    if item.status in {"COMPLETED", "CANCELLED", "FAILED"}:
        return item.status
    now = datetime.now(timezone.utc)
    schedule = db.get(SourceRefreshSchedule, item.resource_id) if item.resource_id else None
    source = db.get(Source, schedule.source_id) if schedule is not None else None
    if schedule is None or source is None or schedule.status != "ACTIVE" or source.status != "ACTIVE":
        item.status = "CANCELLED"
        item.completed_at = now
        item.execution_id = None
        item.result = {"outcome": "CANCELLED", "reason": "SCHEDULE_OR_SOURCE_NOT_ACTIVE"}
        db.commit()
        return "CANCELLED"

    version = db.get(GovernanceConfigVersion, item.source_refresh_config_version_id) if item.source_refresh_config_version_id else None
    if (version is None or version.config_key != KEY_PREFIX + str(source.id)
            or version.status not in {"PUBLISHED", "RETIRED"}):
        item.status = "FAILED"
        item.error_code = "SOURCE_REFRESH_CONFIG_UNAVAILABLE"
        item.execution_id = None
        item.completed_at = now
        db.commit()
        return "FAILED"
    config = SourceRefreshConfiguration.model_validate(version.payload)
    pinned_schedule = {
        "config_version_id": str(version.id), "version": version.version, "revision": version.version,
        **config.model_dump(exclude={"reason"}),
    }
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
        item = claimed_item()
        if item is None:
            return "STALE_CLAIM"
        run_key = f"schedule:{occurrence}:connector:{connector.id}:target:{target.id if target is not None else 'none'}"
        step = db.scalar(select(DiscoveryMissionSourceRun).where(
            DiscoveryMissionSourceRun.run_key == run_key
        ))
        if step is not None and step.status not in {"PENDING", "RUNNING"}:
            continue  # A terminal step is durable evidence: never acquire it twice.
        if step is None:
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
                config_snapshot={"schedule": pinned_schedule},
                started_at=now, created_at=now,
            )
            db.add(step)
            db.commit()

        acquisition = db.scalar(select(AcquisitionRun).where(
            AcquisitionRun.source_id == source.id, AcquisitionRun.run_key == run_key,
        ))
        if acquisition is not None and acquisition.status == "RUNNING" and (
            acquisition.lease_expires_at is not None
            and acquisition.lease_expires_at <= datetime.now(timezone.utc)
        ):
            # The existing repository recovery operation uses row locking and marks
            # abandoned acquisitions failed. A late worker must not overwrite it.
            SqlAlchemyAcquisitionRepository(db).recover_stale_runs()
            db.refresh(acquisition)

        if acquisition is not None:
            if acquisition.status in {"PENDING", "RUNNING"}:
                item = claimed_item()
                if item is None:
                    return "STALE_CLAIM"
                # Waiting is not execution: free the worker slot and preserve
                # attempts while the existing acquisition finishes or expires.
                wait_until = datetime.now(timezone.utc) + timedelta(seconds=60)
                item.status = "PENDING"
                item.started_at = None
                item.execution_id = None
                item.result = {
                    "outcome": "WAITING_FOR_ACQUISITION",
                    "not_before": wait_until.isoformat(),
                }
                db.commit()
                return "DEFERRED"

            metrics = acquisition.metrics or {}
            step.acquisition_run_id = acquisition.id
            step.acquired_count = int(metrics.get("acquired_count") or 0)
            step.persisted_count = int(metrics.get("persisted_count") or 0)
            step.status = {
                "SUCCEEDED": "SUCCEEDED" if step.acquired_count else "SUCCEEDED_EMPTY",
                "PARTIAL": "PARTIAL",
                "RESTRICTED": "RESTRICTED",
                "UNAVAILABLE": "UNAVAILABLE",
                "FAILED": "FAILED",
            }.get(acquisition.status, "FAILED")
            if step.status in {"FAILED", "PARTIAL", "RESTRICTED", "UNAVAILABLE"}:
                step.error_code = (
                    (acquisition.error_summary or "ACQUISITION_NOT_SUCCESSFUL")[:64]
                )
            step.error_details = {
                **(step.error_details or {}), "recovered_from_acquisition_run": True,
            }
            step.completed_at = acquisition.completed_at or datetime.now(timezone.utc)
            db.commit()
            continue

        # No acquisition was ever registered: it is safe to resume this existing
        # step using the original idempotent run key, never create a second step.
        if not (step.config_snapshot or {}).get("schedule", {}).get("config_version_id"):
            step.config_snapshot = {**(step.config_snapshot or {}), "schedule": pinned_schedule}
        execute_source_scope(
            db, source=source, connector=connector, target=target, source_run=step, run_key=run_key,
            service_factory=lambda repository, registry: RegisteredAcquisitionService(repository, registry),
        )
        if step.status in {"PENDING", "RUNNING"}:
            item = claimed_item()
            if item is None:
                return "STALE_CLAIM"
            item.status = "PENDING"
            item.started_at = None
            item.execution_id = None
            item.result = {
                "outcome": "WAITING_FOR_ACQUISITION",
                "not_before": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
            }
            db.commit()
            return "DEFERRED"

    item = claimed_item()
    if item is None:
        return "STALE_CLAIM"
    steps = db.scalars(select(DiscoveryMissionSourceRun).where(
        DiscoveryMissionSourceRun.schedule_id == schedule.id,
        DiscoveryMissionSourceRun.run_key.like(f"schedule:{occurrence}:%"),
    )).all()
    # Fail closed if an unexpected nonterminal step remains. Never call this
    # occurrence completed while a source scope is still RUNNING or PENDING.
    if any(s.status in {"PENDING", "RUNNING"} for s in steps):
        item.status = "PENDING"
        item.started_at = None
        item.execution_id = None
        item.result = {
            "outcome": "WAITING_FOR_ACQUISITION",
            "not_before": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
        }
        db.commit()
        return "DEFERRED"
    skipped = sum(1 for s in steps if (s.error_details or {}).get("scope_outcome") == "SKIPPED")
    failed = sum(1 for s in steps if s.status in {"FAILED", "PARTIAL"})
    executed = len(steps) - skipped
    schedule.last_run_at = now
    item.status = "COMPLETED"
    item.completed_at = now
    item.execution_id = None
    item.result = {
        "outcome": "EXECUTED" if executed > 0 else "NOTHING_EXECUTED",
        "steps": len(steps), "executed": executed, "skipped": skipped, "failed": failed,
    }
    db.commit()
    return "COMPLETED"
