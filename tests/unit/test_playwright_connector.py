import pytest
import sys
from types import ModuleType, SimpleNamespace
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


def test_job_link_is_not_accepted_outside_allowed_hosts():
    from urllib.parse import urljoin

    allowed_hosts = {"jobs.example.test"}
    candidate = urljoin("https://jobs.example.test/list", "https://attacker.test/fake-job")
    assert _host_is_allowed(candidate, allowed_hosts) is False


def test_same_host_job_link_is_accepted_by_allowlist():
    from urllib.parse import urljoin

    allowed_hosts = {"jobs.example.test"}
    candidate = urljoin("https://jobs.example.test/list", "/job/123")
    assert _host_is_allowed(candidate, allowed_hosts) is True



def test_browser_aborts_requests_to_hosts_outside_allowlist(monkeypatch):
    calls = {"route_handler": None, "aborted": False, "continued": False}

    class FakeRoute:
        def __init__(self, url):
            self.request = SimpleNamespace(url=url)

        def abort(self):
            calls["aborted"] = True

        def continue_(self):
            calls["continued"] = True

    class FakePage:
        url = "https://jobs.example.test/list"

        def set_default_timeout(self, timeout):
            pass

        def goto(self, url, **kwargs):
            self.url = "https://external.example.test/redirected"
            return SimpleNamespace(status=200)

    class FakeBrowserContext:
        def route(self, pattern, handler):
            calls["route_handler"] = handler

        def new_page(self):
            return FakePage()

        def close(self):
            pass

    class FakeBrowser:
        def new_context(self, **kwargs):
            return FakeBrowserContext()

        def close(self):
            pass

    class FakePlaywrightContextManager:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: FakeBrowser()))

        def __exit__(self, *args):
            return False

    fake_sync_api = ModuleType("playwright.sync_api")
    fake_sync_api.TimeoutError = type("PlaywrightTimeoutError", (Exception,), {})
    fake_sync_api.sync_playwright = lambda: FakePlaywrightContextManager()
    fake_playwright = ModuleType("playwright")
    fake_playwright.sync_api = fake_sync_api
    monkeypatch.setitem(sys.modules, "playwright", fake_playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)

    result = PlaywrightConnector().acquire(
        source_id=uuid4(),
        context={"start_urls": ["https://jobs.example.test/list"], "allowed_domains": ["jobs.example.test"]},
    )
    assert result == []
    calls["route_handler"](FakeRoute("https://external.example.test/script.js"))
    assert calls["aborted"] is True
    calls["route_handler"](FakeRoute("https://jobs.example.test/style.css"))
    assert calls["continued"] is True


def test_off_domain_redirect_is_not_extracted(monkeypatch):
    class FakePage:
        url = "https://jobs.example.test/list"

        def set_default_timeout(self, timeout):
            pass

        def goto(self, url, **kwargs):
            self.url = "https://external.example.test/redirected"
            return SimpleNamespace(status=200)

    class FakeBrowserContext:
        def route(self, pattern, handler):
            self.route_handler = handler

        def new_page(self):
            return FakePage()

        def close(self):
            pass

    class FakeBrowser:
        def new_context(self, **kwargs):
            return FakeBrowserContext()

        def close(self):
            pass

    class FakePlaywrightContextManager:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: FakeBrowser()))

        def __exit__(self, *args):
            return False

    fake_sync_api = ModuleType("playwright.sync_api")
    fake_sync_api.TimeoutError = type("PlaywrightTimeoutError", (Exception,), {})
    fake_sync_api.sync_playwright = lambda: FakePlaywrightContextManager()
    fake_playwright = ModuleType("playwright")
    fake_playwright.sync_api = fake_sync_api
    monkeypatch.setitem(sys.modules, "playwright", fake_playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)

    result = PlaywrightConnector().acquire(
        source_id=uuid4(),
        context={"start_urls": ["https://jobs.example.test/list"], "allowed_domains": ["jobs.example.test"]},
    )
    assert result == []
