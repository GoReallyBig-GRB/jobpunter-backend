"""Resolve persisted connector types and versions to implementations."""

from app.infrastructure.acquisition.jobspy import JobSpyConnector
from app.infrastructure.acquisition.json_api import JsonApiConnector
from app.infrastructure.acquisition.playwright import PlaywrightConnector
from app.infrastructure.acquisition.rss_atom import RssAtomConnector
from app.infrastructure.acquisition.scrapy import ScrapyConnector
from app.infrastructure.connectors.greenhouse import GreenhouseJobBoardConnector
from app.ports.acquisition import AcquisitionConnector


class UnsupportedConnectorError(ValueError):
    """Raised when a source connector has no registered implementation."""


class AcquisitionConnectorRegistry:
    def __init__(self) -> None:
        self._factories = {
            (JobSpyConnector.connector_type, JobSpyConnector.connector_version): JobSpyConnector,
            (ScrapyConnector.connector_type, ScrapyConnector.connector_version): ScrapyConnector,
            (PlaywrightConnector.connector_type, PlaywrightConnector.connector_version): PlaywrightConnector,
            (GreenhouseJobBoardConnector.connector_type, GreenhouseJobBoardConnector.connector_version): GreenhouseJobBoardConnector,
            (JsonApiConnector.connector_type, JsonApiConnector.connector_version): JsonApiConnector,
            (RssAtomConnector.connector_type, RssAtomConnector.connector_version): RssAtomConnector,
        }

    def create(self, connector_type: str, connector_version: str) -> AcquisitionConnector:
        factory = self._factories.get((connector_type.strip().upper(), connector_version.strip()))
        if factory is None:
            raise UnsupportedConnectorError(
                f"Unsupported acquisition connector: {connector_type}@{connector_version}"
            )
        return factory()

    def supported_target_kinds(self, connector_type: str, connector_version: str) -> tuple[str, ...]:
        """Return target kinds explicitly supported by the registered connector implementation."""
        factory = self._factories.get((connector_type.strip().upper(), connector_version.strip()))
        if factory is None:
            return ()
        return tuple(sorted(getattr(factory, "supported_target_kinds", ())))

    def supports_target(self, connector_type: str, connector_version: str, target_kind: str) -> bool:
        """Fail closed for unknown connectors and target kinds."""
        if not isinstance(target_kind, str):
            return False
        return target_kind.strip().upper() in self.supported_target_kinds(
            connector_type, connector_version
        )

    @property
    def supported_connectors(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._factories))
