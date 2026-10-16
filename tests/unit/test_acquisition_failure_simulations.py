from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest

from app.infrastructure.acquisition.jobspy import JobSpyConnector
from app.infrastructure.acquisition.playwright import PlaywrightConnector
from app.ports.acquisition import AcquisitionOutcome


class FakeFrame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, *, orient):
        assert orient == "records"
        return self.rows


def test_jobspy_empty_result_is_an_empty_success_not_a_fake_job(monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "jobspy",
        SimpleNamespace(scrape_jobs=lambda **kwargs: FakeFrame([])),
    )

    result = JobSpyConnector().acquire(
        source_id=uuid4(),
        context={"search_terms": ["Technical Product Manager"], "location": "Nigeria"},
    )

    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert result.records == ()


def test_jobspy_library_failure_is_not_silently_reported_as_empty(monkeypatch):
    def fail(**kwargs):
        raise TimeoutError("upstream timed out")

    monkeypatch.setitem(__import__("sys").modules, "jobspy", SimpleNamespace(scrape_jobs=fail))

    with pytest.raises(TimeoutError, match="upstream timed out"):
        JobSpyConnector().acquire(
            source_id=uuid4(),
            context={"search_terms": ["Technical Product Manager"], "location": "Nigeria"},
        )


class FakeLocator:
    def __init__(self, *, items=None, text=None, href=None):
        self.items = items
        self.text = text
        self.href = href
        self.first = self

    def count(self):
        return len(self.items) if self.items is not None else int(self.text is not None or self.href is not None)

    def nth(self, index):
        return self.items[index]

    def inner_text(self):
        if self.text is None:
            raise AssertionError("No text configured for this selector")
        return self.text

    def get_attribute(self, name):
        assert name == "href"
        return self.href

    def wait_for(self, **kwargs):
        return None


class FakeCard:
    values = {
        ".title": "Technical Product Manager",
        ".company": "Example Ltd",
        ".location": "Remote, Nigeria",
        ".description": "Own product strategy and coordinate engineering delivery.",
    }

    def locator(self, selector):
        if selector == "a":
            return FakeLocator(href="/jobs/123")
        return FakeLocator(text=self.values.get(selector))


def _install_fake_browser(monkeypatch, *, status=200, final_url=None):
    calls = {"closed": False}

    class FakePage:
        url = "https://jobs.example.test/list"

        def set_default_timeout(self, timeout):
            calls["timeout"] = timeout

        def goto(self, url, **kwargs):
            self.url = final_url or url
            return SimpleNamespace(status=status)

        def locator(self, selector):
            assert selector == "article"
            return FakeLocator(items=[FakeCard()])

    class FakeBrowserContext:
        def route(self, pattern, handler):
            calls["route_pattern"] = pattern

        def new_page(self):
            return FakePage()

        def close(self):
            calls["context_closed"] = True

    class FakeBrowser:
        def new_context(self, **kwargs):
            calls["context_options"] = kwargs
            return FakeBrowserContext()

        def close(self):
            calls["closed"] = True

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
    monkeypatch.setitem(__import__("sys").modules, "playwright", fake_playwright)
    monkeypatch.setitem(__import__("sys").modules, "playwright.sync_api", fake_sync_api)
    return calls


def test_playwright_extracts_traceable_records_from_a_permitted_page(monkeypatch):
    calls = _install_fake_browser(monkeypatch)
    source_id = uuid4()

    records = PlaywrightConnector().acquire(
        source_id=source_id,
        context={
            "start_urls": ["https://jobs.example.test/list"],
            "allowed_domains": ["jobs.example.test"],
            "selectors": {
                "job_card": "article",
                "title": ".title",
                "company": ".company",
                "location": ".location",
                "description": ".description",
            },
            "request_delay_seconds": 1,
        },
    )

    assert records.status == AcquisitionOutcome.SUCCEEDED
    assert len(records.records) == 1
    record = records.records[0]
    assert record.source_id == source_id
    assert record.source_url == "https://jobs.example.test/jobs/123"
    assert record.raw_content["title"] == "Technical Product Manager"
    assert record.raw_content["company"] == "Example Ltd"
    assert record.raw_content["description"]
    assert len(record.content_hash) == 64
    assert calls["closed"] is True
    assert calls["context_closed"] is True


@pytest.mark.parametrize("status", [401, 403, 407, 429, 500])
def test_playwright_does_not_extract_from_blocked_or_failed_responses(monkeypatch, status):
    _install_fake_browser(monkeypatch, status=status)

    records = PlaywrightConnector().acquire(
        source_id=uuid4(),
        context={
            "start_urls": ["https://jobs.example.test/list"],
            "allowed_domains": ["jobs.example.test"],
            "selectors": {"job_card": "article", "title": ".title"},
            "request_delay_seconds": 1,
        },
    )

    assert records.records == ()


def test_playwright_does_not_extract_after_off_domain_redirect(monkeypatch):
    _install_fake_browser(
        monkeypatch,
        final_url="https://external.example.test/redirected",
    )

    records = PlaywrightConnector().acquire(
        source_id=uuid4(),
        context={
            "start_urls": ["https://jobs.example.test/list"],
            "allowed_domains": ["jobs.example.test"],
            "selectors": {"job_card": "article", "title": ".title"},
            "request_delay_seconds": 1,
        },
    )

    assert records.records == ()
