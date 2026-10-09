from dataclasses import dataclass
from typing import Callable
from uuid import UUID

from app.ports.repositories.outbox import OutboxRepository


OutboxHandler = Callable[[dict], None]


@dataclass(frozen=True)
class DispatchResult:
    claimed: int
    processed: int
    rescheduled: int
    failed: int


class OutboxDispatcher:
    """Deliver due outbox events with bounded retry and lease-based recovery.

    Handlers must be idempotent because a worker can crash after performing a
    side effect but before acknowledging the event.
    """

    def __init__(
        self,
        repository: OutboxRepository,
        handlers: dict[str, OutboxHandler],
        *,
        max_attempts: int = 5,
        base_delay_seconds: int = 5,
        max_delay_seconds: int = 900,
        lease_seconds: int = 120,
    ):
        if max_attempts < 1 or base_delay_seconds < 1 or max_delay_seconds < base_delay_seconds:
            raise ValueError("invalid retry configuration")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self.repository = repository
        self.handlers = handlers
        self.max_attempts = max_attempts
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.lease_seconds = lease_seconds

    def dispatch_once(self, *, limit: int = 10) -> DispatchResult:
        if limit < 1:
            raise ValueError("limit must be positive")

        events = self.repository.claim_due(limit=limit, lease_seconds=self.lease_seconds)
        processed = 0
        rescheduled = 0
        failed = 0

        for event in events:
            handler = self.handlers.get(event.event_type)
            if handler is None:
                error_code = "NO_HANDLER"
            else:
                try:
                    handler(event.payload)
                except Exception as exc:
                    # Persist an error category only; exception text can contain secrets.
                    error_code = type(exc).__name__
                else:
                    # If acknowledgement fails, let the database error propagate. The lease
                    # will expire and the idempotent handler can safely be attempted again.
                    self.repository.mark_processed(event.id)
                    processed += 1
                    continue

            self.repository.record_failure(
                event.id,
                error_code,
                max_attempts=self.max_attempts,
                base_delay_seconds=self.base_delay_seconds,
                max_delay_seconds=self.max_delay_seconds,
            )
            if event.attempt_count >= self.max_attempts:
                failed += 1
            else:
                rescheduled += 1

        return DispatchResult(len(events), processed, rescheduled, failed)
