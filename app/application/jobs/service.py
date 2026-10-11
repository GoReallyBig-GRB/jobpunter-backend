from dataclasses import dataclass
from uuid import UUID

from app.ports.acquisition import AcquisitionConnector
from app.ports.repositories.acquisition import AcquisitionRepository


@dataclass(frozen=True)
class AcquisitionResult:
    run_id: UUID
    status: str
    acquired_count: int
    persisted_count: int

    @property
    def duplicate_count(self) -> int:
        """Records fetched from the source that were not newly persisted due to deduplication."""
        return max(self.acquired_count - self.persisted_count, 0)


class AcquisitionService:
    def __init__(self, repository: AcquisitionRepository, connector: AcquisitionConnector):
        self.repository = repository
        self.connector = connector

    @staticmethod
    def _result(run) -> AcquisitionResult:
        metrics = run.metrics or {}
        return AcquisitionResult(
            run_id=run.id,
            status=run.status,
            acquired_count=int(metrics.get("acquired_count", 0)),
            persisted_count=int(metrics.get("persisted_count", 0)),
        )

    def run(
        self, source_id: UUID, connector_id: UUID, run_key: str, context: dict
    ) -> AcquisitionResult:
        existing = self.repository.get_run(source_id, run_key)
        if existing:
            return self._result(existing)

        connector_row = self.repository.get_connector(connector_id)
        if not connector_row or connector_row.source_id != source_id:
            raise ValueError("connector does not belong to source")

        run, created = self.repository.create_run(source_id, connector_id, run_key)
        if not created:
            # A concurrent request claimed this idempotency key first.
            return self._result(run)

        try:
            acquired = self.connector.acquire(source_id=source_id, context=context)
        except Exception as exc:
            self.repository.fail_run(
                run.id,
                error_summary=f"connector_error:{type(exc).__name__}",
            )
            raise

        persisted_count = 0
        try:
            persisted_count = sum(
                1 for item in acquired if self.repository.persist_raw_if_new(run.id, item)
            )
            self.repository.complete_run(run.id, len(acquired), persisted_count)
        except Exception as exc:
            self.repository.fail_run(
                run.id,
                error_summary=f"persistence_error:{type(exc).__name__}",
                acquired_count=len(acquired),
                # Raw rows and outbox events share one transaction; fail_run rolls it back.
                persisted_count=0,
            )
            raise

        completed = self.repository.get_run(source_id, run_key)
        if completed is None:
            raise RuntimeError("Completed acquisition run could not be retrieved")
        return self._result(completed)
