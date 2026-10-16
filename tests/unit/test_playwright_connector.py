import pytest
import socket
import sys
from types import ModuleType, SimpleNamespace
from uuid import uuid4

from app.infrastructure.acquisition.playwright import PlaywrightConnector, _host_is_allowed
from app.infrastructure.acquisition.registry import AcquisitionConnectorRegistry
from app.ports.acquisition import AcquisitionOutcome


@pytest.fixture(autouse=True)
def resolve_test_hosts_to_public_ip(monkeypatch):
    """Keep fake .test hostnames deterministic without live DNS."""
    monkeypatch.setattr(
        "app.infrastructure.acquisition.playwright.socket.getaddrinfo",
        lambda host, port, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))
        ],
    )


def test_playwright_connector_is_registered():
    registry = AcquisitionConnectorRegistry()
    assert ("PLAYWRIGHT", "1.0.0") in registry.supported_connectors
    assert isinstance(registry.create("playwright", "1.0.0"), PlaywrightConnector)


def test_playwright_rejects_private_ip_literal_before_browser_launch():
    with pytest.raises(ValueError, match="private or non-public"):
        PlaywrightConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["http://127.0.0.1/jobs"]},
        )


def test_playwright_rejects_dns_answers_with_private_addresses(monkeypatch):
    monkeypatch.setattr(
        "app.infrastructure.acquisition.playwright.socket.getaddrinfo",
        lambda host, port, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port or 443))
        ],
    )
    with pytest.raises(ValueError, match="private or non-public"):
        PlaywrightConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["https://jobs.example.test/list"]},
        )


def test_playwright_rejects_embedded_url_credentials():
    with pytest.raises(ValueError, match="without credentials"):
        PlaywrightConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["https://user:pass@jobs.example.test/list"]},
        )


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
        ("https://jobs.example.test./list", {"jobs.example.test"}, False),
        ("https://jobs.example.test:8443/list", {"jobs.example.test"}, False),
        ("http://jobs.example.test:443/list", {"jobs.example.test"}, False),
        ("https://jobs.example.test:443/list", {"jobs.example.test"}, True),
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
    calls = {"route_handler": None, "aborted": False, "continued": False, "launch_options": None}

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
            return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: (calls.update({"launch_options": kwargs}) or FakeBrowser())))

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
    assert result.status == AcquisitionOutcome.RESTRICTED
    assert result.records == ()
    calls["route_handler"](FakeRoute("https://external.example.test/script.js"))
    assert calls["aborted"] is True
    calls["route_handler"](FakeRoute("https://jobs.example.test/style.css"))
    assert calls["continued"] is True
    assert "--no-proxy-server" in calls["launch_options"]["args"]
    assert "--disable-features=AsyncDns" in calls["launch_options"]["args"]
    assert "--host-resolver-rules=MAP jobs.example.test 93.184.216.34" in calls["launch_options"]["args"]


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
    assert result.status == AcquisitionOutcome.RESTRICTED
    assert result.records == ()



@pytest.mark.parametrize(
    ("max_pages", "max_items", "expected_category"),
    [
        (2, 500, "PAGE_TIMEOUT"),
        (1, 500, "MAX_PAGES_LIMIT_REACHED"),
        (2, 1, "MAX_ITEMS_LIMIT_REACHED"),
    ],
)
def test_playwright_preserves_records_when_a_later_page_fails(
    monkeypatch, max_pages, max_items, expected_category,
):
    calls = {"page": 0}
    monkeypatch.setattr(PlaywrightConnector, "MAX_ITEMS", max_items)

    class FakeTimeout(Exception):
        pass

    class FakeLocator:
        def __init__(self, text=None, href=None, count=1):
            self.text = text
            self.href = href
            self._count = count

        @property
        def first(self):
            return self

        def count(self):
            return self._count

        def nth(self, index):
            return self

        def inner_text(self):
            return self.text or ""

        def get_attribute(self, name):
            return self.href

    class FakeCard:
        def locator(self, selector):
            if selector == ".title":
                return FakeLocator(text="Product Manager")
            if selector == "a":
                return FakeLocator(href="/job/1")
            return FakeLocator(count=0)

    class FakeCards(FakeLocator):
        def nth(self, index):
            return FakeCard()

    class FakePage:
        url = "https://jobs.example.test/page1"

        def set_default_timeout(self, timeout):
            pass

        def goto(self, url, **kwargs):
            calls["page"] += 1
            self.url = url
            if url.endswith("page2"):
                raise FakeTimeout("timeout")
            return SimpleNamespace(status=200)

        def locator(self, selector):
            if selector == "article":
                return FakeCards()
            return FakeLocator(count=0)

    class FakeBrowserContext:
        def route(self, pattern, handler):
            pass

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
    fake_sync_api.TimeoutError = FakeTimeout
    fake_sync_api.sync_playwright = lambda: FakePlaywrightContextManager()
    fake_playwright = ModuleType("playwright")
    fake_playwright.sync_api = fake_sync_api
    monkeypatch.setitem(sys.modules, "playwright", fake_playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)
    monkeypatch.setattr("app.infrastructure.acquisition.playwright.time.sleep", lambda _seconds: None)

    result = PlaywrightConnector().acquire(
        source_id=uuid4(),
        context={
            "start_urls": [
                "https://jobs.example.test/page1",
                "https://jobs.example.test/page2",
            ],
            "allowed_domains": ["jobs.example.test"],
            "selectors": {"job_card": "article", "title": ".title", "job_url": "a"},
            "max_pages": max_pages,
        },
    )

    assert result.status == AcquisitionOutcome.PARTIAL
    assert len(result.records) == 1
    assert result.records[0].raw_content["title"] == "Product Manager"
    assert result.error_category == expected_category
    assert result.failures[0].category == expected_category
    assert result.failures[0].retryable is (expected_category == "PAGE_TIMEOUT")

@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.example.test:8443/list",
        "http://jobs.example.test:8080/list",
    ],
)
def test_playwright_rejects_nonstandard_start_url_ports_before_browser_launch(url):
    with pytest.raises(ValueError, match="nonstandard ports"):
        PlaywrightConnector().acquire(source_id=uuid4(), context={"start_urls": [url]})
