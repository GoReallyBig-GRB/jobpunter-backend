import pytest
from uuid import uuid4

from app.infrastructure.acquisition.playwright import PlaywrightConnector, _host_is_allowed
from app.infrastructure.acquisition.registry import AcquisitionConnectorRegistry


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


@pytest.mark.parametrize(
    ("url", "allowed_hosts", "expected"),
    [
        ("https://jobs.example.test/list", {"jobs.example.test"}, True),
        ("https://evil.example.test/list", {"jobs.example.test"}, False),
        ("https://jobs.example.test.attacker.test/list", {"jobs.example.test"}, False),
        ("javascript:alert(1)", {"jobs.example.test"}, False),
        ("https:///missing-host", {"jobs.example.test"}, False),
    ],
)
def test_host_allowlist_requires_exact_hostname(url, allowed_hosts, expected):
    assert _host_is_allowed(url, allowed_hosts) is expected
