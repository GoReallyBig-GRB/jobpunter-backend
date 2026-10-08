import uuid

from sqlalchemy.orm import Session

from app.infrastructure.db.models.outbox import OutboxEvent


class OutboxService:
    def __init__(self, db: Session):
        self.db = db

    def append(self, *, event_type: str, aggregate_type: str, aggregate_id: uuid.UUID | None, payload: dict) -> OutboxEvent:
        event = OutboxEvent(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
        )
        self.db.add(event)
        self.db.flush()
        return event
