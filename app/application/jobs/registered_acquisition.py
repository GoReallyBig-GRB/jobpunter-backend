"""Run a configured acquisition connector after enforcing source/connector state."""

from uuid import UUID

from app.application.jobs.service import AcquisitionResult, AcquisitionService
from app.application.jobs.source_governance import assert_automated_acquisition_permitted
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
        source_row = self.repository.get_source(source_id)
        if source_row is None:
            raise ValueError("source does not exist")
        if str(source_row.status).strip().upper() != "ACTIVE":
            raise ValueError("source is not active")
        connector_row = self.repository.get_connector(connector_id)
        if connector_row is None or connector_row.source_id != source_id:
            raise ValueError("connector does not belong to source")
        if str(connector_row.status).strip().upper() != "ACTIVE":
            raise ValueError("connector is not active")
        try:
            assert_automated_acquisition_permitted(source_row)
        except ValueError:
            self.repository.record_source_operational_state(
                source_id, "RESTRICTED", "PERMISSION_REVIEW_REQUIRED"
            )
            raise

        # Persisted configuration provides defaults; explicit per-run values override them.
        # Runtime context must be a mapping to avoid surprising or unsafe coercion.
        if not isinstance(context, dict):
            raise ValueError("acquisition context must be an object")
        merged_context: dict = {}
        for config in (source_row.configuration, connector_row.configuration, context):
            if config is None:
                continue
            if not isinstance(config, dict):
                raise ValueError("source and connector configuration must be objects")
            merged_context.update(config)

        connector = self.registry.create(
            connector_row.connector_type,
            connector_row.connector_version,
        )
        return AcquisitionService(self.repository, connector).run(
            source_id, connector_id, run_key, merged_context
        )
