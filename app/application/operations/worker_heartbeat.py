"""Safe persistence helpers for worker liveness and bounded cycle metrics."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.db.models.worker_heartbeat import WorkerHeartbeat

logger = logging.getLogger("jobpunter.worker_heartbeat")
WorkerHeartbeatCallback = Callable[[str, dict[str, Any] | None, str | None, bool], None]
_ALLOWED_STATUSES = frozenset({"RUNNING", "IDLE", "STOPPED", "FAILED"})
_SAFE_METRIC_KEY = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_SENSITIVE_METRIC_PARTS = ("payload", "message", "email", "token", "secret", "candidate", "notification")


def _safe_metrics(value: dict[str, Any] | None) -> dict[str, int | float | bool | str]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, int | float | bool | str] = {}
    for raw_key, item in value.items():
        if not isinstance(raw_key, str):
            continue
        key = raw_key.strip().lower()
        if not _SAFE_METRIC_KEY.fullmatch(key) or any(part in key for part in _SENSITIVE_METRIC_PARTS):
            continue
        if isinstance(item, bool):
            safe[key] = item
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            safe[key] = item
        elif isinstance(item, str) and len(item) <= 64 and not any(
            part in key for part in _SENSITIVE_METRIC_PARTS
        ):
            safe[key] = item
    return safe


def record_worker_heartbeat(
    session_factory: Callable[[], Session],
    *,
    worker_name: str,
    instance_id: UUID,
    status: str,
    metrics: dict[str, Any] | None = None,
    error_category: str | None = None,
    successful_cycle: bool = False,
) -> None:
    """Upsert one process heartbeat; raw payloads and exception messages are never stored."""
    name = worker_name.strip().lower()
    state = status.strip().upper()
    if not name or len(name) > 64 or not _SAFE_METRIC_KEY.fullmatch(name):
        raise ValueError("worker_name is invalid")
    if state not in _ALLOWED_STATUSES:
        raise ValueError("worker status is invalid")
    now = datetime.now(timezone.utc)
    safe_error = error_category[:96] if isinstance(error_category, str) else None
    with session_factory() as db:
        row = db.scalar(
            select(WorkerHeartbeat)
            .where(
                WorkerHeartbeat.worker_name == name,
                WorkerHeartbeat.instance_id == instance_id,
            )
            .with_for_update()
        )
        if row is None:
            row = WorkerHeartbeat(
                worker_name=name,
                instance_id=instance_id,
                status=state,
                started_at=now,
                last_seen_at=now,
                last_success_at=now if successful_cycle else None,
                last_error_category=safe_error,
                metrics=_safe_metrics(metrics),
            )
            db.add(row)
        else:
            row.status = state
            row.last_seen_at = now
            row.last_error_category = safe_error
            if metrics is not None:
                row.metrics = _safe_metrics(metrics)
            if successful_cycle:
                row.last_success_at = now
        db.commit()


def build_worker_heartbeat(worker_name: str) -> WorkerHeartbeatCallback:
    """Create a process-scoped callback. Heartbeat failure is logged, not allowed to mask work."""
    from app.infrastructure.db.session import SessionLocal

    instance_id = uuid4()

    def emit(
        status: str,
        metrics: dict[str, Any] | None = None,
        error_category: str | None = None,
        successful_cycle: bool = False,
    ) -> None:
        try:
            record_worker_heartbeat(
                SessionLocal,
                worker_name=worker_name,
                instance_id=instance_id,
                status=status,
                metrics=metrics,
                error_category=error_category,
                successful_cycle=successful_cycle,
            )
        except Exception:
            logger.exception("worker heartbeat persistence failed worker=%s", worker_name)

    return emit
