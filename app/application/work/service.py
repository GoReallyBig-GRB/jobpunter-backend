from dataclasses import dataclass
from uuid import UUID

from app.ports.repositories.work import WorkRepository


@dataclass(frozen=True)
class EnqueuedWork:
    work_id: UUID
    work_type: str
    status: str
    idempotency_key: str


class WorkService:
    def __init__(self, repository: WorkRepository):
        self.repository = repository

    def enqueue(
        self,
        *,
        work_type: str,
        idempotency_key: str,
        payload: dict,
        resource_type: str | None = None,
        resource_id: UUID | None = None,
    ) -> EnqueuedWork:
        if not work_type.strip():
            raise ValueError("work_type is required")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")

        item = self.repository.enqueue(
            work_type=work_type,
            idempotency_key=idempotency_key,
            resource_type=resource_type,
            resource_id=resource_id,
            payload=payload,
        )
        return EnqueuedWork(
            work_id=item.id,
            work_type=item.work_type,
            status=item.status,
            idempotency_key=item.idempotency_key,
        )
