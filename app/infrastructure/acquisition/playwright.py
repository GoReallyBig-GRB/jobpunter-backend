"""Playwright connector for permitted, JavaScript-rendered job pages.

Uses an identified client in headless mode. It does not spoof browser fingerprints, hide
automation, solve CAPTCHAs, or bypass anti-bot/access controls. Install the browser runtime
separately with `playwright install chromium`.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import UUID

from app.ports.acquisition import RawAcquisition


def _host_is_allowed(url: str, allowed_hosts: set[str]) -> bool:
    """Return whether a URL resolves to an explicitly allowed hostname."""
    hostname = urlparse(url).hostname
    return hostname is not None and hostname.lower() in allowed_hosts


class PlaywrightConnector:
    connector_type = "PLAYWRIGHT"
    connector_version = "1.0.0"

    def acquire(self, *, source_id: UUID, context: dict[str, Any]) -> list[RawAcquisition]:
        start_urls = context.get("start_urls")
        if not isinstance(start_urls, list) or not start_urls:
            raise ValueError("context.start_urls must be a non-empty list of public HTTP(S) URLs")

        hosts: set[str] = set()
        for url in start_urls:
            parsed = urlparse(url) if isinstance(url, str) else None
            if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("every start_urls entry must be an absolute HTTP(S) URL")
            hosts.add(parsed.hostname.lower())

        allowed_domains = context.get("allowed_domains", sorted(hosts))
        if (
            not isinstance(allowed_domains, list)
            or not allowed_domains
            or any(not isinstance(host, str) or not host.strip() for host in allowed_domains)
        ):
            raise ValueError("context.allowed_domains must be a non-empty list of domain names")
        allowed_hosts = {host.strip().lower().lstrip(".") for host in allowed_domains}
        if not hosts.issubset(allowed_hosts):
            raise ValueError("every start URL host must be included in allowed_domains")

        selectors = context.get("selectors", {})
        if not isinstance(selectors, dict):
            raise ValueError("context.selectors must be an object of CSS selectors")
        selector_map = {
            str(key): value.strip()
            for key, value in selectors.items()
            if isinstance(value, str) and value.strip()
        }

        max_pages = context.get("max_pages", len(start_urls))
        if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= 100:
            raise ValueError("context.max_pages must be an integer from 1 to 100")
        timeout_ms = context.get("timeout_ms", 20000)
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or not 1000 <= timeout_ms <= 60000:
            raise ValueError("context.timeout_ms must be an integer from 1000 to 60000")
        delay = context.get("request_delay_seconds", 2.0)
        if not isinstance(delay, (int, float)) or isinstance(delay, bool) or not 1 <= delay <= 30:
            raise ValueError("context.request_delay_seconds must be between 1 and 30")
        user_agent = context.get(
            "user_agent", "JobPunterResearchBot/1.0 (identified job research client)"
        )
        if not isinstance(user_agent, str) or len(user_agent.strip()) < 8:
            raise ValueError("context.user_agent must identify the client")

        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Install the Playwright dependency and browser runtime to enable this connector."
            ) from exc

        records: list[dict[str, Any]] = []
        observed_at = datetime.now(timezone.utc)
        visited: set[str] = set()
        queue = list(start_urls)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context_options = {
                    "user_agent": user_agent,
                    "java_script_enabled": True,
                    "accept_downloads": False,
                }
                context_options["service_workers"] = "block"
                browser_context = browser.new_context(**context_options)

                # Enforce the allowlist at request time, not only after navigation. This
                # blocks off-domain redirects and subresource requests before they are sent.
                def enforce_allowed_host(route) -> None:
                    if _host_is_allowed(route.request.url, allowed_hosts):
                        route.continue_()
                    else:
                        route.abort()

                browser_context.route("**/*", enforce_allowed_host)
                page = browser_context.new_page()
                page.set_default_timeout(timeout_ms)

                while queue and len(visited) < max_pages:
                    url = queue.pop(0)
                    if url in visited:
                        continue
                    if not _host_is_allowed(url, allowed_hosts):
                        continue
                    visited.add(url)
                    try:
                        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                        # A permitted start URL may redirect off-domain. Validate the final page
                        # URL before reading any content or following any links.
                        if not _host_is_allowed(page.url, allowed_hosts):
                            continue
                        if response is not None and response.status in {401, 403, 407, 429}:
                            # Treat access/rate-limit responses as blocked; do not retry or evade.
                            continue
                        if response is not None and response.status >= 400:
                            continue
                        if selector_map.get("ready"):
                            page.locator(selector_map["ready"]).first.wait_for(state="visible")
                        card_selector = selector_map.get("job_card", "article")
                        cards = page.locator(card_selector)
                        for index in range(cards.count()):
                            card = cards.nth(index)

                            def extract(key: str) -> str | None:
                                selector = selector_map.get(key)
                                if not selector:
                                    return None
                                locator = card.locator(selector).first
                                try:
                                    value = locator.inner_text().strip()
                                except PlaywrightTimeoutError:
                                    return None
                                return " ".join(value.split()) or None

                            job_url = None
                            link_selector = selector_map.get("job_url", "a")
                            link = card.locator(link_selector).first
                            if link.count():
                                href = link.get_attribute("href")
                                if href:
                                    candidate_job_url = urljoin(page.url, href)
                                    if _host_is_allowed(candidate_job_url, allowed_hosts):
                                        job_url = candidate_job_url
                            raw: dict[str, Any] = {
                                "source_page_url": page.url,
                                "title": extract("title"),
                                "company": extract("company"),
                                "location": extract("location"),
                                "description": extract("description"),
                                "job_url": job_url or page.url,
                                "connector": self.connector_type,
                                "connector_version": self.connector_version,
                            }
                            records.append({key: value for key, value in raw.items() if value is not None})

                        next_selector = selector_map.get("next_page")
                        if next_selector and len(visited) + len(queue) < max_pages:
                            next_link = page.locator(next_selector).first
                            if next_link.count():
                                href = next_link.get_attribute("href")
                                if href:
                                    next_url = urljoin(page.url, href)
                                    if _host_is_allowed(next_url, allowed_hosts) and next_url not in visited:
                                        queue.append(next_url)
                    except PlaywrightTimeoutError:
                        # One page timing out does not invalidate records from other pages.
                        continue
                    if queue and len(visited) < max_pages:
                        time.sleep(float(delay))
                browser_context.close()
            finally:
                browser.close()

        result: list[RawAcquisition] = []
        for raw in records:
            serialized = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            result.append(
                RawAcquisition(
                    source_id=source_id,
                    external_record_id=None,
                    source_url=str(raw.get("job_url") or raw.get("source_page_url")),
                    raw_content=raw,
                    content_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
                    observed_at=observed_at,
                )
            )
        return result
