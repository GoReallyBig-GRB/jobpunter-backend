"""Run an acquisition using the implementation registered for its source connector."""

from uuid import UUID

from app.application.jobs.service import AcquisitionResult, AcquisitionService
from app.infrastructure.acquisition.registry import AcquisitionConnectorRegistry
from app.ports.repositories.acquisition import AcquisitionRepository


class RegisteredAcquisitionService:
    def __init__(
        self,
        repository: AcquisitionRepository,
        registry: AcquisitionConnectorRegistry | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry or AcquisitionConnectorRegistry()

    def run(
        self, source_id: UUID, connector_id: UUID, run_key: str, context: dict
    ) -> AcquisitionResult:
        connector_row = self.repository.get_connector(connector_id)
        if connector_row is None or connector_row.source_id != source_id:
            raise ValueError("connector does not belong to source")

        connector = self.registry.create(
            connector_row.connector_type,
            connector_row.connector_version,
        )
        return AcquisitionService(self.repository, connector).run(
            source_id, connector_id, run_key, context
        )
