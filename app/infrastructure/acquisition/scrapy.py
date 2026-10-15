"""Configurable Scrapy connector for public, permitted job-listing pages.

Each crawl runs in a fresh Python process because Twisted reactors cannot be restarted
inside a long-lived worker. Crawling remains conservative and honors robots.txt.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from app.ports.acquisition import (
    AcquisitionAdapterError,
    AcquisitionBatchResult,
    AcquisitionFailure,
    AcquisitionOutcome,
    RawAcquisition,
)


_LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


def _normalize_hostname(hostname: str) -> str:
    return hostname.strip().lower().rstrip(".")


def _validate_public_host(hostname: str) -> str:
    normalized = _normalize_hostname(hostname)
    if not normalized or normalized == "localhost" or normalized.endswith(_LOCAL_HOST_SUFFIXES):
        raise ValueError("Scrapy URLs must use public hostnames")
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return normalized
    if not address.is_global:
        raise ValueError("Scrapy URLs must not target private or non-public IP addresses")
    return normalized


def _normalize_allowed_domain(domain: str) -> str:
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError("context.allowed_domains must contain hostnames")
    candidate = domain.strip()
    parsed = urlparse("https://" + candidate)
    if (
        parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("context.allowed_domains entries must be hostnames without paths or ports")
    normalized = _validate_public_host(parsed.hostname)
    if normalized != _normalize_hostname(candidate):
        raise ValueError("context.allowed_domains entries must be hostnames without paths or ports")
    return normalized


def _validate_resolved_host(hostname: str) -> None:
    """Reject hostnames whose DNS answers include any non-public address."""
    try:
        ipaddress.ip_address(hostname)
        return  # Literal addresses were checked by _validate_public_host.
    except ValueError:
        pass
    try:
        answers = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Scrapy hostname could not be resolved to a public address") from exc
    addresses = set()
    for answer in answers:
        try:
            addresses.add(ipaddress.ip_address(answer[4][0]))
        except (IndexError, ValueError, TypeError) as exc:
            raise ValueError("Scrapy hostname returned an invalid DNS address") from exc
    if not addresses:
        raise ValueError("Scrapy hostname did not resolve to an address")
    if any(not address.is_global for address in addresses):
        raise ValueError("Scrapy hostname resolves to a private or non-public IP address")


class ScrapyConnector:
    connector_type = "SCRAPY"
    connector_version = "1.0.0"

    def acquire(self, *, source_id: UUID, context: dict[str, Any]) -> AcquisitionBatchResult:
        start_urls = context.get("start_urls")
        if not isinstance(start_urls, list) or not start_urls:
            raise ValueError("context.start_urls must be a non-empty list of public HTTP(S) URLs")
        start_hosts: list[str] = []
        for url in start_urls:
            try:
                parsed = urlparse(url) if isinstance(url, str) else None
                if not parsed or parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise ValueError("every start_urls entry must be an absolute HTTP(S) URL")
                if parsed.username is not None or parsed.password is not None:
                    raise ValueError("start_urls must not contain embedded credentials")
                if parsed.hostname is None:
                    raise ValueError("every start_urls entry must include a public hostname")
                start_hosts.append(_validate_public_host(parsed.hostname))
            except ValueError as exc:
                if "start_urls" in str(exc) or "public hostname" in str(exc) or "private or non-public" in str(exc) or "public hostnames" in str(exc):
                    raise
                raise ValueError("every start_urls entry must be an absolute HTTP(S) URL") from exc

        selectors = context.get("selectors", {})
        if not isinstance(selectors, dict):
            raise ValueError("context.selectors must be an object of CSS selectors")
        supported_selector_keys = {
            "job_card", "title", "company", "location", "description", "job_url", "next_page"
        }
        if any(
            not isinstance(key, str)
            or not key.strip()
            or key not in supported_selector_keys
            or not isinstance(value, str)
            or not value.strip()
            for key, value in selectors.items()
        ):
            raise ValueError(
                "context.selectors must contain only supported selector names with non-empty CSS strings"
            )
        allowed_domains = context.get("allowed_domains")
        if allowed_domains is None:
            allowed_domains = sorted({
                urlparse(url).hostname for url in start_urls if urlparse(url).hostname
            })
        if not isinstance(allowed_domains, list) or not allowed_domains:
            raise ValueError("context.allowed_domains must be a non-empty list of domain names")
        normalized_domains = sorted({_normalize_allowed_domain(domain) for domain in allowed_domains})
        for host in start_hosts:
            if not any(host == domain or host.endswith("." + domain) for domain in normalized_domains):
                raise ValueError("every start_urls hostname must match context.allowed_domains")

        max_pages = context.get("max_pages", 50)
        if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= 500:
            raise ValueError("context.max_pages must be an integer from 1 to 500")
        delay = context.get("download_delay_seconds", 2.0)
        if not isinstance(delay, (int, float)) or isinstance(delay, bool) or not 1 <= delay <= 30:
            raise ValueError("context.download_delay_seconds must be between 1 and 30")
        user_agent = context.get(
            "user_agent", "JobPunterResearchBot/1.0 (+contact configured by operator)"
        )
        if not isinstance(user_agent, str) or len(user_agent.strip()) < 8:
            raise ValueError("context.user_agent must identify the client")
        timeout = context.get("timeout_seconds", 180)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 10 <= timeout <= 600:
            raise ValueError("context.timeout_seconds must be an integer from 10 to 600")

        # Resolve before starting a worker, rejecting private DNS answers for both
        # entry points and the broader domains Scrapy may follow.
        for hostname in sorted(set(start_hosts) | set(normalized_domains)):
            _validate_resolved_host(hostname)

        worker_config = {
            "start_urls": start_urls,
            "selectors": {
                str(key): value for key, value in selectors.items()
                if isinstance(value, str) and value.strip()
            },
            "allowed_domains": normalized_domains,
            "max_pages": max_pages,
            "download_delay_seconds": float(delay),
            "user_agent": user_agent,
        }
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "app.infrastructure.acquisition.scrapy_worker"],
                input=json.dumps(worker_config),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AcquisitionAdapterError(AcquisitionOutcome.UNAVAILABLE, "SCRAPY_WORKER_TIMEOUT") from exc
        except OSError as exc:
            raise AcquisitionAdapterError(AcquisitionOutcome.UNAVAILABLE, "SCRAPY_WORKER_UNAVAILABLE") from exc

        if completed.returncode != 0:
            # Do not include worker stderr: it may contain operator-supplied URLs/config.
            raise AcquisitionAdapterError(AcquisitionOutcome.FAILED, "SCRAPY_WORKER_FAILED")
        try:
            payload = json.loads(completed.stdout)
            records = payload["records"]
            failures_payload = payload.get("failures", [])
            if (
                not isinstance(records, list)
                or any(not isinstance(record, dict) for record in records)
                or not isinstance(failures_payload, list)
                or any(not isinstance(failure, dict) for failure in failures_payload)
            ):
                raise ValueError
            failures = tuple(
                AcquisitionFailure(
                    category=failure["category"],
                    retryable=failure.get("retryable", False),
                )
                for failure in failures_payload
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise AcquisitionAdapterError(AcquisitionOutcome.FAILED, "SCRAPY_INVALID_RESULT") from exc

        observed_at = datetime.now(timezone.utc)
        result: list[RawAcquisition] = []
        for raw_record in records:
            raw = dict(raw_record)
            raw["connector"] = self.connector_type
            raw["connector_version"] = self.connector_version
            serialized = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            result.append(RawAcquisition(
                source_id=source_id,
                external_record_id=None,
                source_url=str(raw.get("job_url") or raw.get("source_page_url") or "") or None,
                raw_content=raw,
                content_hash=digest,
                observed_at=observed_at,
            ))

        if failures:
            categories = {failure.category for failure in failures}
            if result:
                return AcquisitionBatchResult(
                    status=AcquisitionOutcome.PARTIAL,
                    records=tuple(result),
                    error_category=failures[0].category,
                    failures=failures,
                )
            if "SOURCE_RESTRICTED" in categories:
                outcome = AcquisitionOutcome.RESTRICTED
            elif "UPSTREAM_UNAVAILABLE" in categories:
                outcome = AcquisitionOutcome.UNAVAILABLE
            else:
                outcome = AcquisitionOutcome.FAILED
            return AcquisitionBatchResult(
                status=outcome,
                error_category=failures[0].category,
                failures=failures,
            )
        return AcquisitionBatchResult(
            status=AcquisitionOutcome.SUCCEEDED,
            records=tuple(result),
        )
