"""Run the PostgreSQL-backed discovery schedule poller outside the API process."""
from __future__ import annotations

import argparse
import logging
import signal
import time
from collections.abc import Callable

from app.application.jobs.discovery_missions import schedule_due_missions
from app.application.jobs.discovery_schedules import schedule_due_discovery_runs
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
) -> int:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    cycles = 0
    if heartbeat:
        heartbeat("RUNNING", {"batch_size": batch_size})
    try:
        while not should_stop():
            with SessionLocal() as db:
                mission_ids = schedule_due_missions(db, limit=batch_size)
            # Global/source discovery schedules are deliberately independent from
            # candidate settings and use their own transaction and run history.
            with SessionLocal() as db:
                run_ids = schedule_due_discovery_runs(db, limit=batch_size)
            cycles += 1
            total = len(mission_ids) + len(run_ids)
            outcome = {
                "cycles": cycles, "enqueued": total,
                "candidate_missions": len(mission_ids), "admin_discovery_runs": len(run_ids),
                "batch_size": batch_size,
            }
            logger.info(
                "discovery schedule cycle=%s candidate_missions=%s admin_runs=%s",
                cycles, len(mission_ids), len(run_ids),
            )
            if heartbeat:
                heartbeat("IDLE" if not total else "RUNNING", outcome, successful_cycle=True)
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
