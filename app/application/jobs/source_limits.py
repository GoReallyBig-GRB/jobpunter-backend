"""Per-source acquisition run-rate limit evaluation (E4.4).

The count is a point-in-time read: under concurrent workers the limit can be exceeded by at most
the number of workers that pass the check simultaneously. It is a pacing control, not an atomic
budget reservation.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import AcquisitionRun
from app.infrastructure.db.models.discovery_source_limit import DiscoverySourceLimit


def source_run_limit_reached(db: Session, source_id: UUID, *, now: datetime | None = None) -> dict | None:
    """Return the limit state when the source has used its allowance, else None."""
    limit = db.scalar(
        select(DiscoverySourceLimit).where(DiscoverySourceLimit.source_id == source_id)
        .execution_options(populate_existing=True)
    )
    if limit is None:
        return None
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(minutes=limit.window_minutes)
    used = int(db.scalar(
        select(func.count(AcquisitionRun.id)).where(
            AcquisitionRun.source_id == source_id, AcquisitionRun.created_at >= cutoff
        )
    ) or 0)
    if used < limit.max_runs:
        return None
    return {"max_runs": limit.max_runs, "window_minutes": limit.window_minutes, "runs_in_window": used}
