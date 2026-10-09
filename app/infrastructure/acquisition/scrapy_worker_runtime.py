"""Scrapy crawl implementation used only by the one-shot worker process."""
from typing import Any


def crawl(config: dict[str, Any]) -> list[dict[str, Any]]:
    import scrapy
    from scrapy.crawler import CrawlerProcess

    records: list[dict[str, Any]] = []
    start_urls = config["start_urls"]
    selectors = config["selectors"]
    allowed_domains = config["allowed_domains"]
    max_pages = config["max_pages"]
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
            if self._count >= max_pages:
                return
            next_selector = selectors.get("next_page", "a[rel='next']::attr(href)")
            next_link = response.css(next_selector).get()
            if next_link:
                yield response.follow(next_link, callback=self.parse)

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
    return records
