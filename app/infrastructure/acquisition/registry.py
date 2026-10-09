"""Resolve persisted connector types and versions to implementations."""

from app.infrastructure.acquisition.jobspy import JobSpyConnector
from app.infrastructure.acquisition.scrapy import ScrapyConnector
from app.ports.acquisition import AcquisitionConnector


class UnsupportedConnectorError(ValueError):
    """Raised when a source connector has no registered implementation."""


class AcquisitionConnectorRegistry:
    def __init__(self) -> None:
        self._factories = {
            (JobSpyConnector.connector_type, JobSpyConnector.connector_version): JobSpyConnector,
            (ScrapyConnector.connector_type, ScrapyConnector.connector_version): ScrapyConnector,
        }

    def create(self, connector_type: str, connector_version: str) -> AcquisitionConnector:
        factory = self._factories.get((connector_type.strip().upper(), connector_version.strip()))
        if factory is None:
            raise UnsupportedConnectorError(
                f"Unsupported acquisition connector: {connector_type}@{connector_version}"
            )
        return factory()

    @property
    def supported_connectors(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._factories))
