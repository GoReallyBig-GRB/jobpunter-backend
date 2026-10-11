from typing import Protocol
from uuid import UUID


class WorkRepository(Protocol):
    def enqueue(
        self,
        *,
        work_type: str,
        idempotency_key: str,
        resource_type: str | None,
        resource_id: UUID | None,
        payload: dict,
    ): ...
