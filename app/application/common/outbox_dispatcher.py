from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.application.jobs.canonicalization import CanonicalizationService
from app.infrastructure.db.models import OutboxEvent, WorkItem
from app.infrastructure.db.repositories.canonicalization import SqlAlchemyCanonicalizationRepository


EventHandler = Callable[[dict], None]


class OutboxDispatcher:
    """Concurrent-safe outbox dispatcher with bounded retries and stale-lease recovery."""

    def __init__(
        self,
        session_factory: sessionmaker,
        handlers: dict[str, EventHandler],
        *,
        work_handlers: dict[str, EventHandler] | None = None,
        lease_seconds: int = 300,
        max_attempts: int = 8,
        base_backoff_seconds: int = 5,
    ):
        if lease_seconds < 1 or max_attempts < 1 or base_backoff_seconds < 1:
            raise ValueError("lease, attempts and backoff must be positive")
        self.session_factory = session_factory
        self.handlers = handlers
        # Generic work is opt-in by persisted work_type. Discovery missions remain owned
        # by their dedicated worker and must not be registered here as well.
        self.work_handlers = work_handlers or {}
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
                        (OutboxEvent.status == "PROCESSING") & or_(OutboxEvent.locked_at.is_(None), OutboxEvent.locked_at <= stale_before),
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
            payload = dict(event.payload) if isinstance(event.payload, dict) else {}
            handler: EventHandler | None

            if event_type == "WORK_REQUESTED":
                if event.aggregate_type != "WorkItem" or event.aggregate_id is None:
                    event.status = "DEAD"
                    event.last_error = "invalid_work_item_reference"
                    event.locked_at = None
                    db.commit()
                    return "DEAD"

                item = db.get(WorkItem, event.aggregate_id)
                if item is None:
                    event.status = "DEAD"
                    event.last_error = "work_item_not_found"
                    event.locked_at = None
                    db.commit()
                    return "DEAD"

                handler = self.work_handlers.get(item.work_type)
                if handler is None:
                    # Missing handlers are a deterministic configuration defect, not a
                    # transient failure. Fail visibly now instead of retrying eight times.
                    now = datetime.now(timezone.utc)
                    event.status = "DEAD"
                    event.last_error = f"unsupported_work_type:{item.work_type[:64]}"
                    event.locked_at = None
                    item.status = "FAILED"
                    item.completed_at = now
                    item.updated_at = now
                    item.error_code = "UNSUPPORTED_WORK_TYPE"
                    item.error_details = {
                        "category": "NO_REGISTERED_HANDLER",
                        "work_type": item.work_type,
                    }
                    db.commit()
                    return "DEAD"

                if item.status == "SUCCEEDED":
                    # At-least-once delivery of an already completed request is a no-op.
                    event.status = "PROCESSED"
                    event.last_error = None
                    event.locked_at = None
                    db.commit()
                    return "PROCESSED"

                now = datetime.now(timezone.utc)
                item.status = "RUNNING"
                item.attempt_count += 1
                item.started_at = now
                item.completed_at = None
                item.updated_at = now
                # The persisted WorkItem is authoritative; never trust these fields from
                # the event payload when selecting or describing the requested operation.
                payload.update({
                    "work_item_id": str(item.id),
                    "work_type": item.work_type,
                    "resource_type": item.resource_type,
                    "resource_id": str(item.resource_id) if item.resource_id else None,
                    "execution_id": str(item.execution_id) if item.execution_id else None,
                    "idempotency_key": item.idempotency_key,
                })
                db.commit()
            else:
                handler = self.handlers.get(event_type)
                if handler is None:
                    event.status = "DEAD"
                    event.last_error = f"unsupported_event_type:{event_type[:64]}"
                    event.locked_at = None
                    db.commit()
                    return "DEAD"

        try:
            handler(payload)
        except Exception as exc:
            with self.session_factory() as db:
                event = db.get(OutboxEvent, event_id)
                if event is None:
                    return "MISSING"
                event.last_error = f"handler_error:{type(exc).__name__}"
                event.locked_at = None
                now = datetime.now(timezone.utc)
                if event.attempt_count >= self.max_attempts:
                    event.status = "DEAD"
                else:
                    delay = self.base_backoff_seconds * (2 ** min(event.attempt_count - 1, 10))
                    event.status = "PENDING"
                    event.available_at = now + timedelta(seconds=delay)
                if event.event_type == "WORK_REQUESTED" and event.aggregate_type == "WorkItem" and event.aggregate_id:
                    item = db.get(WorkItem, event.aggregate_id)
                    if item is not None:
                        item.error_code = type(exc).__name__[:64]
                        item.error_details = {"category": type(exc).__name__}
                        item.updated_at = now
                        if event.status == "DEAD":
                            item.status = "FAILED"
                            item.completed_at = now
                        else:
                            item.status = "PENDING"
                            item.completed_at = None
                db.commit()
                return event.status

        with self.session_factory() as db:
            event = db.get(OutboxEvent, event_id)
            if event is None:
                return "MISSING"
            event.status = "PROCESSED"
            event.locked_at = None
            event.last_error = None
            if event.event_type == "WORK_REQUESTED" and event.aggregate_type == "WorkItem" and event.aggregate_id:
                item = db.get(WorkItem, event.aggregate_id)
                if item is not None:
                    item.status = "SUCCEEDED"
                    item.completed_at = datetime.now(timezone.utc)
                    item.updated_at = item.completed_at
                    item.error_code = None
                    item.error_details = None
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
    """Build an idempotent handler for acquired and re-observed raw source records."""
    def handle(payload: dict) -> None:
        raw_id = UUID(payload["raw_source_record_id"])
        with session_factory() as db:
            CanonicalizationService(SqlAlchemyCanonicalizationRepository(db)).process(raw_id)
    return handle
