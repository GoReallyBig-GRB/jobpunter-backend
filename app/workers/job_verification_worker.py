"""Periodically verify discovered job application URLs with durable audit records."""
from __future__ import annotations

import argparse
import logging
import signal
import time
from datetime import datetime, timedelta, timezone
from typing import Callable
from app.application.operations.worker_heartbeat import WorkerHeartbeatCallback, build_worker_heartbeat

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.application.jobs.job_verification import (
    VerificationResult,
    VERIFICATION_FRESHNESS_WINDOW,
    mark_stale_jobs,
    record_job_verification,
)
from app.infrastructure.db.models import Job, JobVerificationAttempt
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.verification.http_application_verifier import PublicWebApplicationVerifier

logger = logging.getLogger("jobpunter.job_verification_worker")
REVERIFY_AFTER = VERIFICATION_FRESHNESS_WINDOW
RETRY_UNKNOWN_AFTER = timedelta(hours=6)
TERMINAL_JOB_STATES = {"CLOSED", "EXPIRED", "REMOVED"}


class JobVerificationWorker:
    """Check one job per transaction while holding its row lock during the request."""

    def __init__(
        self,
        session_factory=SessionLocal,
        verifier: Callable[[str], VerificationResult] | None = None,
    ):
        self.session_factory = session_factory
        self.verifier = verifier or PublicWebApplicationVerifier()

    def _next_job(self, db: Session, now: datetime) -> Job | None:
        recent_attempt = select(JobVerificationAttempt.id).where(
            JobVerificationAttempt.job_id == Job.id,
            JobVerificationAttempt.checked_at >= now - RETRY_UNKNOWN_AFTER,
        ).exists()
        stale_verification = or_(
            Job.last_verified_at.is_(None),
            Job.last_verified_at < now - REVERIFY_AFTER,
        )
        return db.scalar(
            select(Job)
            .where(
                Job.identity_status == "CANONICAL",
                Job.application_url.is_not(None),
                Job.application_url != "",
                Job.lifecycle_status.notin_(TERMINAL_JOB_STATES),
                stale_verification,
                ~recent_attempt,
            )
            .order_by(Job.last_verified_at.asc().nulls_first(), Job.created_at, Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    def run_once(self, limit: int = 10) -> dict[str, int]:
        if limit < 1:
            raise ValueError("limit must be positive")
        outcome = {"checked": 0, "active": 0, "closed": 0, "unknown": 0, "failed": 0, "stale": 0}
        with self.session_factory() as db:
            try:
                outcome["stale"] = mark_stale_jobs(db)
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.error("staleness sweep failed error_type=%s", type(exc).__name__)
                outcome["failed"] += 1
        for _ in range(limit):
            with self.session_factory() as db:
                now = datetime.now(timezone.utc)
                job = self._next_job(db, now)
                if job is None:
                    db.rollback()
                    break
                job_id = job.id
                url = job.application_url
                try:
                    result = self.verifier(url)
                except ValueError:
                    result = VerificationResult(
                        status="UNKNOWN",
                        checked_url=url,
                        final_url=None,
                        http_status_code=None,
                        reason_code="URL_REJECTED_BY_SAFETY_POLICY",
                        evidence={"error_type": "ValueError"},
                    )
                except Exception as exc:
                    logger.warning(
                        "job verification failed job=%s error=%s",
                        job_id, type(exc).__name__,
                    )
                    result = VerificationResult(
                        status="UNKNOWN",
                        checked_url=url,
                        final_url=None,
                        http_status_code=None,
                        reason_code="VERIFIER_UNEXPECTED_ERROR",
                        evidence={"error_type": type(exc).__name__},
                    )
                try:
                    record_job_verification(db, job_id, result, now=now, commit=False)
                    db.commit()
                    outcome["checked"] += 1
                    key = result.status.lower()
                    if key in {"active", "closed", "unknown"}:
                        outcome[key] += 1
                except Exception as exc:
                    db.rollback()
                    # Database exception text/tracebacks can include bound values or private content.
                    logger.error(
                        "could not persist verification attempt job=%s error_type=%s",
                        job_id, type(exc).__name__,
                    )
                    outcome["failed"] += 1
        return outcome


def run_worker(
    *,
    interval_seconds: float = 30.0,
    batch_size: int = 10,
    once: bool = False,
    should_stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
    heartbeat: WorkerHeartbeatCallback | None = None,
    worker: JobVerificationWorker | None = None,
) -> int:
    if interval_seconds <= 0 or batch_size < 1:
        raise ValueError("interval_seconds and batch_size must be positive")
    consumer = worker or JobVerificationWorker()
    cycles = 0
    if heartbeat:
        heartbeat("RUNNING", {"batch_size": batch_size})
    try:
        while not should_stop():
            outcome = consumer.run_once(batch_size)
            cycles += 1
            logger.info("job verification cycle=%s outcome=%s", cycles, outcome)
            if heartbeat:
                heartbeat(
                    "IDLE" if outcome["checked"] == 0 else "RUNNING",
                    {**outcome, "cycles": cycles, "batch_size": batch_size},
                    successful_cycle=True,
                )
            if once:
                break
            if outcome["checked"] == 0:
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
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stopped = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    run_worker(
        interval_seconds=args.interval_seconds,
        batch_size=args.batch_size,
        once=args.once,
        should_stop=lambda: stopped,
        heartbeat=build_worker_heartbeat("job_verification_worker"),
    )


if __name__ == "__main__":
    main()
