"""Configurable Scrapy connector for public, permitted job-listing pages.

This connector uses conservative crawling defaults and honors robots.txt. It does not
attempt to evade anti-bot controls, CAPTCHAs, authentication, or access restrictions.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from app.ports.acquisition import RawAcquisition


class ScrapyConnector:
    connector_type = "SCRAPY"
    connector_version = "1.0.0"

    def acquire(self, *, source_id: UUID, context: dict[str, Any]) -> list[RawAcquisition]:
        start_urls = context.get("start_urls")
        if not isinstance(start_urls, list) or not start_urls:
            raise ValueError("context.start_urls must be a non-empty list of public HTTP(S) URLs")
        for url in start_urls:
            if not isinstance(url, str) or urlparse(url).scheme not in {"http", "https"} or not urlparse(url).netloc:
                raise ValueError("every start_urls entry must be an absolute HTTP(S) URL")

        selectors = context.get("selectors", {})
        if not isinstance(selectors, dict):
            raise ValueError("context.selectors must be an object of CSS selectors")
        allowed_domains = context.get("allowed_domains")
        if allowed_domains is None:
            allowed_domains = sorted({urlparse(url).hostname for url in start_urls if urlparse(url).hostname})
        if not isinstance(allowed_domains, list) or not allowed_domains or any(not isinstance(x, str) or not x.strip() for x in allowed_domains):
            raise ValueError("context.allowed_domains must be a non-empty list of domain names")

        try:
            import scrapy
            from scrapy.crawler import CrawlerProcess
        except ImportError as exc:
            raise RuntimeError("Install the standard Scrapy dependency to enable this connector.") from exc

        max_pages = context.get("max_pages", 50)
        if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= 500:
            raise ValueError("context.max_pages must be an integer from 1 to 500")
        delay = context.get("download_delay_seconds", 2.0)
        if not isinstance(delay, (int, float)) or isinstance(delay, bool) or not 1 <= delay <= 30:
            raise ValueError("context.download_delay_seconds must be between 1 and 30")
        user_agent = context.get("user_agent", "JobPunterResearchBot/1.0 (+contact configured by operator)")
        if not isinstance(user_agent, str) or len(user_agent.strip()) < 8:
            raise ValueError("context.user_agent must identify the client")

        records: list[dict[str, Any]] = []
        selector_map = {str(k): str(v) for k, v in selectors.items() if isinstance(v, str) and v.strip()}

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
            start_urls = start_urls
            allowed_domains = allowed_domains
            _count = 0

            def parse(self, response):
                self._count += 1
                items = response.css(selector_map.get("job_card", "article"))
                for item in items:
                    raw: dict[str, Any] = {
                        "source_page_url": response.url,
                        "title": self._extract(item, selector_map.get("title")),
                        "company": self._extract(item, selector_map.get("company")),
                        "location": self._extract(item, selector_map.get("location")),
                        "description": self._extract(item, selector_map.get("description")),
                    }
                    link_selector = selector_map.get("job_url", "a::attr(href)")
                    link = item.css(link_selector).get()
                    raw["job_url"] = response.urljoin(link) if link else response.url
                    raw = {key: value for key, value in raw.items() if value is not None}
                    records.append(raw)
                if self._count >= max_pages:
                    return
                next_selector = selector_map.get("next_page", "a[rel='next']::attr(href)")
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

        process = CrawlerProcess()
        process.crawl(ConfiguredJobsSpider)
        process.start(stop_after_crawl=True)

        observed_at = datetime.now(timezone.utc)
        result: list[RawAcquisition] = []
        for raw in records:
            raw["connector"] = self.connector_type
            raw["connector_version"] = self.connector_version
            serialized = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            result.append(RawAcquisition(
                source_id=source_id,
                external_record_id=None,
                source_url=str(raw.get("job_url") or raw.get("source_page_url")),
                raw_content=raw,
                content_hash=digest,
                observed_at=observed_at,
            ))
        return result
