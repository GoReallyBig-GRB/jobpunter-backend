"""Scrapy crawl implementation used only by the one-shot worker process."""
import ipaddress
from typing import Any

from app.application.jobs.target_policy import ForbiddenTargetError, assert_target_not_forbidden

from twisted.internet.interfaces import IResolverSimple
from zope.interface import implementer


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
            assert_target_not_forbidden(
                request.url, self.rules, connector_type="SCRAPY"
            )
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


def crawl(config: dict[str, Any]) -> dict[str, Any]:
    import scrapy
    from scrapy.crawler import CrawlerProcess

    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    start_urls = config["start_urls"]
    rules = config.get("forbidden_target_rules", [])
    if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
        raise ValueError("forbidden target policy is invalid")
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
            "USER_AGENT": user_agent,
            "LOG_LEVEL": "WARNING",
            "TELNETCONSOLE_ENABLED": False,
            "TWISTED_DNS_RESOLVER": "app.infrastructure.acquisition.scrapy_worker_runtime.PublicOnlyCachingThreadedResolver",
            "HTTPPROXY_ENABLED": False,
            "DOWNLOAD_MAXSIZE": 2_000_000,
            "DOWNLOADER_MIDDLEWARES": {
                "app.infrastructure.acquisition.scrapy_worker_runtime.ForbiddenTargetMiddleware": 50,
            },
            "JOBPUNTER_FORBIDDEN_TARGET_RULES": rules,
        }
        _count = 0

        def start_requests(self):
            for url in start_urls[:max_pages]:
                try:
                    assert_target_not_forbidden(url, rules, connector_type="SCRAPY")
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
            if status in {401, 403, 407, 429}:
                category, retryable = "SOURCE_RESTRICTED", status == 429
            elif status in {408, 425} or (isinstance(status, int) and status >= 500):
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
                    assert_target_not_forbidden(
                        candidate_url, rules, connector_type="SCRAPY"
                    )
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

    # DNS_RESOLVER is read by CrawlerProcess.start() before spider settings are
    # applied. Keep the security-critical resolver and download limits at process level.
    process = CrawlerProcess(settings={
        "TWISTED_DNS_RESOLVER": "app.infrastructure.acquisition.scrapy_worker_runtime.PublicOnlyCachingThreadedResolver",
        "HTTPPROXY_ENABLED": False,
        "DOWNLOAD_MAXSIZE": 2_000_000,
    })
    process.crawl(ConfiguredJobsSpider)
    process.start(stop_after_crawl=True)
    return {"records": records, "failures": failures}
