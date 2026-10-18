from typing import Protocol
from uuid import UUID

from app.ports.acquisition import AcquisitionOutcome, RawAcquisition


class AcquisitionRepository(Protocol):
    def get_run(self, source_id: UUID, run_key: str): ...

    def get_connector(self, connector_id: UUID): ...
    def list_active_forbidden_target_rules(self) -> list[dict]: ...

    def create_run(self, source_id: UUID, connector_id: UUID, run_key: str): ...

    def persist_raw_if_new(self, run_id: UUID, item: RawAcquisition) -> bool: ...

    def record_observation(
        self, run_id: UUID, item: RawAcquisition, *, persisted_new: bool
    ) -> str: ...

    def complete_run(
        self,
        run_id: UUID,
        acquired_count: int,
        persisted_count: int,
        *,
        status: str = AcquisitionOutcome.SUCCEEDED.value,
        error_category: str | None = None,
        failure_count: int = 0,
        failure_categories: list[str] | None = None,
        retry_after_seconds: int | None = None,
    ) -> None: ...

    def fail_run(
        self,
        run_id: UUID,
        *,
        error_summary: str,
        acquired_count: int = 0,
        persisted_count: int = 0,
    ) -> None: ...

    def recover_stale_runs(self, *, now=None, limit: int = 100) -> int: ...

    def record_source_operational_state(
        self, source_id: UUID, state: str, error_category: str | None = None
    ) -> None: ...
