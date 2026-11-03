"""Discovery alerts (E5.5): evaluate persisted metrics, track episodes, email administrators once.

Approved defaults:
- STALE_SOURCE: an ACTIVE source with no successful acquisition for 72 h (a source that has never
  succeeded alerts only once it is itself older than 72 h, so new sources get a grace period).
- TOOL_FAILURE_RATE: over the last 24 h, a tool's non-skipped terminal steps include at least 5
  and more than 50% of them failed (FAILED, RESTRICTED or UNAVAILABLE). Deliberate skips never count.
An alert is one episode: it is emailed once when opened, can be acknowledged (which stops nothing
but records ownership), and resolves automatically when its condition clears. A recurrence opens
a new episode.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.notifications.service import _insert_notification
from app.infrastructure.db.models import (
    AcquisitionRun, AdminGrant, DiscoveryAlert, DiscoveryMissionSourceRun, Source, SourceConnector, User,
)

STALE_AFTER_HOURS = 72
FAILURE_WINDOW_HOURS = 24
FAILURE_RATE_THRESHOLD = 0.5
FAILURE_MIN_STEPS = 5
TERMINAL_STATUSES = {"SUCCEEDED", "CANONICALIZED", "SUCCEEDED_EMPTY", "PARTIAL", "FAILED", "RESTRICTED", "UNAVAILABLE"}
FAILURE_STATUSES = {"FAILED", "RESTRICTED", "UNAVAILABLE"}
ALERT_EVENT_TYPE = "ADMIN_DISCOVERY_ALERT"
MAX_LISTED_IN_EMAIL = 10


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def current_conditions(db: Session, now: datetime) -> dict[str, dict]:
    """Return every alert condition that holds right now, keyed by stable alert key."""
    found: dict[str, dict] = {}
    stale_cutoff = now - timedelta(hours=STALE_AFTER_HOURS)
    latest = (
        select(AcquisitionRun.source_id.label("source_id"), func.max(AcquisitionRun.completed_at).label("last_success_at"))
        .where(AcquisitionRun.status == "SUCCEEDED").group_by(AcquisitionRun.source_id).subquery()
    )
    stale_rows = db.execute(
        select(Source.id, Source.name, latest.c.last_success_at)
        .outerjoin(latest, latest.c.source_id == Source.id)
        .where(
            Source.status == "ACTIVE",
            (latest.c.last_success_at < stale_cutoff)
            | (latest.c.last_success_at.is_(None) & (Source.created_at < stale_cutoff)),
        )
    ).all()
    for source_id, name, last_success in stale_rows:
        found[f"stale-source:{source_id}"] = {
            "kind": "STALE_SOURCE", "subject_id": source_id, "tool_key": None,
            "details": {"source_name": name, "last_success_at": _iso(last_success), "stale_after_hours": STALE_AFTER_HOURS},
        }

    skipped = func.coalesce(DiscoveryMissionSourceRun.error_details["scope_outcome"].as_string() == "SKIPPED", False).label("was_skipped")
    rows = db.execute(
        select(SourceConnector.connector_type, DiscoveryMissionSourceRun.status, skipped, func.count(DiscoveryMissionSourceRun.id))
        .join(SourceConnector, SourceConnector.id == DiscoveryMissionSourceRun.source_connector_id)
        .where(DiscoveryMissionSourceRun.created_at >= now - timedelta(hours=FAILURE_WINDOW_HOURS))
        .group_by(SourceConnector.connector_type, DiscoveryMissionSourceRun.status, skipped)
    ).all()
    totals: dict[str, list[int]] = {}
    for connector_type, step_status, was_skipped, count in rows:
        if was_skipped or step_status not in TERMINAL_STATUSES:
            continue
        pair = totals.setdefault(str(connector_type).strip().upper(), [0, 0])
        pair[0] += int(count)
        if step_status in FAILURE_STATUSES:
            pair[1] += int(count)
    for tool_key, (steps, failures) in totals.items():
        if steps >= FAILURE_MIN_STEPS and failures / steps > FAILURE_RATE_THRESHOLD:
            found[f"tool-failure-rate:{tool_key}"] = {
                "kind": "TOOL_FAILURE_RATE", "subject_id": None, "tool_key": tool_key,
                "details": {"steps": steps, "failures": failures, "window_hours": FAILURE_WINDOW_HOURS,
                            "failure_rate": round(failures / steps, 3)},
            }
    return found


def _describe(alert: DiscoveryAlert) -> str:
    d = alert.details or {}
    if alert.kind == "STALE_SOURCE":
        last = d.get("last_success_at") or "never"
        return f"Source '{d.get('source_name')}' has had no successful acquisition since {last} (limit {d.get('stale_after_hours')} h)."
    return (f"Tool {alert.tool_key}: {d.get('failures')} of {d.get('steps')} steps failed in the last "
            f"{d.get('window_hours')} h ({round(float(d.get('failure_rate', 0)) * 100)}%).")


def notify_administrators(db: Session, opened: list[DiscoveryAlert]) -> int:
    """One digest notification (and email) per active administrator for a batch of new alerts."""
    if not opened:
        return 0
    lines = [_describe(a) for a in opened[:MAX_LISTED_IN_EMAIL]]
    if len(opened) > MAX_LISTED_IN_EMAIL:
        lines.append(f"...and {len(opened) - MAX_LISTED_IN_EMAIL} more. See the discovery alerts list in the Admin console.")
    admin_ids = db.scalars(
        select(User.id).join(AdminGrant, AdminGrant.user_id == User.id)
        .where(AdminGrant.active.is_(True), AdminGrant.role == "ADMIN", User.status == "ACTIVE")
        .order_by(User.id)
    ).all()
    batch = min(opened, key=lambda a: str(a.id)).id
    now = datetime.now(timezone.utc)
    for admin_id in admin_ids:
        _insert_notification(db, {
            "user_id": admin_id,
            "event_key": f"discovery-alerts:{batch}:{admin_id}",
            "event_type": ALERT_EVENT_TYPE,
            "title": f"Discovery alert: {len(opened)} new" if len(opened) > 1 else "Discovery alert",
            "message": "\n".join(lines),
            "resource_type": "DISCOVERY_ALERT",
            "resource_id": batch,
            "created_at": now,
            "read_at": None,
        })
    return len(admin_ids)


def evaluate_discovery_alerts(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Open alerts for new conditions, refresh live ones, resolve cleared ones, then notify."""
    current = now or datetime.now(timezone.utc)
    conditions = current_conditions(db, current)
    unresolved = {
        a.alert_key: a for a in db.scalars(
            select(DiscoveryAlert).where(DiscoveryAlert.status != "RESOLVED").with_for_update()
        ).all()
    }
    opened: list[DiscoveryAlert] = []
    resolved = 0
    for key, cond in conditions.items():
        existing = unresolved.get(key)
        if existing is not None:
            existing.last_seen_at = current
            existing.details = cond["details"]
            continue
        alert = DiscoveryAlert(
            alert_key=key, kind=cond["kind"], subject_id=cond["subject_id"], tool_key=cond["tool_key"],
            status="OPEN", details=cond["details"], opened_at=current, last_seen_at=current,
        )
        db.add(alert)
        opened.append(alert)
    for key, alert in unresolved.items():
        if key not in conditions:
            alert.status = "RESOLVED"
            alert.resolved_at = current
            alert.revision += 1
            resolved += 1
    db.commit()
    notified = notify_administrators(db, opened)
    return {"opened": len(opened), "resolved": resolved, "active": len(conditions), "administrators_notified": notified}
