"""Per-source acquisition run-rate limit evaluation (E4.4).

The count is a point-in-time read: under concurrent workers the limit can be exceeded by at most
the number of workers that pass the check simultaneously. It is a pacing control, not an atomic
budget reservation.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import AcquisitionRun, SourceConnector
from app.infrastructure.db.models.discovery_source_limit import DiscoverySourceLimit
from app.infrastructure.db.models.discovery_tool import DiscoveryToolControl

GLOBAL_BUDGET_KEY = "ALL"


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


def _budget_row(db: Session, key: str) -> DiscoveryToolControl | None:
    row = db.scalar(
        select(DiscoveryToolControl).where(DiscoveryToolControl.tool_key == key)
        .execution_options(populate_existing=True)
    )
    return row if row is not None and row.max_runs is not None else None


def run_budget_usage(db: Session, key: str, row: DiscoveryToolControl, now: datetime) -> int:
    cutoff = now - timedelta(minutes=row.window_minutes)
    query = select(func.count(AcquisitionRun.id)).where(AcquisitionRun.created_at >= cutoff)
    if key != GLOBAL_BUDGET_KEY:
        query = query.join(SourceConnector, SourceConnector.id == AcquisitionRun.source_connector_id).where(
            SourceConnector.connector_type == key
        )
    return int(db.scalar(query) or 0)


def run_budget_reached(db: Session, tool_key: str, *, now: datetime | None = None) -> dict | None:
    """Return the exhausted budget (tool first, then global) or None. Point-in-time count like source limits."""
    current = now or datetime.now(timezone.utc)
    tool = str(tool_key).strip().upper()
    for scope, key in (("TOOL", tool), ("GLOBAL", GLOBAL_BUDGET_KEY)):
        row = _budget_row(db, key)
        if row is None:
            continue
        used = run_budget_usage(db, key, row, current)
        if used >= row.max_runs:
            return {"budget_scope": scope, "max_runs": row.max_runs, "window_minutes": row.window_minutes,
                    "runs_in_window": used}
    return None


def effective_config_snapshot(db: Session, *, source_id: UUID, tool_key: str, schedule=None) -> dict:
    """The admin controls in force right now for one source/tool step (no secrets, no URLs)."""
    tool = str(tool_key).strip().upper()
    control = db.scalar(select(DiscoveryToolControl).where(DiscoveryToolControl.tool_key == tool)
                        .execution_options(populate_existing=True))
    global_row = _budget_row(db, GLOBAL_BUDGET_KEY)
    source_limit = db.scalar(select(DiscoverySourceLimit).where(DiscoverySourceLimit.source_id == source_id)
                             .execution_options(populate_existing=True))

    def budget(row):
        return None if row is None or row.max_runs is None else {
            "max_runs": row.max_runs, "window_minutes": row.window_minutes}

    snapshot = {
        "strategy": "code-defined-waves/v1",
        "tool": {"key": tool, "status": control.status if control else "ENABLED", "budget": budget(control)},
        "global_budget": budget(global_row),
        "source_limit": None if source_limit is None else {
            "max_runs": source_limit.max_runs, "window_minutes": source_limit.window_minutes},
    }
    if schedule is not None:
        from app.application.jobs.schedule_window import format_clock

        snapshot["schedule"] = {
            "revision": int(schedule.revision), "interval_minutes": schedule.interval_minutes,
            "window_start": format_clock(schedule.window_start_minute),
            "window_end": format_clock(schedule.window_end_minute),
            "window_timezone": schedule.window_timezone,
        }
    return snapshot
