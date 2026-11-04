"""Playwright connector for permitted, JavaScript-rendered job pages.

Uses an identified client in headless mode. It does not spoof browser fingerprints, hide
automation, solve CAPTCHAs, or bypass anti-bot/access controls. Install the browser runtime
separately with `playwright install chromium`.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import UUID

from app.application.jobs.target_policy import ForbiddenTargetError, assert_current_target_not_forbidden
from app.ports.acquisition import (
    AcquisitionAdapterError,
    AcquisitionBatchResult,
    AcquisitionFailure,
    AcquisitionOutcome,
    RawAcquisition,
)


_LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


def _public_address_for_host(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Resolve a configured host and reject every non-public DNS answer."""
    normalized = hostname.strip().lower().rstrip(".")
    if not normalized or normalized == "localhost" or normalized.endswith(_LOCAL_HOST_SUFFIXES):
        raise ValueError("Playwright URLs must use public hostnames")
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        try:
            answers = socket.getaddrinfo(normalized, 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError("Playwright hostname could not be resolved") from exc
        addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        try:
            for answer in answers:
                addresses.add(ipaddress.ip_address(answer[4][0]))
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError("Playwright hostname returned an invalid DNS address") from exc
        if not addresses or any(not item.is_global for item in addresses):
            raise ValueError("Playwright hostname resolves to a private or non-public address")
        return sorted(addresses, key=lambda item: (item.version, int(item)))[0]
    if not address.is_global:
        raise ValueError("Playwright URLs must not target private or non-public IP addresses")
    return address


def _public_host_resolver_rules(allowed_hosts: set[str]) -> str:
    """Pin browser DNS resolution to the public IPs validated before launch."""
    mappings = []
    for hostname in sorted(allowed_hosts):
        address = _public_address_for_host(hostname)
        if hostname == str(address):
            continue
        rendered_address = f"[{address}]" if address.version == 6 else str(address)
        mappings.append(f"MAP {hostname} {rendered_address}")
    return ",".join(mappings)


def _host_is_allowed(url: str, allowed_hosts: set[str]) -> bool:
    """Return whether a URL uses an allowed host, no credentials, and a standard port."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        return False
    standard_port = port is None or (
        parsed.scheme == "http" and port == 80
    ) or (
        parsed.scheme == "https" and port == 443
    )
    return (
        parsed.scheme in {"http", "https"}
        and standard_port
        and hostname is not None
        and parsed.username is None
        and parsed.password is None
        and not hostname.endswith(".")
        and hostname.lower() in allowed_hosts
    )


class PlaywrightConnector:
    connector_type = "PLAYWRIGHT"
    connector_version = "1.0.0"
    supported_target_kinds = frozenset({"CRAWL", "SEARCH"})
    MAX_ITEMS = 500

    def acquire(self, *, source_id: UUID, context: dict[str, Any]) -> AcquisitionBatchResult:
        start_urls = context.get("start_urls")
        if not isinstance(start_urls, list) or not start_urls:
            raise ValueError("context.start_urls must be a non-empty list of public HTTP(S) URLs")
        rules = context.get("forbidden_target_rules", [])
        if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
            raise ValueError("forbidden target policy is invalid")
        for start_url in start_urls:
            try:
                assert_current_target_not_forbidden(start_url, context, connector_type=self.connector_type)
            except ForbiddenTargetError as exc:
                raise AcquisitionAdapterError(
                    AcquisitionOutcome.RESTRICTED, "FORBIDDEN_TARGET_RULE_MATCHED"
                ) from exc

        hosts: set[str] = set()
        for url in start_urls:
            parsed = urlparse(url) if isinstance(url, str) else None
            if (
                parsed is None
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.hostname.endswith(".")
            ):
                raise ValueError("every start_urls entry must be an absolute HTTP(S) URL without credentials or a trailing dot")
            try:
                port = parsed.port
            except ValueError as exc:
                raise ValueError("start_urls must not use invalid or nonstandard ports") from exc
            if port not in ({None, 80} if parsed.scheme == "http" else {None, 443}):
                raise ValueError("start_urls must not use nonstandard ports")
            hosts.add(parsed.hostname.lower())

        allowed_domains = context.get("allowed_domains", sorted(hosts))
        if (
            not isinstance(allowed_domains, list)
            or not allowed_domains
            or any(not isinstance(host, str) or not host.strip() for host in allowed_domains)
        ):
            raise ValueError("context.allowed_domains must be a non-empty list of domain names")
        allowed_hosts = {host.strip().lower().lstrip(".").rstrip(".") for host in allowed_domains}
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
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise AcquisitionAdapterError(
                AcquisitionOutcome.UNAVAILABLE,
                "PLAYWRIGHT_RUNTIME_UNAVAILABLE",
            ) from exc

        records: list[dict[str, Any]] = []
        failures: list[AcquisitionFailure] = []
        blocked_request_urls: set[str] = set()
        failure_status = AcquisitionOutcome.FAILED
        failure_category: str | None = None
        observed_at = datetime.now(timezone.utc)
        visited: set[str] = set()
        queue = list(start_urls)

        # An allowlist alone does not stop an allowed hostname resolving to a
        # private address. Validate every host and pin Chromium's resolver before launch.
        resolver_rules = _public_host_resolver_rules(allowed_hosts)
        launch_args = ["--no-proxy-server", "--disable-features=AsyncDns"]
        if resolver_rules:
            launch_args.append(f"--host-resolver-rules={resolver_rules}")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=launch_args)
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
                    nonlocal failure_status, failure_category
                    try:
                        assert_current_target_not_forbidden(
                            route.request.url,
                            context,
                            connector_type=self.connector_type,
                            method=route.request.method,
                        )
                    except AcquisitionAdapterError as exc:
                        failures.append(AcquisitionFailure(exc.error_category))
                        failure_status = AcquisitionOutcome.RESTRICTED
                        failure_category = exc.error_category
                        route.abort()
                        return
                    except ForbiddenTargetError:
                        failures.append(AcquisitionFailure("FORBIDDEN_TARGET_RULE_MATCHED"))
                        failure_status = AcquisitionOutcome.RESTRICTED
                        failure_category = "FORBIDDEN_TARGET_RULE_MATCHED"
                        route.abort()
                        return
                    except ValueError:
                        failures.append(AcquisitionFailure("TARGET_POLICY_INVALID"))
                        failure_status = AcquisitionOutcome.RESTRICTED
                        failure_category = "TARGET_POLICY_INVALID"
                        route.abort()
                        return
                    if _host_is_allowed(route.request.url, allowed_hosts):
                        route.continue_()
                    else:
                        if route.request.url not in blocked_request_urls:
                            blocked_request_urls.add(route.request.url)
                            failures.append(AcquisitionFailure("REDIRECT_BLOCKED"))
                        failure_status = AcquisitionOutcome.RESTRICTED
                        failure_category = "REDIRECT_BLOCKED"
                        route.abort()

                browser_context.route("**/*", enforce_allowed_host)
                page = browser_context.new_page()

                def note_failed_off_domain_request(request) -> None:
                    nonlocal failure_status, failure_category
                    if _host_is_allowed(request.url, allowed_hosts):
                        return
                    # Preserve a more specific live-policy denial already recorded by
                    # the route guard; requestfailed can fire after route.abort().
                    if failure_category is not None and failure_category != "REDIRECT_BLOCKED":
                        return
                    if request.url not in blocked_request_urls:
                        blocked_request_urls.add(request.url)
                        failures.append(AcquisitionFailure("REDIRECT_BLOCKED"))
                    failure_status = AcquisitionOutcome.RESTRICTED
                    failure_category = "REDIRECT_BLOCKED"

                # Playwright emits requestfailed for browser-level redirect aborts
                # that do not surface through the context route callback.
                page_on = getattr(page, "on", None)
                if callable(page_on):
                    page_on("requestfailed", note_failed_off_domain_request)

                page.set_default_timeout(timeout_ms)

                while queue and len(visited) < max_pages and len(records) < self.MAX_ITEMS:
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
                            failures.append(AcquisitionFailure("REDIRECT_BLOCKED"))
                            failure_status = AcquisitionOutcome.RESTRICTED
                            failure_category = "REDIRECT_BLOCKED"
                            continue
                        if response is not None and response.status in {401, 403, 407, 429}:
                            # Treat access/rate-limit responses as blocked; do not retry or evade.
                            failures.append(AcquisitionFailure("SOURCE_RESTRICTED", retryable=True))
                            failure_status = AcquisitionOutcome.RESTRICTED
                            failure_category = "SOURCE_RESTRICTED"
                            continue
                        if response is not None and response.status >= 500:
                            failures.append(AcquisitionFailure("UPSTREAM_UNAVAILABLE", retryable=True))
                            if failure_status != AcquisitionOutcome.RESTRICTED:
                                failure_status = AcquisitionOutcome.UNAVAILABLE
                                failure_category = "UPSTREAM_UNAVAILABLE"
                            continue
                        if response is not None and response.status >= 400:
                            failures.append(AcquisitionFailure("UPSTREAM_HTTP_ERROR"))
                            if failure_category is None:
                                failure_status = AcquisitionOutcome.FAILED
                                failure_category = "UPSTREAM_HTTP_ERROR"
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
                            if len(records) >= self.MAX_ITEMS:
                                break

                        if len(records) >= self.MAX_ITEMS:
                            failures.append(AcquisitionFailure("MAX_ITEMS_LIMIT_REACHED"))
                            if failure_category is None:
                                failure_status = AcquisitionOutcome.FAILED
                                failure_category = "MAX_ITEMS_LIMIT_REACHED"
                            break

                        next_selector = selector_map.get("next_page")
                        if next_selector and len(visited) + len(queue) < max_pages:
                            next_link = page.locator(next_selector).first
                            if next_link.count():
                                href = next_link.get_attribute("href")
                                if href:
                                    next_url = urljoin(page.url, href)
                                    if _host_is_allowed(next_url, allowed_hosts) and next_url not in visited:
                                        try:
                                            assert_current_target_not_forbidden(
                                                next_url, context, connector_type=self.connector_type
                                            )
                                        except ForbiddenTargetError:
                                            failures.append(AcquisitionFailure("FORBIDDEN_TARGET_RULE_MATCHED"))
                                            failure_status = AcquisitionOutcome.RESTRICTED
                                            failure_category = "FORBIDDEN_TARGET_RULE_MATCHED"
                                        except ValueError:
                                            failures.append(AcquisitionFailure("TARGET_POLICY_INVALID"))
                                            failure_status = AcquisitionOutcome.RESTRICTED
                                            failure_category = "TARGET_POLICY_INVALID"
                                        else:
                                            queue.append(next_url)
                    except PlaywrightTimeoutError:
                        # A blocked main-frame redirect can surface as a navigation timeout.
                        # Preserve the explicit security denial instead of downgrading it.
                        if failure_category == "REDIRECT_BLOCKED":
                            continue
                        # One page timing out does not invalidate records from other pages.
                        failures.append(AcquisitionFailure("PAGE_TIMEOUT", retryable=True))
                        if failure_status != AcquisitionOutcome.RESTRICTED:
                            failure_status = AcquisitionOutcome.UNAVAILABLE
                            failure_category = "PAGE_TIMEOUT"
                        continue
                    except PlaywrightError:
                        # Route-aborted off-domain navigation raises from page.goto().
                        # Preserve the security outcome instead of leaking a browser exception.
                        if failure_category == "REDIRECT_BLOCKED":
                            continue
                        failures.append(AcquisitionFailure("PLAYWRIGHT_NAVIGATION_FAILED"))
                        if failure_status != AcquisitionOutcome.RESTRICTED:
                            failure_status = AcquisitionOutcome.UNAVAILABLE
                            failure_category = "PLAYWRIGHT_NAVIGATION_FAILED"
                        continue
                    if queue and len(visited) < max_pages:
                        time.sleep(float(delay))
                if len(visited) >= max_pages and any(
                    queued_url not in visited and _host_is_allowed(queued_url, allowed_hosts)
                    for queued_url in queue
                ):
                    failures.append(AcquisitionFailure("MAX_PAGES_LIMIT_REACHED"))
                    if failure_category is None:
                        failure_status = AcquisitionOutcome.FAILED
                        failure_category = "MAX_PAGES_LIMIT_REACHED"
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
        if failures:
            category = failure_category or failures[0].category
            if result:
                return AcquisitionBatchResult(
                    status=AcquisitionOutcome.PARTIAL,
                    records=tuple(result),
                    error_category=category,
                    failures=tuple(failures),
                )
            return AcquisitionBatchResult(
                status=failure_status,
                error_category=category,
                failures=tuple(failures),
            )
        return AcquisitionBatchResult(
            status=AcquisitionOutcome.SUCCEEDED,
            records=tuple(result),
        )
