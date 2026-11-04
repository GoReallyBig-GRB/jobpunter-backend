"""Run the PostgreSQL-backed discovery schedule poller outside the API process."""
from __future__ import annotations

import argparse
import logging
import signal
import time
from collections.abc import Callable

from app.application.jobs.discovery_alerts import evaluate_discovery_alerts
from app.application.jobs.discovery_missions import schedule_due_missions
from app.application.jobs.discovery_schedules import schedule_due_discovery_runs
from app.application.jobs.source_refresh import enqueue_due_source_refreshes
from app.application.operations.worker_heartbeat import (
    WorkerHeartbeatCallback,
    build_worker_heartbeat,
)
from app.infrastructure.db.session import SessionLocal

logger = logging.getLogger("jobpunter.discovery_scheduler")


def run_scheduler(
    *,
    interval_seconds: float = 15.0,
    batch_size: int = 25,
    once: bool = False,
    should_stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
    heartbeat: WorkerHeartbeatCallback | None = None,
    alert_interval_seconds: float = 300.0,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    cycles = 0
    last_alert_check: float | None = None
    if heartbeat:
        heartbeat("RUNNING", {"batch_size": batch_size})
    try:
        while not should_stop():
            with SessionLocal() as db:
                mission_ids = schedule_due_missions(db, limit=batch_size)
            with SessionLocal() as db:
                admin_run_ids = schedule_due_discovery_runs(db, limit=batch_size)
            with SessionLocal() as db:
                refresh_ids = enqueue_due_source_refreshes(db, limit=batch_size)
            alerts_opened = 0
            if last_alert_check is None or clock() - last_alert_check >= alert_interval_seconds:
                # Alert evaluation must never stop scheduling; a failure is logged by type only.
                try:
                    with SessionLocal() as db:
                        alerts_opened = evaluate_discovery_alerts(db)["opened"]
                except Exception as exc:
                    logger.error("discovery alert evaluation failed error_type=%s", type(exc).__name__)
                last_alert_check = clock()
            cycles += 1
            total = len(mission_ids) + len(admin_run_ids) + len(refresh_ids)
            outcome = {
                "cycles": cycles, "enqueued": total,
                "candidate_missions": len(mission_ids),
                "admin_discovery_runs": len(admin_run_ids),
                "source_refreshes_enqueued": len(refresh_ids), "alerts_opened": alerts_opened,
                "batch_size": batch_size,
            }
            logger.info(
                "discovery schedule cycle=%s candidate_missions=%s admin_runs=%s refreshes=%s",
                cycles, len(mission_ids), len(admin_run_ids), len(refresh_ids),
            )
            if heartbeat:
                heartbeat("RUNNING" if total else "IDLE", outcome, successful_cycle=True)
            if once:
                break
            if not total:
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
    parser.add_argument("--once", action="store_true", help="schedule one batch and exit")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stopped = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    run_scheduler(
        interval_seconds=args.interval_seconds,
        batch_size=args.batch_size,
        once=args.once,
        should_stop=lambda: stopped,
        heartbeat=build_worker_heartbeat("discovery_scheduler"),
    )


if __name__ == "__main__":
    main()
