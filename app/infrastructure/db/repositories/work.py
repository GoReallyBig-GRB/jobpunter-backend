from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.infrastructure.db.models import OutboxEvent, WorkItem


class SqlAlchemyWorkRepository:
    """Atomically persist a work item and its dispatch event."""

    def __init__(self, db: Session):
        self.db = db

    def enqueue(
        self,
        *,
        work_type: str,
        idempotency_key: str,
        resource_type: str | None,
        resource_id: UUID | None,
        payload: dict,
    ) -> WorkItem:
        existing = self.db.scalar(
            select(WorkItem).where(
                WorkItem.work_type == work_type,
                WorkItem.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing

        now = datetime.now(timezone.utc)
        item = WorkItem(
            work_type=work_type,
            idempotency_key=idempotency_key,
            resource_type=resource_type,
            resource_id=resource_id,
            status="PENDING",
            attempt_count=0,
            created_at=now,
            updated_at=now,
        )
        try:
            self.db.add(item)
            self.db.flush()
            event = OutboxEvent(
                event_type="WORK_REQUESTED",
                aggregate_type="WorkItem",
                aggregate_id=item.id,
                payload={
                    "work_item_id": str(item.id),
                    "work_type": work_type,
                    "idempotency_key": idempotency_key,
                    "payload": payload,
                },
                status="PENDING",
                attempt_count=0,
                available_at=now,
                created_at=now,
            )
            self.db.add(event)
            self.db.commit()
            self.db.refresh(item)
            return item
        except IntegrityError:
            self.db.rollback()
            # A concurrent request may have won the unique-key race. Return that
            # durable item if it exists; otherwise preserve the original database error.
            existing = self.db.scalar(
                select(WorkItem).where(
                    WorkItem.work_type == work_type,
                    WorkItem.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return existing
            raise
