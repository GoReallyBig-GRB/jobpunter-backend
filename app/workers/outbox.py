"""Run the durable PostgreSQL outbox dispatcher as a worker process."""

from __future__ import annotations

import argparse
import logging
import signal
import time
from collections.abc import Callable
from app.application.operations.worker_heartbeat import WorkerHeartbeatCallback, build_worker_heartbeat

from app.application.common.outbox_dispatcher import (
    OutboxDispatcher,
    canonicalization_event_handler,
)
from app.infrastructure.db.session import SessionLocal
from app.application.support.notifications import support_ticket_notification_handler
from app.application.notifications.service import (
    application_status_notification_handler,
    discovery_completion_notification_handler,
    evaluation_attention_notification_handler,
    punt_notification_handler,
    notification_email_delivery_handler,
    notification_email_retry_policy_provider,
)

logger = logging.getLogger("jobpunter.outbox_worker")


def build_dispatcher() -> OutboxDispatcher:
    """Build the production dispatcher and its idempotent canonicalization handler."""
    return OutboxDispatcher(
        SessionLocal,
        {
            "RAW_SOURCE_RECORD_ACQUIRED": canonicalization_event_handler(SessionLocal),
            "RAW_SOURCE_RECORD_OBSERVED": canonicalization_event_handler(SessionLocal),
            "PUNT_QUALIFIED": punt_notification_handler(SessionLocal),
            "APPLICATION_STATUS_CHANGED": application_status_notification_handler(SessionLocal),
            "EVALUATION_REQUIRES_ATTENTION": evaluation_attention_notification_handler(SessionLocal),
            "DISCOVERY_MISSION_COMPLETED": discovery_completion_notification_handler(SessionLocal),
            "NOTIFICATION_EMAIL_REQUESTED": notification_email_delivery_handler(SessionLocal),
            "SUPPORT_TICKET_CREATED": support_ticket_notification_handler(SessionLocal),
            "SUPPORT_TICKET_STATUS_CHANGED": support_ticket_notification_handler(SessionLocal),
        },
        # Discovery missions are durably polled by DiscoveryMissionWorker. Their
        # outbox event is acknowledged without executing or mutating the WorkItem here.
        delegated_work_types={"DISCOVERY_MISSION"},
        retry_policy_provider=notification_email_retry_policy_provider(SessionLocal),
    )


def run_worker(
    dispatcher: OutboxDispatcher,
    *,
    batch_size: int = 25,
    interval_seconds: float = 2.0,
    once: bool = False,
    should_stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
    heartbeat: WorkerHeartbeatCallback | None = None,
) -> int:
    """Dispatch events until stopped, or perform one batch when ``once`` is true."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    batches = 0
    if heartbeat:
        heartbeat("RUNNING", {"batch_size": batch_size})
    try:
        while not should_stop():
            result = dispatcher.dispatch_batch(limit=batch_size)
            batches += 1
            logger.info("outbox batch %s", result)
            if heartbeat:
                heartbeat(
                    "IDLE" if result["claimed"] == 0 else "RUNNING",
                    result,
                    successful_cycle=True,
                )
            if once:
                break
            if result["claimed"] == 0:
                sleep(interval_seconds)
    except Exception as exc:
        if heartbeat:
            heartbeat("FAILED", {"batches": batches}, type(exc).__name__)
        raise
    if heartbeat:
        heartbeat("STOPPED", {"batches": batches})
    return batches


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="process one batch and exit")
    parser.add_argument("--batch-size", type=int, default=25)
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
        build_dispatcher(),
        batch_size=args.batch_size,
        interval_seconds=args.interval_seconds,
        once=args.once,
        should_stop=lambda: stopped,
        heartbeat=build_worker_heartbeat("outbox"),
    )


if __name__ == "__main__":
    main()
