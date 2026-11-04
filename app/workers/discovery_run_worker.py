"""Lease-aware worker for durable administrator discovery runs."""
from __future__ import annotations

import argparse
import logging
import signal
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select

from app.application.jobs.discovery_run_execution import execute_discovery_run
from app.application.operations.worker_heartbeat import (
    WorkerHeartbeatCallback,
    build_worker_heartbeat,
)
from app.infrastructure.db.models.discovery_orchestration import DiscoveryRun
from app.infrastructure.db.models.work_item import WorkItem
from app.infrastructure.db.session import SessionLocal

logger = logging.getLogger("jobpunter.discovery_run_worker")


class DiscoveryRunWorker:
    def __init__(self, session_factory=SessionLocal, *, lease_seconds: int = 300):
        if lease_seconds < 30:
            raise ValueError("lease_seconds must be at least 30")
        self.session_factory = session_factory
        self.lease_seconds = lease_seconds

    def claim_batch(self, limit: int = 10) -> list[tuple[UUID, int]]:
        if limit < 1:
            raise ValueError("limit must be positive")
        now = datetime.now(UTC)
        with self.session_factory() as db:
            items = db.scalars(
                select(WorkItem)
                .join(DiscoveryRun, WorkItem.resource_id == DiscoveryRun.id)
                .where(
                    WorkItem.work_type == "DISCOVERY_RUN",
                    DiscoveryRun.status.in_(("PENDING", "RUNNING")),
                    or_(DiscoveryRun.next_attempt_at.is_(None), DiscoveryRun.next_attempt_at <= now),
                    or_(
                        WorkItem.status == "PENDING",
                        and_(
                            WorkItem.status == "PROCESSING",
                            or_(DiscoveryRun.lease_expires_at.is_(None), DiscoveryRun.lease_expires_at <= now),
                        ),
                    ),
                )
                .order_by(WorkItem.created_at, WorkItem.id)
                .limit(limit).with_for_update(skip_locked=True, of=WorkItem)
            ).all()
            claimed: list[tuple[UUID, int]] = []
            for item in items:
                run = db.get(DiscoveryRun, item.resource_id)
                if run is None:
                    continue
                item.status = "PROCESSING"
                item.started_at = now
                item.attempt_count += 1
                item.updated_at = now
                item.revision += 1
                run.status = "RUNNING"
                configured_duration = (
                    run.configuration_snapshot.get("strategy", {})
                    .get("global_limits", {})
                    .get("max_duration_seconds")
                )
                bounded_duration = (
                    int(configured_duration)
                    if isinstance(configured_duration, int) and configured_duration > 0
                    else 0
                )
                run.lease_expires_at = now + timedelta(
                    seconds=max(self.lease_seconds, bounded_duration + 60)
                )
                run.revision += 1
                claimed.append((run.id, item.attempt_count))
            db.commit()
            return claimed

    def _finish_work_item(self, run_id: UUID, status: str) -> None:
        with self.session_factory() as db:
            item = db.scalar(select(WorkItem).where(
                WorkItem.work_type == "DISCOVERY_RUN", WorkItem.resource_id == run_id,
            ).with_for_update())
            if item is None:
                return
            if item.status == "CANCELLED" or status == "CANCELLED":
                item.status = "CANCELLED"
            else:
                item.status = (
                    "COMPLETED" if status in {"SUCCEEDED", "PARTIAL", "BLOCKED"} else "FAILED"
                )
            item.completed_at = datetime.now(UTC)
            item.result = {"discovery_run_status": status}
            item.updated_at = datetime.now(UTC)
            item.revision += 1
            db.commit()

    def _record_failure(self, run_id: UUID, attempt_number: int, exc: Exception) -> str:
        with self.session_factory() as db:
            run = db.scalar(select(DiscoveryRun).where(DiscoveryRun.id == run_id).with_for_update())
            item = db.scalar(select(WorkItem).where(
                WorkItem.work_type == "DISCOVERY_RUN", WorkItem.resource_id == run_id,
            ).with_for_update())
            if run is None or item is None:
                return "FAILED"
            if run.status == "CANCELLED":
                return "CANCELLED"
            policy = run.configuration_snapshot.get("schedule", {}).get("retry_policy", {})
            max_attempts = int(policy.get("max_attempts", 1))
            initial = int(policy.get("initial_backoff_seconds", 5))
            maximum = int(policy.get("maximum_backoff_seconds", 300))
            now = datetime.now(UTC)
            retry = attempt_number < max_attempts
            backoff = min(initial * (2 ** max(attempt_number - 1, 0)), maximum)
            run.error_details = {"code": type(exc).__name__, "retryable": retry}
            run.retry_information = {
                "attempt": attempt_number, "max_attempts": max_attempts,
                "next_backoff_seconds": backoff if retry else None,
            }
            run.lease_expires_at = None
            run.revision += 1
            if retry:
                run.status = "PENDING"
                run.next_attempt_at = now + timedelta(seconds=backoff)
                item.status = "PENDING"
                item.started_at = None
                item.error_code = type(exc).__name__
                item.error_details = {"retryable": True}
            else:
                run.status = "FAILED"
                run.completed_at = now
                item.status = "FAILED"
                item.completed_at = now
                item.error_code = type(exc).__name__
                item.error_details = {"retryable": False}
            item.updated_at = now
            item.revision += 1
            db.commit()
            return "RETRIED" if retry else "FAILED"

    def run_once(self, limit: int = 10) -> dict[str, int]:
        claimed = self.claim_batch(limit)
        result = {
            "claimed": len(claimed), "completed": 0, "retried": 0,
            "failed": 0, "cancelled": 0,
        }
        for run_id, attempt_number in claimed:
            try:
                with self.session_factory() as db:
                    status = execute_discovery_run(db, run_id, attempt_number=attempt_number)
                self._finish_work_item(run_id, status)
                result["completed"] += 1
            except Exception as exc:  # noqa: BLE001 - isolate one durable run and apply retry policy
                logger.error("discovery run failed run=%s error_type=%s", run_id, type(exc).__name__)
                failure_outcome = self._record_failure(run_id, attempt_number, exc)
                if failure_outcome == "RETRIED":
                    result["retried"] += 1
                elif failure_outcome == "CANCELLED":
                    result["cancelled"] += 1
                else:
                    result["failed"] += 1
        return result


def run_worker(
    *, interval_seconds: float = 2.0, batch_size: int = 10, once: bool = False,
    should_stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
    heartbeat: WorkerHeartbeatCallback | None = None,
    worker: DiscoveryRunWorker | None = None,
) -> int:
    if interval_seconds <= 0 or batch_size < 1:
        raise ValueError("interval_seconds and batch_size must be positive")
    consumer = worker or DiscoveryRunWorker()
    cycles = 0
    if heartbeat:
        heartbeat("RUNNING", {"batch_size": batch_size})
    try:
        while not should_stop():
            outcome = consumer.run_once(batch_size)
            cycles += 1
            if heartbeat:
                heartbeat(
                    "IDLE" if outcome["claimed"] == 0 else "RUNNING",
                    {**outcome, "cycles": cycles, "batch_size": batch_size},
                    successful_cycle=True,
                )
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
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
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
        should_stop=lambda: stopped, heartbeat=build_worker_heartbeat("discovery_run_worker"),
    )


if __name__ == "__main__":
    main()
