"""Execute claimed SOURCE_REFRESH work items (scheduled, candidate-independent source refresh)."""
from __future__ import annotations

import argparse
import logging
import signal
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select

from app.application.jobs.source_refresh import WORK_TYPE, process_source_refresh
from app.application.operations.worker_heartbeat import WorkerHeartbeatCallback, build_worker_heartbeat
from app.infrastructure.db.models import WorkItem
from app.infrastructure.db.session import SessionLocal

logger = logging.getLogger("jobpunter.source_refresh_worker")
MAX_ATTEMPTS = 3


class SourceRefreshWorker:
    """Row-locking consumer with stale-claim recovery and bounded retries."""

    def __init__(self, session_factory=SessionLocal, *, lease_seconds: int = 900):
        if lease_seconds < 30:
            raise ValueError("lease_seconds must be at least 30")
        self.session_factory = session_factory
        self.lease_seconds = lease_seconds

    def claim_batch(self, limit: int = 5) -> list[UUID]:
        if limit < 1:
            raise ValueError("limit must be positive")
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(seconds=self.lease_seconds)
        with self.session_factory() as db:
            items = db.scalars(
                select(WorkItem).where(
                    WorkItem.work_type == WORK_TYPE,
                    or_(
                        WorkItem.status == "PENDING",
                        and_(WorkItem.status == "PROCESSING",
                             or_(WorkItem.started_at.is_(None), WorkItem.started_at <= stale_before)),
                    ),
                ).order_by(WorkItem.created_at, WorkItem.id).limit(limit).with_for_update(skip_locked=True)
            ).all()
            for item in items:
                item.status = "PROCESSING"
                item.started_at = now
                item.attempt_count += 1
                item.updated_at = now
            ids = [item.id for item in items]
            db.commit()
            return ids

    def run_once(self, limit: int = 5) -> dict[str, int]:
        ids = self.claim_batch(limit)
        result = {"claimed": len(ids), "completed": 0, "failed": 0}
        for item_id in ids:
            try:
                with self.session_factory() as db:
                    process_source_refresh(db, item_id)
                result["completed"] += 1
            except Exception as exc:
                logger.error("source refresh failed item=%s error_type=%s", item_id, type(exc).__name__)
                with self.session_factory() as db:
                    item = db.get(WorkItem, item_id)
                    if item is not None:
                        exhausted = item.attempt_count >= MAX_ATTEMPTS
                        item.status = "FAILED" if exhausted else "PENDING"
                        item.error_code = type(exc).__name__
                        item.started_at = None
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
