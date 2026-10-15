"""Run the durable PostgreSQL outbox dispatcher as a worker process."""

from __future__ import annotations

import argparse
import logging
import signal
import time
from collections.abc import Callable

from app.application.common.outbox_dispatcher import (
    OutboxDispatcher,
    canonicalization_event_handler,
)
from app.infrastructure.db.session import SessionLocal

logger = logging.getLogger("jobpunter.outbox_worker")


def build_dispatcher() -> OutboxDispatcher:
    """Build the production dispatcher and its idempotent canonicalization handler."""
    return OutboxDispatcher(
        SessionLocal,
        {
            "RAW_SOURCE_RECORD_ACQUIRED": canonicalization_event_handler(SessionLocal),
            "RAW_SOURCE_RECORD_OBSERVED": canonicalization_event_handler(SessionLocal),
        },
    )


def run_worker(
    dispatcher: OutboxDispatcher,
    *,
    batch_size: int = 25,
    interval_seconds: float = 2.0,
    once: bool = False,
    should_stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Dispatch events until stopped, or perform one batch when ``once`` is true."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    batches = 0
    while not should_stop():
        result = dispatcher.dispatch_batch(limit=batch_size)
        batches += 1
        logger.info("outbox batch %s", result)
        if once:
            break
        if result["claimed"] == 0:
            sleep(interval_seconds)
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
    )


if __name__ == "__main__":
    main()
