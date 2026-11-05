"""Execute claimed SOURCE_REFRESH work items (scheduled, candidate-independent source refresh)."""
from __future__ import annotations

import argparse
import logging
import signal
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select

from app.config.settings import get_settings

from app.application.jobs.source_refresh import WORK_TYPE, process_source_refresh
from app.application.operations.worker_heartbeat import WorkerHeartbeatCallback, build_worker_heartbeat
from app.infrastructure.db.models import WorkItem
from app.infrastructure.db.session import SessionLocal

logger = logging.getLogger("jobpunter.source_refresh_worker")
MAX_ATTEMPTS = 3
SOURCE_REFRESH_CLAIM_LOCK = 7340211


class SourceRefreshWorker:
    """Row-locking consumer with stale-claim recovery and bounded retries."""

    def __init__(self, session_factory=SessionLocal, *, lease_seconds: int = 900, max_concurrent: int | None = None):
        if lease_seconds < 30:
            raise ValueError("lease_seconds must be at least 30")
        if max_concurrent is not None and max_concurrent < 1:
            raise ValueError("max_concurrent must be positive")
        self.session_factory = session_factory
        self.lease_seconds = lease_seconds
        self._max_concurrent = max_concurrent
        self._claim_tokens: dict[UUID, UUID] = {}

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent or get_settings().source_refresh_max_concurrent

    def claim_batch(self, limit: int = 5) -> list[UUID]:
        if limit < 1:
            raise ValueError("limit must be positive")
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(seconds=self.lease_seconds)
        self._claim_tokens = {}
        with self.session_factory() as db:
            # Serialize claims across worker processes so the ceiling cannot be overshot by a race.
            db.execute(select(func.pg_advisory_xact_lock(SOURCE_REFRESH_CLAIM_LOCK)))
            # Global ceiling: live (non-stale) claims across all worker processes count against it.
            in_progress = int(db.scalar(select(func.count(WorkItem.id)).where(
                WorkItem.work_type == WORK_TYPE, WorkItem.status == "PROCESSING",
                WorkItem.started_at > stale_before,
            )) or 0)
            limit = min(limit, self.max_concurrent - in_progress)
            if limit < 1:
                return []
            items = db.scalars(
                select(WorkItem).where(
                    WorkItem.work_type == WORK_TYPE,
                    or_(
                        and_(
                            WorkItem.status == "PENDING",
                            func.coalesce(
                                WorkItem.result["not_before"].as_string(), ""
                            ) <= now.isoformat(),
                        ),
                        and_(WorkItem.status == "PROCESSING",
                             or_(WorkItem.started_at.is_(None), WorkItem.started_at <= stale_before)),
                    ),
                ).order_by(WorkItem.created_at, WorkItem.id).limit(limit).with_for_update(skip_locked=True)
            ).all()
            for item in items:
                was_waiting = (
                    isinstance(item.result, dict)
                    and item.result.get("outcome") == "WAITING_FOR_ACQUISITION"
                )
                # Waiting for an existing acquisition is not a failed attempt.
                # Expired running claims still consume the real retry budget.
                if item.attempt_count >= MAX_ATTEMPTS and not was_waiting:
                    item.status = "FAILED"
                    item.started_at = None
                    item.completed_at = now
                    item.error_code = "WORKER_LEASE_RETRIES_EXHAUSTED"
                    item.error_details = {"retryable": False, "reason": "STALE_CLAIM"}
                    item.updated_at = now
                    item.revision += 1
                    item.execution_id = None
                    continue
                item.status = "PROCESSING"
                item.started_at = now
                if not was_waiting:
                    item.attempt_count += 1
                item.result = None
                item.execution_id = uuid4()
                self._claim_tokens[item.id] = item.execution_id
                item.updated_at = now
            ids = [item.id for item in items]
            db.commit()
            return ids

    def run_once(self, limit: int = 5) -> dict[str, int]:
        ids = self.claim_batch(limit)
        result = {"claimed": len(ids), "completed": 0, "failed": 0}
        for item_id in ids:
            claim_token = self._claim_tokens[item_id]
            try:
                with self.session_factory() as db:
                    outcome = process_source_refresh(db, item_id, execution_id=claim_token)
                if outcome in {"DEFERRED", "STALE_CLAIM"}:
                    result["deferred"] = result.get("deferred", 0) + 1
                else:
                    result["completed"] += 1
            except Exception as exc:
                logger.error("source refresh failed item=%s error_type=%s", item_id, type(exc).__name__)
                with self.session_factory() as db:
                    item = db.scalar(
                        select(WorkItem).where(WorkItem.id == item_id)
                        .with_for_update().execution_options(populate_existing=True)
                    )
                    if item is not None and item.status == "PROCESSING" and item.execution_id == claim_token:
                        exhausted = item.attempt_count >= MAX_ATTEMPTS
                        item.status = "FAILED" if exhausted else "PENDING"
                        item.error_code = type(exc).__name__
                        item.started_at = None
                        item.execution_id = None
                        if exhausted:
                            item.completed_at = datetime.now(timezone.utc)
                        db.commit()
                result["failed"] += 1
        return result


def run_worker(
    *, interval_seconds: float = 5.0, batch_size: int = 5, once: bool = False,
    should_stop: Callable[[], bool] = lambda: False, sleep: Callable[[float], None] = time.sleep,
    heartbeat: WorkerHeartbeatCallback | None = None, worker: SourceRefreshWorker | None = None,
) -> int:
    if interval_seconds <= 0 or batch_size < 1:
        raise ValueError("interval_seconds and batch_size must be positive")
    consumer = worker or SourceRefreshWorker()
    cycles = 0
    if heartbeat:
        heartbeat("RUNNING", {"batch_size": batch_size})
    try:
        while not should_stop():
            outcome = consumer.run_once(batch_size)
            cycles += 1
            if heartbeat:
                heartbeat("IDLE" if outcome["claimed"] == 0 else "RUNNING",
                          {**outcome, "cycles": cycles}, successful_cycle=True)
            if once:
                break
            if outcome["claimed"] == 0:
                sleep(interval_seconds)
    except Exception as exc:
        if heartbeat:
            heartbeat("FAILED", {"cycles": cycles}, type(exc).__name__)
        raise
    if heartbeat:
        heartbeat("STOPPED", {"cycles": cycles})
    return cycles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stopped = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    run_worker(
        interval_seconds=args.interval_seconds, batch_size=args.batch_size, once=args.once,
        should_stop=lambda: stopped, heartbeat=build_worker_heartbeat("source_refresh_worker"),
    )


if __name__ == "__main__":
    main()
