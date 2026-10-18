import json
import socket
import subprocess
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest

from app.infrastructure.acquisition.registry import AcquisitionConnectorRegistry
from app.ports.acquisition import AcquisitionAdapterError, AcquisitionOutcome
from app.infrastructure.acquisition.scrapy import ScrapyConnector
from app.infrastructure.acquisition.scrapy_worker_runtime import (
    ForbiddenTargetMiddleware,
    PublicOnlyCachingThreadedResolver,
    _require_public_resolved_address,
    _build_worker_policy_guard,
    crawl,
)


def test_scrapy_runtime_resolver_accepts_public_addresses():
    assert _require_public_resolved_address("93.184.216.34") == "93.184.216.34"
    assert _require_public_resolved_address("2606:4700:4700::1111") == "2606:4700:4700::1111"


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "::1", "169.254.169.254"])
def test_scrapy_runtime_resolver_rejects_non_public_addresses(address):
    with pytest.raises(ValueError, match="private or non-public"):
        _require_public_resolved_address(address)


def test_scrapy_dns_resolver_validates_the_address_used_for_connection(monkeypatch):
    class FakeDeferred:
        def __init__(self, value):
            self.value = value
            self.failure = None

        def addCallback(self, callback):
            try:
                self.value = callback(self.value)
            except Exception as exc:
                self.failure = exc
            return self

    class FakeCachingThreadedResolver:
        address = "93.184.216.34"

        def __init__(self, reactor, cache_size, timeout):
            self.reactor = reactor
            self.cache_size = cache_size
            self.timeout = timeout

        def getHostByName(self, name, timeout=None):
            return FakeDeferred(self.address)

    fake_scrapy = ModuleType("scrapy")
    fake_scrapy.__path__ = []
    fake_resolver = ModuleType("scrapy.resolver")
    fake_resolver.CachingThreadedResolver = FakeCachingThreadedResolver
    fake_scrapy.resolver = fake_resolver
    monkeypatch.setitem(__import__("sys").modules, "scrapy", fake_scrapy)
    monkeypatch.setitem(__import__("sys").modules, "scrapy.resolver", fake_resolver)

    class FakeReactor:
        def installResolver(self, resolver):
            self.resolver = resolver

    reactor = FakeReactor()
    resolver = PublicOnlyCachingThreadedResolver(reactor)
    resolver.install_on_reactor()
    accepted = resolver.getHostByName("jobs.example.test")
    assert reactor.resolver is resolver
    assert accepted.value == "93.184.216.34"
    assert accepted.failure is None

    FakeCachingThreadedResolver.address = "127.0.0.1"
    rejected = resolver.getHostByName("jobs.example.test")
    assert rejected.failure is not None
    assert "private or non-public" in str(rejected.failure)


def test_scrapy_worker_enables_runtime_dns_guard_and_response_limit(monkeypatch):
    class FakeSpider:
        pass

    class FakeCrawlerProcess:
        latest = None

        def __init__(self, settings=None):
            self.__class__.latest = self
            self.settings = settings or {}

        def crawl(self, spider):
            self.spider = spider

        def start(self, **kwargs):
            self.start_kwargs = kwargs

    fake_scrapy = ModuleType("scrapy")
    fake_scrapy.__path__ = []
    fake_scrapy.Spider = FakeSpider
    fake_crawler = ModuleType("scrapy.crawler")
    fake_crawler.CrawlerProcess = FakeCrawlerProcess
    fake_scrapy.crawler = fake_crawler
    monkeypatch.setitem(__import__("sys").modules, "scrapy", fake_scrapy)
    monkeypatch.setitem(__import__("sys").modules, "scrapy.crawler", fake_crawler)

    crawl({
        "start_urls": ["https://jobs.example.test/jobs"],
        "selectors": {},
        "allowed_domains": ["jobs.example.test"],
        "max_pages": 1,
        "max_items": 10,
        "download_delay_seconds": 1.0,
        "user_agent": "JobPunterTest/1.0",
    })

    process_settings = FakeCrawlerProcess.latest.settings
    spider_settings = FakeCrawlerProcess.latest.spider.custom_settings
    assert process_settings["TWISTED_DNS_RESOLVER"] == (
        "app.infrastructure.acquisition.scrapy_worker_runtime.PublicOnlyCachingThreadedResolver"
    )
    assert process_settings["HTTPPROXY_ENABLED"] is False
    assert process_settings["DOWNLOAD_MAXSIZE"] == 2_000_000
    assert spider_settings["TWISTED_DNS_RESOLVER"] == process_settings["TWISTED_DNS_RESOLVER"]
    assert spider_settings["HTTPPROXY_ENABLED"] is False
    assert spider_settings["DOWNLOAD_MAXSIZE"] == 2_000_000
    assert FakeCrawlerProcess.latest.start_kwargs == {"stop_after_crawl": True}


def test_scrapy_connector_is_registered():
    registry = AcquisitionConnectorRegistry()
    assert ("SCRAPY", "1.0.0") in registry.supported_connectors
    assert isinstance(registry.create("scrapy", "1.0.0"), ScrapyConnector)


def test_scrapy_rejects_missing_urls_before_worker_use():
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


def test_scrapy_rejects_invalid_timeout():
    with pytest.raises(ValueError, match="timeout_seconds"):
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["https://example.test/jobs"], "timeout_seconds": 1},
        )


def test_each_acquisition_uses_a_fresh_worker_process(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"records":[]}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    connector = ScrapyConnector()
    context = {"start_urls": ["https://example.test/jobs"]}

    first = connector.acquire(source_id=uuid4(), context=context)
    second = connector.acquire(source_id=uuid4(), context=context)
    assert first.status == second.status == AcquisitionOutcome.SUCCEEDED
    assert first.records == second.records == ()

    assert len(calls) == 2
    assert calls[0][0] == calls[1][0]
    assert calls[0][0][-1] == "app.infrastructure.acquisition.scrapy_worker"
    assert calls[0][1]["timeout"] == 180
    assert calls[1][1]["timeout"] == 180
    assert json.loads(calls[0][1]["input"])["start_urls"] == context["start_urls"]


def test_scrapy_worker_records_are_normalized(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"records": [{"title": "Product Manager", "job_url": "https://example.test/job/1"}]}),
            stderr="",
        ),
    )
    source_id = uuid4()
    records = ScrapyConnector().acquire(
        source_id=source_id,
        context={"start_urls": ["https://example.test/jobs"]},
    )

    assert records.status == AcquisitionOutcome.SUCCEEDED
    assert len(records.records) == 1
    assert records.records[0].source_id == source_id
    assert records.records[0].source_url == "https://example.test/job/1"
    assert records.records[0].raw_content["connector"] == "SCRAPY"
    assert records.records[0].raw_content["connector_version"] == "1.0.0"
    assert len(records.records[0].content_hash) == 64


def test_scrapy_timeout_becomes_safe_source_error(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="scrapy-worker", timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(AcquisitionAdapterError) as raised:
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["https://example.test/jobs"]},
        )
    assert raised.value.status == AcquisitionOutcome.UNAVAILABLE
    assert raised.value.error_category == "SCRAPY_WORKER_TIMEOUT"


def test_scrapy_nonzero_exit_does_not_expose_worker_output(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=2,
            stdout="",
            stderr="secret token and operator URL",
        ),
    )
    with pytest.raises(AcquisitionAdapterError) as exc:
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["https://example.test/jobs"]},
        )
    assert exc.value.status == AcquisitionOutcome.FAILED
    assert exc.value.error_category == "SCRAPY_WORKER_FAILED"
    assert "secret token" not in str(exc.value)
    assert "operator URL" not in str(exc.value)


def test_scrapy_rejects_malformed_worker_output(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="not-json", stderr=""),
    )
    with pytest.raises(AcquisitionAdapterError) as raised:
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["https://example.test/jobs"]},
        )
    assert raised.value.status == AcquisitionOutcome.FAILED
    assert raised.value.error_category == "SCRAPY_INVALID_RESULT"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/jobs",
        "http://10.1.2.3/jobs",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/jobs",
        "http://localhost/jobs",
        "http://service.internal/jobs",
    ],
)
def test_scrapy_rejects_private_or_local_targets_before_worker_use(monkeypatch, url):
    def should_not_run(*args, **kwargs):
        pytest.fail("Scrapy worker must not start for a private/local target")

    monkeypatch.setattr(subprocess, "run", should_not_run)
    with pytest.raises(ValueError, match="public|private|non-public"):
        ScrapyConnector().acquire(source_id=uuid4(), context={"start_urls": [url]})


def test_scrapy_start_urls_must_match_explicit_domain_allowlist(monkeypatch):
    def should_not_run(*args, **kwargs):
        pytest.fail("Scrapy worker must not start for a hostname outside the allowlist")

    monkeypatch.setattr(subprocess, "run", should_not_run)
    with pytest.raises(ValueError, match="match context.allowed_domains"):
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={
                "start_urls": ["https://careers.example.test/jobs"],
                "allowed_domains": ["other.example.test"],
            },
        )


def test_scrapy_allows_allowlisted_subdomains_and_normalizes_domain_case(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(json.loads(kwargs["input"]))
        return SimpleNamespace(returncode=0, stdout='{"records":[]}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = ScrapyConnector().acquire(
        source_id=uuid4(),
        context={
            "start_urls": ["https://careers.example.test/jobs"],
            "allowed_domains": ["EXAMPLE.TEST"],
        },
    )
    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert result.records == ()
    assert calls[0]["allowed_domains"] == ["example.test"]


@pytest.fixture(autouse=True)
def deterministic_public_test_dns(monkeypatch):
    original = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        if isinstance(host, str) and host.lower().endswith(".test"):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0))
            ]
        return original(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


def test_scrapy_rejects_hostname_that_resolves_to_private_ip(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.20.30.40", 0))
        ],
    )
    with pytest.raises(ValueError, match="resolves to a private or non-public"):
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["https://jobs.example.test/jobs"]},
        )


def test_scrapy_rejects_hostname_with_mixed_public_and_private_dns_answers(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.168.1.10", 0)),
        ],
    )
    with pytest.raises(ValueError, match="resolves to a private or non-public"):
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["https://jobs.example.test/jobs"]},
        )


def test_scrapy_rejects_unresolvable_hostname_before_worker_use(monkeypatch):
    def fail_resolution(*args, **kwargs):
        raise socket.gaierror("private resolver details")

    monkeypatch.setattr(socket, "getaddrinfo", fail_resolution)
    with pytest.raises(ValueError, match="could not be resolved to a public address"):
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["https://jobs.example.test/jobs"]},
        )


def test_scrapy_checks_parent_allowlist_domains_for_private_dns(monkeypatch):
    def resolve(host, port, *args, **kwargs):
        address = "10.1.2.3" if host == "example.test" else "93.184.216.34"
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 0))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with pytest.raises(ValueError, match="resolves to a private or non-public"):
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={
                "start_urls": ["https://careers.example.test/jobs"],
                "allowed_domains": ["example.test"],
            },
        )


def test_scrapy_rejects_credentials_embedded_in_start_url(monkeypatch):
    def should_not_run(*args, **kwargs):
        pytest.fail("Scrapy worker must not start for credential-bearing URLs")

    monkeypatch.setattr(subprocess, "run", should_not_run)
    with pytest.raises(ValueError, match="embedded credentials"):
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["https://user:secret@example.test/jobs"]},
        )


@pytest.mark.parametrize(
    "selectors",
    [
        {"unknown_selector": "div.job"},
        {"title": ""},
        {"company": 7},
    ],
)
def test_scrapy_rejects_unsupported_or_malformed_selectors(monkeypatch, selectors):
    def should_not_run(*args, **kwargs):
        pytest.fail("Scrapy worker must not start with invalid selectors")

    monkeypatch.setattr(subprocess, "run", should_not_run)
    with pytest.raises(ValueError, match="supported selector names"):
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={
                "start_urls": ["https://example.test/jobs"],
                "selectors": selectors,
            },
        )


def test_scrapy_passes_supported_css_selectors_to_worker(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(json.loads(kwargs["input"]))
        return SimpleNamespace(returncode=0, stdout='{"records":[]}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    selectors = {
        "job_card": "article.job",
        "title": "h2::text",
        "company": ".company::text",
        "location": ".location::text",
        "description": ".description::text",
        "job_url": "a::attr(href)",
        "next_page": "a.next::attr(href)",
    }
    source_id, connector_id = uuid4(), uuid4()
    snapshot = {
        "source_id": str(source_id),
        "connector_id": str(connector_id),
        "source_configuration_sha256": "source-hash",
        "source_base_url": "https://example.test",
        "source_type": "JOB_BOARD",
        "connector_configuration_sha256": "connector-hash",
        "connector_configuration_version": 1,
        "connector_type": "SCRAPY",
        "connector_version": "1.0.0",
    }
    def revalidate(**_kwargs):
        return None
    revalidate.policy_snapshot = snapshot
    result = ScrapyConnector().acquire(
        source_id=source_id,
        context={
            "start_urls": ["https://example.test/jobs"],
            "selectors": selectors,
            "acquisition_policy_revalidator": revalidate,
        },
    )
    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert result.records == ()
    assert calls[0]["selectors"] == selectors
    assert calls[0]["policy_snapshot"] == snapshot
    assert calls[0]["source_id"] == str(source_id)
    assert calls[0]["connector_id"] == str(connector_id)



def test_scrapy_preserves_records_when_worker_reports_page_failures(monkeypatch):
    payload = {
        "records": [{
            "title": "Product Manager",
            "job_url": "https://example.test/job/1",
        }],
        "failures": [{"category": "SOURCE_RESTRICTED", "retryable": False}],
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    result = ScrapyConnector().acquire(
        source_id=uuid4(),
        context={"start_urls": ["https://example.test/jobs"]},
    )

    assert result.status == AcquisitionOutcome.PARTIAL
    assert len(result.records) == 1
    assert result.error_category == "SOURCE_RESTRICTED"
    assert result.failures[0].category == "SOURCE_RESTRICTED"


def test_scrapy_reports_restricted_outcome_when_all_pages_are_blocked(monkeypatch):
    payload = {
        "records": [],
        "failures": [{"category": "SOURCE_RESTRICTED", "retryable": False}],
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    result = ScrapyConnector().acquire(
        source_id=uuid4(),
        context={"start_urls": ["https://example.test/jobs"]},
    )

    assert result.status == AcquisitionOutcome.RESTRICTED
    assert result.records == ()



def test_scrapy_caps_worker_records_and_reports_partial_result(monkeypatch):
    payload = {
        "records": [
            {"title": "Product Manager", "job_url": f"https://example.test/jobs/{index}"}
            for index in range(501)
        ],
        "failures": [],
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    result = ScrapyConnector().acquire(
        source_id=uuid4(),
        context={"start_urls": ["https://example.test/jobs"]},
    )

    assert result.status == AcquisitionOutcome.PARTIAL
    assert len(result.records) == ScrapyConnector.MAX_ITEMS
    assert result.error_category == "MAX_ITEMS_LIMIT_REACHED"

def test_scrapy_middleware_blocks_forbidden_redirect_target_before_network(monkeypatch):
    fake_scrapy = ModuleType("scrapy")
    fake_scrapy.__path__ = []
    fake_exceptions = ModuleType("scrapy.exceptions")

    class IgnoreRequest(Exception):
        pass

    fake_exceptions.IgnoreRequest = IgnoreRequest
    fake_scrapy.exceptions = fake_exceptions
    monkeypatch.setitem(__import__("sys").modules, "scrapy", fake_scrapy)
    monkeypatch.setitem(__import__("sys").modules, "scrapy.exceptions", fake_exceptions)

    spider = SimpleNamespace(_policy_failures=[])
    middleware = ForbiddenTargetMiddleware([{
        "id": "deny-private-path",
        "rule_type": "URL_PATH_PREFIX",
        "pattern": "https://jobs.example.test/private",
        "connector_scope": "ALL",
        "reason": "Unapproved path",
        "active": True,
    }])

    assert middleware.process_request(
        SimpleNamespace(url="https://jobs.example.test/jobs/next"), spider
    ) is None
    with pytest.raises(IgnoreRequest):
        middleware.process_request(
            SimpleNamespace(url="https://jobs.example.test/private/redirected"), spider
        )
    assert spider._policy_failures == [{
        "category": "FORBIDDEN_TARGET_RULE_MATCHED",
        "retryable": False,
    }]



def test_scrapy_middleware_revalidates_live_policy_before_each_request(monkeypatch):
    fake_scrapy = ModuleType("scrapy")
    fake_scrapy.__path__ = []
    fake_exceptions = ModuleType("scrapy.exceptions")

    class IgnoreRequest(Exception):
        pass

    fake_exceptions.IgnoreRequest = IgnoreRequest
    fake_scrapy.exceptions = fake_exceptions
    monkeypatch.setitem(__import__("sys").modules, "scrapy", fake_scrapy)
    monkeypatch.setitem(__import__("sys").modules, "scrapy.exceptions", fake_exceptions)

    calls = []
    def revalidate(**kwargs):
        calls.append(kwargs)
        if kwargs["url"].endswith("/blocked"):
            raise AcquisitionAdapterError(
                AcquisitionOutcome.RESTRICTED, "FORBIDDEN_TARGET_RULE_MATCHED"
            )

    spider = SimpleNamespace(_policy_failures=[], _policy_revalidator=revalidate)
    middleware = ForbiddenTargetMiddleware([])
    assert middleware.process_request(
        SimpleNamespace(url="https://example.test/first", method="GET"), spider
    ) is None
    with pytest.raises(IgnoreRequest):
        middleware.process_request(
            SimpleNamespace(url="https://example.test/blocked", method="GET"), spider
        )
    assert len(calls) == 2
    assert calls[1]["url"] == "https://example.test/blocked"
    assert spider._policy_failures == [{
        "category": "FORBIDDEN_TARGET_RULE_MATCHED", "retryable": False
    }]


def test_scrapy_worker_builds_live_guard_from_persisted_policy(monkeypatch):
    from app.application.jobs import registered_acquisition
    from app.infrastructure.db import session as db_session
    from app.infrastructure.db.repositories import acquisition as acquisition_repository

    source_id, connector_id = uuid4(), uuid4()
    expected_snapshot = {"source_id": str(source_id), "connector_id": str(connector_id)}
    fake_session = object()
    source, connector = object(), object()
    lookups = []
    calls = []

    class FakeRepository:
        def __init__(self, session):
            assert session is fake_session
        def get_source(self, value):
            lookups.append(("source", value))
            return source
        def get_connector(self, value):
            lookups.append(("connector", value))
            return connector

    def fake_builder(repository, sid, cid, current_source, current_connector):
        assert isinstance(repository, FakeRepository)
        assert (sid, cid, current_source, current_connector) == (
            source_id, connector_id, source, connector
        )
        def revalidate(**kwargs):
            calls.append(kwargs)
        revalidate.policy_snapshot = expected_snapshot
        return revalidate

    monkeypatch.setattr(db_session, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        acquisition_repository, "SqlAlchemyAcquisitionRepository", FakeRepository
    )
    monkeypatch.setattr(
        registered_acquisition, "build_acquisition_policy_revalidator", fake_builder
    )

    session, guard, error = _build_worker_policy_guard({
        "policy_snapshot": expected_snapshot,
        "source_id": str(source_id),
        "connector_id": str(connector_id),
    })
    assert session is fake_session
    assert error is None
    guard(url="https://example.test/one", connector_type="SCRAPY", method="GET")
    guard(url="https://example.test/two", connector_type="SCRAPY", method="GET")
    assert len(lookups) == 2
    assert [call["url"] for call in calls] == [
        "https://example.test/one", "https://example.test/two"
    ]


def test_scrapy_worker_fails_closed_when_persisted_snapshot_changed(monkeypatch):
    from app.application.jobs import registered_acquisition
    from app.infrastructure.db import session as db_session
    from app.infrastructure.db.repositories import acquisition as acquisition_repository

    source_id, connector_id = uuid4(), uuid4()
    fake_session = object()

    class FakeRepository:
        def __init__(self, session):
            assert session is fake_session
        def get_source(self, _value):
            return object()
        def get_connector(self, _value):
            return object()

    def fake_builder(*_args):
        def revalidate(**_kwargs):
            pytest.fail("a mismatched snapshot must fail before request policy callback")
        revalidate.policy_snapshot = {"changed": True}
        return revalidate

    monkeypatch.setattr(db_session, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        acquisition_repository, "SqlAlchemyAcquisitionRepository", FakeRepository
    )
    monkeypatch.setattr(
        registered_acquisition, "build_acquisition_policy_revalidator", fake_builder
    )
    session, guard, error = _build_worker_policy_guard({
        "policy_snapshot": {"expected": True},
        "source_id": str(source_id),
        "connector_id": str(connector_id),
    })
    assert session is fake_session
    assert error == "ACQUISITION_CONFIGURATION_CHANGED"
    with pytest.raises(AcquisitionAdapterError, match="ACQUISITION_CONFIGURATION_CHANGED"):
        guard(url="https://example.test/one", connector_type="SCRAPY", method="GET")


def test_scrapy_worker_guard_blocks_request_after_live_rule_is_added(monkeypatch):
    from app.application.jobs.registered_acquisition import build_acquisition_policy_revalidator
    from app.infrastructure.db import session as db_session
    from app.infrastructure.db.repositories import acquisition as acquisition_repository

    source_id, connector_id = uuid4(), uuid4()
    source = SimpleNamespace(
        id=source_id, status="ACTIVE", permission_review_status="APPROVED",
        permission_review_reference="reviewed terms", permission_reviewed_at=object(),
        configuration={}, base_url="https://example.test", source_type="JOB_BOARD",
    )
    connector = SimpleNamespace(
        id=connector_id, source_id=source_id, status="ACTIVE", configuration={},
        active_configuration_version=1, connector_type="SCRAPY", connector_version="1.0.0",
    )
    repositories = []
    fake_session = object()

    class FakeRepository:
        def __init__(self, session):
            assert session is fake_session
            self.rules = []
            repositories.append(self)
        def get_source(self, _source_id):
            return source
        def get_connector(self, _connector_id):
            return connector
        def list_active_forbidden_target_rules(self):
            return list(self.rules)

    monkeypatch.setattr(db_session, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        acquisition_repository, "SqlAlchemyAcquisitionRepository", FakeRepository
    )
    expected = build_acquisition_policy_revalidator(
        FakeRepository(fake_session), source_id, connector_id, source, connector
    ).policy_snapshot
    session, guard, error = _build_worker_policy_guard({
        "policy_snapshot": expected,
        "source_id": str(source_id),
        "connector_id": str(connector_id),
    })
    assert session is fake_session
    assert error is None
    network_requests = 0
    guard(url="https://example.test/jobs/1", connector_type="SCRAPY", method="GET")
    network_requests += 1

    repositories[-1].rules = [{
        "id": "new-rule", "rule_type": "HOST_EXACT",
        "pattern": "blocked.example.test", "connector_scope": "SCRAPY",
        "reason": "Policy changed during crawl", "active": True,
    }]
    with pytest.raises(AcquisitionAdapterError, match="FORBIDDEN_TARGET_RULE_MATCHED"):
        guard(url="https://blocked.example.test/jobs/2", connector_type="SCRAPY", method="GET")
    assert network_requests == 1
