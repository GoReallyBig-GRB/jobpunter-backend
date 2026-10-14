"""Read access to the unified append-only audit projection."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import AuditEvent


class AuditEventService:
    """Query audit history after the calling domain service enforces ownership."""

    def __init__(self, db: Session):
        self.db = db

    def list_for_resource(self, resource_type: str, resource_id: UUID) -> list[AuditEvent]:
        return list(self.db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.resource_type == resource_type,
                AuditEvent.resource_id == resource_id,
            )
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
        ).all())
