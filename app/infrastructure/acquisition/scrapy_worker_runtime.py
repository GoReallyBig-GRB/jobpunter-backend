"""Scrapy crawl implementation used only by the one-shot worker process."""
from typing import Any


def crawl(config: dict[str, Any]) -> dict[str, Any]:
    import scrapy
    from scrapy.crawler import CrawlerProcess

    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    start_urls = config["start_urls"]
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
        }
        _count = 0

        def start_requests(self):
            for url in start_urls[:max_pages]:
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
                yield response.follow(next_link, callback=self.parse, errback=self.errback)

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

    process = CrawlerProcess()
    process.crawl(ConfiguredJobsSpider)
    process.start(stop_after_crawl=True)
    return {"records": records, "failures": failures}
