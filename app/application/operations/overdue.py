"""Read-only detection of missed schedules and overdue durable work.

Recovery is already automatic: due schedules are re-enqueued on the next scheduler poll, and
expired claims, leases and outbox locks are reclaimed. This module only makes lag visible so a
stopped scheduler/host or a stuck queue is noticed. It returns counts and ages, never payloads.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import OutboxEvent, Source, WorkItem
from app.infrastructure.db.models.discovery_orchestration import DiscoverySchedule
from app.infrastructure.db.models.discovery_schedule import SourceRefreshSchedule

# A schedule is "overdue" when it is this far past its next_run_at (scheduler polls every ~15 s).
SCHEDULE_GRACE_SECONDS = 600
# Work is "aged" when untouched for longer than a worker's maximum cycle (default 300 s) plus slack.
WORK_AGE_SECONDS = 900


def _age_seconds(now: datetime, value: datetime | None) -> int | None:
    return None if value is None else max(int((now - value).total_seconds()), 0)


def overdue_snapshot(
    db: Session,
    *,
    now: datetime | None = None,
    schedule_grace_seconds: int = SCHEDULE_GRACE_SECONDS,
    work_age_seconds: int = WORK_AGE_SECONDS,
) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    schedule_cutoff = current - timedelta(seconds=schedule_grace_seconds)
    work_cutoff = current - timedelta(seconds=work_age_seconds)

    refresh_oldest = db.scalar(
        select(func.min(SourceRefreshSchedule.next_run_at))
        .join(Source, Source.id == SourceRefreshSchedule.source_id)
        .where(
            SourceRefreshSchedule.status == "ACTIVE",
            Source.status == "ACTIVE",
            SourceRefreshSchedule.next_run_at <= schedule_cutoff,
        )
    )
    refresh_count = int(db.scalar(
        select(func.count(SourceRefreshSchedule.id))
        .join(Source, Source.id == SourceRefreshSchedule.source_id)
        .where(
            SourceRefreshSchedule.status == "ACTIVE",
            Source.status == "ACTIVE",
            SourceRefreshSchedule.next_run_at <= schedule_cutoff,
        )
    ) or 0)
    discovery_filter = (
        DiscoverySchedule.status == "ACTIVE",
        DiscoverySchedule.next_run_at.is_not(None),
        DiscoverySchedule.next_run_at <= schedule_cutoff,
    )
    discovery_count = int(db.scalar(
        select(func.count(DiscoverySchedule.id)).where(*discovery_filter)
    ) or 0)
    discovery_oldest = db.scalar(select(func.min(DiscoverySchedule.next_run_at)).where(*discovery_filter))
    oldest_due = min((v for v in (refresh_oldest, discovery_oldest) if v is not None), default=None)

    pending_work = int(db.scalar(
        select(func.count(WorkItem.id)).where(
            WorkItem.status == "PENDING", WorkItem.updated_at <= work_cutoff
        )
    ) or 0)
    stuck_processing = int(db.scalar(
        select(func.count(WorkItem.id)).where(
            WorkItem.status == "PROCESSING",
            WorkItem.started_at.is_not(None),
            WorkItem.started_at <= work_cutoff,
        )
    ) or 0)
    oldest_outbox = db.scalar(
        select(func.min(OutboxEvent.available_at)).where(
            OutboxEvent.status == "PENDING", OutboxEvent.available_at <= current
        )
    )
    outbox_age = _age_seconds(current, oldest_outbox)
    return {
        "schedule_grace_seconds": schedule_grace_seconds,
        "work_age_seconds": work_age_seconds,
        "overdue_source_refresh_schedules": refresh_count,
        "overdue_discovery_schedules": discovery_count,
        "oldest_overdue_schedule_seconds": _age_seconds(current, oldest_due),
        "aged_pending_work_items": pending_work,
        "stuck_processing_work_items": stuck_processing,
        "oldest_due_outbox_event_seconds": outbox_age,
    }

