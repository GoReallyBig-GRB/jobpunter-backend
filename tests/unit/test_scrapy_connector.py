import pytest
from uuid import uuid4

from app.infrastructure.acquisition.registry import AcquisitionConnectorRegistry
from app.infrastructure.acquisition.scrapy import ScrapyConnector


def test_scrapy_connector_is_registered():
    registry = AcquisitionConnectorRegistry()
    assert ("SCRAPY", "1.0.0") in registry.supported_connectors
    assert isinstance(registry.create("scrapy", "1.0.0"), ScrapyConnector)


def test_scrapy_rejects_missing_urls_before_network_or_dependency_use():
    with pytest.raises(ValueError, match="start_urls"):
        ScrapyConnector().acquire(source_id=uuid4(), context={})


def test_scrapy_rejects_non_http_urls():
    with pytest.raises(ValueError, match="absolute HTTP"):
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["file:///etc/passwd"]},
        )


def test_scrapy_rejects_unbounded_page_count():
    with pytest.raises(ValueError, match="max_pages"):
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["https://example.test/jobs"], "max_pages": 10000},
        )
