from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import OutboxEvent, WorkItem


class SqlAlchemyOutboxRepository:
    def __init__(self, db: Session):
        self.db = db

    def claim_due(self, *, limit: int, lease_seconds: int) -> list[OutboxEvent]:
        now = datetime.now(timezone.utc)
        due = self.db.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.status.in_(("PENDING", "PROCESSING")),
                OutboxEvent.available_at <= now,
            )
            .order_by(OutboxEvent.available_at, OutboxEvent.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()

        lease_until = now + timedelta(seconds=lease_seconds)
        for event in due:
            event.status = "PROCESSING"
            event.attempt_count += 1
            # PROCESSING events become claimable again if the worker dies before ack.
            event.available_at = lease_until
            if event.event_type == "WORK_REQUESTED" and event.aggregate_type == "WorkItem" and event.aggregate_id:
                item = self.db.get(WorkItem, event.aggregate_id)
                if item is not None and item.status != "SUCCEEDED":
                    item.status = "RUNNING"
                    item.attempt_count += 1
                    item.started_at = now
                    item.completed_at = None
        self.db.commit()
        return list(due)

    def mark_processed(self, event_id: UUID) -> None:
        event = self.db.get(OutboxEvent, event_id)
        if event is None:
            return
        now = datetime.now(timezone.utc)
        event.status = "PROCESSED"
        event.processed_at = now
        event.last_error = None
        if event.event_type == "WORK_REQUESTED" and event.aggregate_type == "WorkItem" and event.aggregate_id:
            item = self.db.get(WorkItem, event.aggregate_id)
            if item is not None:
                item.status = "SUCCEEDED"
                item.completed_at = now
                item.error_code = None
                item.error_details = None
        self.db.commit()

    def record_failure(
        self,
        event_id: UUID,
        error_code: str,
        *,
        max_attempts: int,
        base_delay_seconds: int,
        max_delay_seconds: int,
    ) -> None:
        event = self.db.get(OutboxEvent, event_id)
        if event is None:
            return

        now = datetime.now(timezone.utc)
        event.last_error = error_code[:128]
        event.processed_at = None
        if event.attempt_count >= max_attempts:
            event.status = "FAILED"
            if event.event_type == "WORK_REQUESTED" and event.aggregate_type == "WorkItem" and event.aggregate_id:
                item = self.db.get(WorkItem, event.aggregate_id)
                if item is not None:
                    item.status = "FAILED"
                    item.completed_at = now
                    item.error_code = error_code[:64]
                    item.error_details = {"category": error_code[:128]}
        else:
            if event.event_type == "WORK_REQUESTED" and event.aggregate_type == "WorkItem" and event.aggregate_id:
                item = self.db.get(WorkItem, event.aggregate_id)
                if item is not None:
                    item.status = "PENDING"
                    item.completed_at = None
                    item.error_code = error_code[:64]
                    item.error_details = {"category": error_code[:128]}
            delay = min(
                base_delay_seconds * (2 ** max(event.attempt_count - 1, 0)),
                max_delay_seconds,
            )
            event.status = "PENDING"
            event.available_at = now + timedelta(seconds=delay)
        self.db.commit()
