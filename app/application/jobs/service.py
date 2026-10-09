from dataclasses import dataclass
from uuid import UUID

from app.ports.acquisition import AcquisitionConnector
from app.ports.repositories.acquisition import AcquisitionRepository


@dataclass(frozen=True)
class AcquisitionResult:
    run_id: UUID
    acquired_count: int
    persisted_count: int
    status: str


class AcquisitionService:
    def __init__(self, repository: AcquisitionRepository, connector: AcquisitionConnector):
        self.repository = repository
        self.connector = connector

    def run(self, source_id: UUID, connector_id: UUID, run_key: str, context: dict) -> AcquisitionResult:
        existing = self.repository.get_run(source_id, run_key)
        if existing:
            metrics = existing.metrics or {}
            return AcquisitionResult(
                existing.id,
                int(metrics.get("acquired_count", 0)),
                int(metrics.get("persisted_count", 0)),
                existing.status,
            )

        connector_row = self.repository.get_connector(connector_id)
        if not connector_row or connector_row.source_id != source_id:
            raise ValueError("connector does not belong to source")

        run = self.repository.create_run(source_id, connector_id, run_key)
        try:
            acquired = self.connector.acquire(source_id=source_id, context=context)
            if any(item.source_id != source_id for item in acquired):
                raise ValueError("connector returned a record for a different source")

            persisted = sum(
                1 for item in acquired if self.repository.persist_raw_if_new(run.id, item)
            )
            self.repository.complete_run(run.id, len(acquired), persisted)
            return AcquisitionResult(run.id, len(acquired), persisted, "SUCCEEDED")
        except Exception as exc:
            # Store a stable error category, not arbitrary exception text that may contain secrets.
            self.repository.fail_run(run.id, type(exc).__name__)
            raise
