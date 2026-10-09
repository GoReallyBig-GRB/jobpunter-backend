from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.jobs.registered_acquisition import RegisteredAcquisitionService
from app.infrastructure.acquisition.registry import (
    AcquisitionConnectorRegistry,
    UnsupportedConnectorError,
)
from app.infrastructure.acquisition.jobspy import JobSpyConnector
from app.infrastructure.acquisition.scrapy import ScrapyConnector


def test_registry_resolves_jobspy_type_and_version():
    connector = AcquisitionConnectorRegistry().create("JOBSPY", "1.0.0")
    assert isinstance(connector, JobSpyConnector)


def test_registry_resolves_scrapy_type_and_version():
    connector = AcquisitionConnectorRegistry().create("SCRAPY", "1.0.0")
    assert isinstance(connector, ScrapyConnector)


def test_registry_rejects_unknown_type_or_version():
    registry = AcquisitionConnectorRegistry()
    with pytest.raises(UnsupportedConnectorError, match="Unsupported acquisition connector"):
        registry.create("UNKNOWN", "1.0.0")
    with pytest.raises(UnsupportedConnectorError):
        registry.create("JOBSPY", "99.0.0")


def test_registered_service_rejects_connector_owned_by_another_source():
    source_id = uuid4()
    repository = SimpleNamespace(
        get_connector=lambda connector_id: SimpleNamespace(source_id=uuid4())
    )
    with pytest.raises(ValueError, match="connector does not belong to source"):
        RegisteredAcquisitionService(repository).run(
            source_id, uuid4(), "run-1", {"search_terms": ["PM"], "location": "Nigeria"}
        )
