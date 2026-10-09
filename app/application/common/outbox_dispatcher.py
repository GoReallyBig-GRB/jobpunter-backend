from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.application.jobs.canonicalization import CanonicalizationService
from app.infrastructure.db.models import OutboxEvent
from app.infrastructure.db.repositories.canonicalization import SqlAlchemyCanonicalizationRepository


EventHandler = Callable[[dict], None]


class OutboxDispatcher:
    """Concurrent-safe outbox dispatcher with bounded retries and stale-lease recovery."""

    def __init__(
        self,
        session_factory: sessionmaker,
        handlers: dict[str, EventHandler],
        *,
        lease_seconds: int = 300,
        max_attempts: int = 8,
        base_backoff_seconds: int = 5,
    ):
        if lease_seconds < 1 or max_attempts < 1 or base_backoff_seconds < 1:
            raise ValueError("lease, attempts and backoff must be positive")
        self.session_factory = session_factory
        self.handlers = handlers
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.base_backoff_seconds = base_backoff_seconds

    def claim_batch(self, limit: int = 25) -> list[UUID]:
        if limit < 1:
            raise ValueError("limit must be positive")
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(seconds=self.lease_seconds)
        with self.session_factory() as db:
            statement = (
                select(OutboxEvent)
                .where(
                    or_(
                        (OutboxEvent.status == "PENDING") & (OutboxEvent.available_at <= now),
                        (OutboxEvent.status == "PROCESSING") & (OutboxEvent.locked_at <= stale_before),
                    )
                )
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            events = db.scalars(statement).all()
            ids = []
            for event in events:
                event.status = "PROCESSING"
                event.locked_at = now
                event.attempt_count += 1
                ids.append(event.id)
            db.commit()
            return ids

    def process_event(self, event_id: UUID) -> str:
        with self.session_factory() as db:
            event = db.get(OutboxEvent, event_id)
            if event is None:
                return "MISSING"
            if event.status != "PROCESSING":
                return event.status
            event_type = event.event_type
            payload = dict(event.payload or {})
        handler = self.handlers.get(event_type)
        try:
            if handler is None:
                raise LookupError(f"no_handler_for_event:{event_type}")
            handler(payload)
        except Exception as exc:
            with self.session_factory() as db:
                event = db.get(OutboxEvent, event_id)
                if event is None:
                    return "MISSING"
                event.last_error = f"handler_error:{type(exc).__name__}"
                event.locked_at = None
                if event.attempt_count >= self.max_attempts:
                    event.status = "DEAD"
                else:
                    delay = self.base_backoff_seconds * (2 ** min(event.attempt_count - 1, 10))
                    event.status = "PENDING"
                    event.available_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                db.commit()
                return event.status

        with self.session_factory() as db:
            event = db.get(OutboxEvent, event_id)
            if event is None:
                return "MISSING"
            event.status = "PROCESSED"
            event.processed_at = datetime.now(timezone.utc)
            event.locked_at = None
            event.last_error = None
            db.commit()
            return "PROCESSED"

    def dispatch_batch(self, limit: int = 25) -> dict[str, int]:
        result = {"claimed": 0, "processed": 0, "retrying": 0, "dead": 0, "other": 0}
        ids = self.claim_batch(limit)
        result["claimed"] = len(ids)
        for event_id in ids:
            status = self.process_event(event_id)
            if status == "PROCESSED":
                result["processed"] += 1
            elif status == "PENDING":
                result["retrying"] += 1
            elif status == "DEAD":
                result["dead"] += 1
            else:
                result["other"] += 1
        return result


def canonicalization_event_handler(session_factory: sessionmaker) -> EventHandler:
    """Build an idempotent handler for RAW_SOURCE_RECORD_ACQUIRED outbox events."""
    def handle(payload: dict) -> None:
        raw_id = UUID(payload["raw_source_record_id"])
        with session_factory() as db:
            CanonicalizationService(SqlAlchemyCanonicalizationRepository(db)).process(raw_id)
    return handle
