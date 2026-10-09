from datetime import datetime
from typing import Protocol
from uuid import UUID


class OutboxRepository(Protocol):
    def claim_due(self, *, limit: int, lease_seconds: int) -> list: ...

    def mark_processed(self, event_id: UUID) -> None: ...

    def record_failure(
        self,
        event_id: UUID,
        error_code: str,
        *,
        max_attempts: int,
        base_delay_seconds: int,
        max_delay_seconds: int,
    ) -> None: ...
