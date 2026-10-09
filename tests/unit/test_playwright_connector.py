import pytest
from uuid import uuid4

from app.infrastructure.acquisition.playwright import PlaywrightConnector
from app.infrastructure.acquisition.registry import AcquisitionConnectorRegistry
from app.infrastructure.acquisition.playwright import PlaywrightConnector


def test_playwright_connector_is_registered():
    registry = AcquisitionConnectorRegistry()
    assert ("PLAYWRIGHT", "1.0.0") in registry.supported_connectors
    assert isinstance(registry.create("playwright", "1.0.0"), PlaywrightConnector)


def test_playwright_rejects_missing_urls_before_browser_launch():
    with pytest.raises(ValueError, match="start_urls"):
        PlaywrightConnector().acquire(source_id=uuid4(), context={})


def test_playwright_rejects_non_http_urls():
    with pytest.raises(ValueError, match="absolute HTTP"):
        PlaywrightConnector().acquire(
            source_id=uuid4(), context={"start_urls": ["file:///etc/passwd"]}
        )


def test_playwright_rejects_unbounded_page_count():
    with pytest.raises(ValueError, match="max_pages"):
        PlaywrightConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["https://example.test/jobs"], "max_pages": 10000},
        )


def test_playwright_rejects_cross_domain_start_urls():
    with pytest.raises(ValueError, match="allowed_domains"):
        PlaywrightConnector().acquire(
            source_id=uuid4(),
            context={
                "start_urls": ["https://not-allowed.test/jobs"],
                "allowed_domains": ["jobs.example.test"],
            },
        )
