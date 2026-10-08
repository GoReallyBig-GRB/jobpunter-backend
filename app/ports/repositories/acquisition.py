from typing import Protocol
from uuid import UUID

from app.ports.acquisition import RawAcquisition


class AcquisitionRepository(Protocol):
    def get_run(self, source_id: UUID, run_key: str): ...

    def get_connector(self, connector_id: UUID): ...

    def create_run(self, source_id: UUID, connector_id: UUID, run_key: str): ...

    def persist_raw_if_new(self, run_id: UUID, item: RawAcquisition) -> bool: ...

    def complete_run(self, run_id: UUID, acquired_count: int, persisted_count: int) -> None: ...

    def fail_run(
        self,
        run_id: UUID,
        *,
        error_summary: str,
        acquired_count: int = 0,
        persisted_count: int = 0,
    ) -> None: ...
