"""Scrapy crawl implementation used only by the one-shot worker process."""
import ipaddress
from typing import Any

from app.application.jobs.target_policy import ForbiddenTargetError, assert_target_not_forbidden
from app.ports.acquisition import AcquisitionAdapterError, AcquisitionOutcome

from twisted.internet.interfaces import IResolverSimple
from zope.interface import implementer
from scrapy.downloadermiddlewares.redirect import RedirectMiddleware


def _require_public_resolved_address(address: str) -> str:
    """Reject a private DNS answer at the point the crawler resolves its connection."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError as exc:
        raise ValueError("Scrapy DNS resolution returned an invalid address") from exc
    if not parsed.is_global:
        raise ValueError("Scrapy DNS resolution returned a private or non-public address")
    return str(parsed)


@implementer(IResolverSimple)
class PublicOnlyCachingThreadedResolver:
    """Scrapy resolver that validates the IP actually returned for each connection."""

    def __init__(self, reactor, cache_size: int = 10000, timeout: int = 60):
        from scrapy.resolver import CachingThreadedResolver

        self._reactor = reactor
        self._resolver = CachingThreadedResolver(reactor, cache_size, timeout)

    @classmethod
    def from_crawler(cls, crawler, reactor):
        """Build through Scrapy's resolver lifecycle with its configured DNS limits."""
        settings = crawler.settings
        cache_size = settings.getint("DNSCACHE_SIZE") if settings.getbool("DNSCACHE_ENABLED") else 0
        return cls(
            reactor,
            cache_size=cache_size,
            timeout=settings.getfloat("DNS_TIMEOUT"),
        )

    def install_on_reactor(self) -> None:
        self._reactor.installResolver(self)

    def getHostByName(self, name: str, timeout=()):
        deferred = self._resolver.getHostByName(name, timeout)
        deferred.addCallback(_require_public_resolved_address)
        return deferred


def _is_transient_scrapy_failure(failure) -> bool:
    """Identify transport failures that are safe to retry within bounded limits."""
    value = getattr(failure, "value", None)
    if value is None:
        return False
    try:
        from twisted.internet import error as twisted_error

        transient_types = tuple(
            candidate
            for name in (
                "TimeoutError",
                "DNSLookupError",
                "TCPTimedOutError",
                "ConnectionRefusedError",
                "ConnectError",
                "ConnectionLost",
                "NoRouteError",
            )
            if isinstance((candidate := getattr(twisted_error, name, None)), type)
        )
        if transient_types and isinstance(value, transient_types):
            return True
    except ImportError:
        pass
    exception_name = type(value).__name__.casefold()
    return any(
        marker in exception_name
        for marker in (
            "timeout",
            "dnslookup",
            "connectionrefused",
            "connecterror",
            "connectionlost",
            "networkunreachable",
            "responsefailed",
        )
    )


class RecordingRedirectMiddleware(RedirectMiddleware):
    """Persist a safe redirect-limit category before Scrapy discards the response."""

    def process_response(self, request, response, spider):
        redirect_statuses = {301, 302, 303, 307, 308}
        redirect_times = request.meta.get("redirect_times", 0)
        if response.status in redirect_statuses and redirect_times >= self.max_redirect_times:
            failures = getattr(spider, "_policy_failures", None)
            if isinstance(failures, list) and not any(
                isinstance(item, dict) and item.get("category") == "SCRAPY_REDIRECT_LIMIT_REACHED"
                for item in failures
            ):
                failures.append({"category": "SCRAPY_REDIRECT_LIMIT_REACHED", "retryable": False})
        return super().process_response(request, response, spider)


class ForbiddenTargetMiddleware:
    """Reject forbidden destinations before Scrapy sends initial, redirect or follow-up requests."""

    def __init__(self, rules: list[dict[str, Any]]) -> None:
        self.rules = rules

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings.get("JOBPUNTER_FORBIDDEN_TARGET_RULES", []))

    def process_request(self, request, spider):
        from scrapy.exceptions import IgnoreRequest

        try:
            policy_revalidator = getattr(spider, "_policy_revalidator", None)
            if callable(policy_revalidator):
                policy_revalidator(
                    url=request.url,
                    connector_type="SCRAPY",
                    method=request.method,
                )
            else:
                assert_target_not_forbidden(
                    request.url, self.rules, connector_type="SCRAPY"
                )
        except AcquisitionAdapterError as exc:
            self._record_failure(spider, exc.error_category)
            raise IgnoreRequest("live_acquisition_policy_rejected_request")
        except ForbiddenTargetError:
            self._record_failure(spider, "FORBIDDEN_TARGET_RULE_MATCHED")
            raise IgnoreRequest("forbidden_target_rule_matched")
        except ValueError:
            self._record_failure(spider, "TARGET_POLICY_INVALID")
            raise IgnoreRequest("target_policy_invalid")
        return None

    @staticmethod
    def _record_failure(spider, category: str) -> None:
        failures = getattr(spider, "_policy_failures", None)
        if isinstance(failures, list) and not any(
            isinstance(item, dict) and item.get("category") == category
            for item in failures
        ):
            failures.append({"category": category, "retryable": False})


def _build_worker_policy_guard(config: dict[str, Any]):
    """Create a DB-backed request guard, failing closed when live policy cannot be loaded."""
    expected_snapshot = config.get("policy_snapshot")
    if expected_snapshot is None:
        return None, None, None

    session = None
    revalidator = None
    policy_error = None
    try:
        from uuid import UUID
        from app.application.jobs.registered_acquisition import (
            build_acquisition_policy_revalidator,
        )
        from app.infrastructure.db.repositories.acquisition import (
            SqlAlchemyAcquisitionRepository,
        )
        from app.infrastructure.db.session import SessionLocal

        session = SessionLocal()
        repository = SqlAlchemyAcquisitionRepository(session)
        source_id = UUID(config["source_id"])
        connector_id = UUID(config["connector_id"])
        source = repository.get_source(source_id)
        connector = repository.get_connector(connector_id)
        if source is None or connector is None:
            policy_error = "ACQUISITION_POLICY_SUBJECT_MISSING"
        else:
            revalidator = build_acquisition_policy_revalidator(
                repository, source_id, connector_id, source, connector
            )
            if revalidator.policy_snapshot != expected_snapshot:
                policy_error = "ACQUISITION_CONFIGURATION_CHANGED"
    except Exception:
        policy_error = "LIVE_ACQUISITION_POLICY_UNAVAILABLE"

    def enforce_live_policy(*, url: str, connector_type: str, method: str) -> None:
        if policy_error:
            raise AcquisitionAdapterError(AcquisitionOutcome.RESTRICTED, policy_error)
        if revalidator is None:
            raise AcquisitionAdapterError(
                AcquisitionOutcome.RESTRICTED, "LIVE_ACQUISITION_POLICY_UNAVAILABLE"
            )
        revalidator(url=url, connector_type=connector_type, method=method)

    return session, enforce_live_policy, policy_error



def crawl(config: dict[str, Any]) -> dict[str, Any]:
    import scrapy
    from scrapy.crawler import CrawlerProcess

    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    start_urls = config["start_urls"]
    rules = config.get("forbidden_target_rules", [])
    if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
        raise ValueError("forbidden target policy is invalid")

    policy_session, worker_policy_guard, _policy_error = _build_worker_policy_guard(config)

    selectors = config["selectors"]
    allowed_domains = config["allowed_domains"]
    max_pages = config["max_pages"]
    max_items = config.get("max_items", 500)
    if len(start_urls) > max_pages:
        failures.append({"category": "MAX_PAGES_LIMIT_REACHED", "retryable": False})
    delay = config["download_delay_seconds"]
    user_agent = config["user_agent"]

    class ConfiguredJobsSpider(scrapy.Spider):
        name = "jobpunter_configured_jobs"
        custom_settings = {
            "ROBOTSTXT_OBEY": True,
            "DOWNLOAD_DELAY": float(delay),
            "AUTOTHROTTLE_ENABLED": True,
            "AUTOTHROTTLE_START_DELAY": float(delay),
            "AUTOTHROTTLE_MAX_DELAY": 30.0,
            "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
            "RETRY_ENABLED": True,
            "RETRY_TIMES": 2,
            # Pin Scrapy's bounded default so redirect loops stay deterministic across upgrades.
            "REDIRECT_ENABLED": True,
            "REDIRECT_MAX_TIMES": 20,
            "USER_AGENT": user_agent,
            "LOG_LEVEL": "WARNING",
            "TELNETCONSOLE_ENABLED": False,
            "TWISTED_DNS_RESOLVER": "app.infrastructure.acquisition.scrapy_worker_runtime.PublicOnlyCachingThreadedResolver",
            "HTTPPROXY_ENABLED": False,
            "DOWNLOAD_MAXSIZE": 2_000_000,
            "DOWNLOADER_MIDDLEWARES": {
                "scrapy.downloadermiddlewares.redirect.RedirectMiddleware": None,
                "app.infrastructure.acquisition.scrapy_worker_runtime.RecordingRedirectMiddleware": 600,
                "app.infrastructure.acquisition.scrapy_worker_runtime.ForbiddenTargetMiddleware": 950,
            },
            "JOBPUNTER_FORBIDDEN_TARGET_RULES": rules,
        }
        _count = 0

        def start_requests(self):
            for url in start_urls[:max_pages]:
                try:
                    if callable(self._policy_revalidator):
                        self._policy_revalidator(
                            url=url, connector_type="SCRAPY", method="GET"
                        )
                    else:
                        assert_target_not_forbidden(url, rules, connector_type="SCRAPY")
                except AcquisitionAdapterError as exc:
                    failures.append({"category": exc.error_category, "retryable": False})
                    continue
                except ForbiddenTargetError:
                    failures.append({"category": "FORBIDDEN_TARGET_RULE_MATCHED", "retryable": False})
                    continue
                except ValueError:
                    failures.append({"category": "TARGET_POLICY_INVALID", "retryable": False})
                    continue
                yield scrapy.Request(url, callback=self.parse, errback=self.errback)

        def errback(self, failure):
            response = getattr(failure.value, "response", None)
            status = getattr(response, "status", None)
            request = getattr(failure, "request", None)
            request_meta = getattr(request, "meta", {}) or {}
            redirect_times = request_meta.get("redirect_times", 0)
            failure_message = str(failure.value).casefold()
            redirect_limit_reached = (
                redirect_times >= 20
                or ("redirect" in failure_message and ("max" in failure_message or "limit" in failure_message))
            )
            if status in {401, 403, 407, 429}:
                category, retryable = "SOURCE_RESTRICTED", status == 429
            elif status in {408, 425} or (isinstance(status, int) and status >= 500):
                category, retryable = "UPSTREAM_UNAVAILABLE", True
            elif status in {301, 302, 303, 307, 308} or redirect_limit_reached:
                category, retryable = "SCRAPY_REDIRECT_LIMIT_REACHED", False
            elif _is_transient_scrapy_failure(failure):
                category, retryable = "UPSTREAM_UNAVAILABLE", True
            else:
                category, retryable = "SCRAPY_REQUEST_FAILED", False
            failures.append({"category": category, "retryable": retryable})

        def parse(self, response):
            self._count += 1
            items = response.css(selectors.get("job_card", "article"))
            for item in items:
                raw: dict[str, Any] = {
                    "source_page_url": response.url,
                    "title": self._extract(item, selectors.get("title")),
                    "company": self._extract(item, selectors.get("company")),
                    "location": self._extract(item, selectors.get("location")),
                    "description": self._extract(item, selectors.get("description")),
                }
                link_selector = selectors.get("job_url", "a::attr(href)")
                link = item.css(link_selector).get()
                raw["job_url"] = response.urljoin(link) if link else response.url
                records.append({key: value for key, value in raw.items() if value is not None})
                if len(records) >= max_items:
                    failures.append({"category": "MAX_ITEMS_LIMIT_REACHED", "retryable": False})
                    return
            next_selector = selectors.get("next_page", "a[rel='next']::attr(href)")
            next_link = response.css(next_selector).get()
            if self._count >= max_pages:
                if next_link:
                    failures.append({"category": "MAX_PAGES_LIMIT_REACHED", "retryable": False})
                return
            if next_link:
                candidate_url = response.urljoin(next_link)
                try:
                    if callable(self._policy_revalidator):
                        self._policy_revalidator(
                            url=candidate_url, connector_type="SCRAPY", method="GET"
                        )
                    else:
                        assert_target_not_forbidden(
                            candidate_url, rules, connector_type="SCRAPY"
                        )
                except AcquisitionAdapterError as exc:
                    failures.append({"category": exc.error_category, "retryable": False})
                    return
                except ForbiddenTargetError:
                    failures.append({"category": "FORBIDDEN_TARGET_RULE_MATCHED", "retryable": False})
                    return
                except ValueError:
                    failures.append({"category": "TARGET_POLICY_INVALID", "retryable": False})
                    return
                yield scrapy.Request(candidate_url, callback=self.parse, errback=self.errback)

        @staticmethod
        def _extract(item, selector):
            if not selector:
                return None
            values = item.css(selector).getall()
            cleaned = [" ".join(value.split()) for value in values if value and value.strip()]
            return " ".join(cleaned) if cleaned else None

    # Assign closure-derived settings after class creation: class-body assignments with
    # the same names shadow the enclosing locals and raise NameError.
    ConfiguredJobsSpider.start_urls = start_urls
    ConfiguredJobsSpider.allowed_domains = allowed_domains
    ConfiguredJobsSpider._policy_failures = failures
    ConfiguredJobsSpider._forbidden_target_rules = rules
    ConfiguredJobsSpider._policy_revalidator = staticmethod(worker_policy_guard)

    # DNS_RESOLVER is read by CrawlerProcess.start() before spider settings are
    # applied. Keep the security-critical resolver and download limits at process level.
    process = CrawlerProcess(settings={
        "TWISTED_DNS_RESOLVER": "app.infrastructure.acquisition.scrapy_worker_runtime.PublicOnlyCachingThreadedResolver",
        "HTTPPROXY_ENABLED": False,
        "DOWNLOAD_MAXSIZE": 2_000_000,
    })
    try:
        process.crawl(ConfiguredJobsSpider)
        process.start(stop_after_crawl=True)
        return {"records": records, "failures": failures}
    finally:
        if policy_session is not None:
            policy_session.close()
