"""Configurable Scrapy connector for public, permitted job-listing pages.

Each crawl runs in a fresh Python process because Twisted reactors cannot be restarted
inside a long-lived worker. Crawling remains conservative and honors robots.txt.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
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
            parsed = urlparse(url) if isinstance(url, str) else None
            if not parsed or parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("every start_urls entry must be an absolute HTTP(S) URL")

        selectors = context.get("selectors", {})
        if not isinstance(selectors, dict):
            raise ValueError("context.selectors must be an object of CSS selectors")
        allowed_domains = context.get("allowed_domains")
        if allowed_domains is None:
            allowed_domains = sorted({
                urlparse(url).hostname for url in start_urls if urlparse(url).hostname
            })
        if (
            not isinstance(allowed_domains, list)
            or not allowed_domains
            or any(not isinstance(domain, str) or not domain.strip() for domain in allowed_domains)
        ):
            raise ValueError("context.allowed_domains must be a non-empty list of domain names")

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

        worker_config = {
            "start_urls": start_urls,
            "selectors": {
                str(key): value for key, value in selectors.items()
                if isinstance(value, str) and value.strip()
            },
            "allowed_domains": allowed_domains,
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
            raise RuntimeError("Scrapy acquisition timed out") from exc
        except OSError as exc:
            raise RuntimeError("Could not start the Scrapy worker process") from exc

        if completed.returncode != 0:
            # Do not include worker stderr: it may contain operator-supplied URLs/config.
            raise RuntimeError(f"Scrapy worker exited with status {completed.returncode}")
        try:
            payload = json.loads(completed.stdout)
            records = payload["records"]
            if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
                raise ValueError
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Scrapy worker returned an invalid result payload") from exc

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
        return result
