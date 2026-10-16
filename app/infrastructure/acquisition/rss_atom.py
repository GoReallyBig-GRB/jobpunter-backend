"""Bounded RSS/Atom acquisition connector for explicitly approved public feeds."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx

from app.infrastructure.acquisition.outcomes import classify_http_error
from app.infrastructure.acquisition.public_http import (
    PublicAddressHTTPTransport,
    ResponseTooLargeError,
    bounded_get,
)
from app.ports.acquisition import (
    AcquisitionAdapterError,
    AcquisitionBatchResult,
    AcquisitionFailure,
    AcquisitionOutcome,
    RawAcquisition,
)


class FeedConnectorError(AcquisitionAdapterError):
    """Sanitized upstream or payload failure from an RSS/Atom connector."""

    def __init__(
        self,
        error_category: str,
        *,
        status: AcquisitionOutcome = AcquisitionOutcome.FAILED,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(status, error_category, retry_after_seconds=retry_after_seconds)


class RssAtomConnector:
    connector_type = "RSS_ATOM"
    connector_version = "1.0.0"
    TIMEOUT_SECONDS = 15.0
    MAX_RESPONSE_BYTES = 2_000_000
    MAX_ITEMS = 500

    def __init__(self, http_client: Any | None = None):
        self._http_client = http_client

    @staticmethod
    def _validate_url(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("context.feed_url must be a public HTTPS URL")
        parsed = urlparse(value.strip())
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("context.feed_url must be a public HTTPS URL without credentials or fragment")
        host = parsed.hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".test")):
            raise ValueError("context.feed_url must not target a local or reserved hostname")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("context.feed_url must not target a private or non-public address")
        if address is None:
            try:
                answers = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
            except OSError as exc:
                raise ValueError("context.feed_url hostname did not resolve") from exc
            addresses = set()
            for answer in answers:
                try:
                    addresses.add(ipaddress.ip_address(answer[4][0]))
                except (IndexError, ValueError, TypeError) as exc:
                    raise ValueError("context.feed_url returned an invalid DNS address") from exc
            if not addresses or any(not item.is_global for item in addresses):
                raise ValueError("context.feed_url resolves to a private or non-public address")
        return value.strip()

    @staticmethod
    def _text(element: ET.Element | None) -> str | None:
        if element is None:
            return None
        value = "".join(element.itertext()).strip()
        return value or None

    @staticmethod
    def _child(element: ET.Element, name: str) -> ET.Element | None:
        for child in element.iter():
            if child is not element and child.tag.rsplit("}", 1)[-1].lower() == name.lower():
                return child
        return None

    @classmethod
    def _entry(cls, element: ET.Element, feed_url: str) -> dict[str, Any]:
        title = cls._text(cls._child(element, "title"))
        link_element = cls._child(element, "link")
        link = None
        if link_element is not None:
            link = (link_element.attrib.get("href") or cls._text(link_element))
        description = (
            cls._text(cls._child(element, "encoded"))
            or cls._text(cls._child(element, "content"))
            or cls._text(cls._child(element, "description"))
            or cls._text(cls._child(element, "summary"))
        )
        identifier = (
            cls._text(cls._child(element, "guid"))
            or cls._text(cls._child(element, "id"))
            or link
        )
        author = cls._text(cls._child(element, "author")) or cls._text(cls._child(element, "creator"))
        published = (
            cls._text(cls._child(element, "published"))
            or cls._text(cls._child(element, "updated"))
            or cls._text(cls._child(element, "pubdate"))
        )
        return {
            "title": title,
            "company": author,
            "location": "",
            "description": description or "",
            "url": link if isinstance(link, str) and link.strip() else feed_url,
            "feed_url": feed_url,
            "external_id": identifier,
            "published_at": published,
        }

    def acquire(self, *, source_id: UUID, context: dict[str, Any]) -> AcquisitionBatchResult:
        if not isinstance(context, dict):
            raise ValueError("RSS/Atom context must be an object")
        feed_url = self._validate_url(context.get("feed_url"))
        owns_client = self._http_client is None
        client = self._http_client or httpx.Client(
            timeout=self.TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
            transport=PublicAddressHTTPTransport(),
        )
        try:
            try:
                body = bounded_get(
                    client,
                    feed_url,
                    max_bytes=self.MAX_RESPONSE_BYTES,
                    headers={"Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml"},
                )
            except ResponseTooLargeError as exc:
                raise FeedConnectorError("rss_atom_response_too_large") from exc
            except httpx.HTTPError as exc:
                mapped = classify_http_error(exc)
                raise FeedConnectorError(
                    mapped.error_category,
                    status=mapped.status,
                    retry_after_seconds=mapped.retry_after_seconds,
                ) from exc
            try:
                root = ET.fromstring(body)
            except ET.ParseError as exc:
                raise FeedConnectorError("rss_atom_invalid_xml") from exc

            entries = [
                element for element in root.iter()
                if element.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}
            ]
            if not entries:
                return AcquisitionBatchResult(
                    status=AcquisitionOutcome.SUCCEEDED,
                    records=(),
                )
            truncated = len(entries) > self.MAX_ITEMS
            observed_at = datetime.now(timezone.utc)
            acquired: list[RawAcquisition] = []
            failures: list[AcquisitionFailure] = []
            for element in entries[: self.MAX_ITEMS]:
                raw = self._entry(element, feed_url)
                if not raw["title"]:
                    failures.append(AcquisitionFailure("RSS_ATOM_INVALID_ENTRY"))
                    continue
                raw["connector"] = self.connector_type
                raw["connector_version"] = self.connector_version
                encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                acquired.append(RawAcquisition(
                    source_id=source_id,
                    external_record_id=raw.get("external_id"),
                    source_url=raw.get("url"),
                    raw_content=raw,
                    content_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                    observed_at=observed_at,
                ))
            if truncated:
                failures.append(AcquisitionFailure("MAX_ITEMS_LIMIT_REACHED"))
            if failures:
                return AcquisitionBatchResult(
                    status=AcquisitionOutcome.PARTIAL if acquired else AcquisitionOutcome.FAILED,
                    records=tuple(acquired),
                    error_category=failures[0].category,
                    failures=tuple(failures),
                )
            return AcquisitionBatchResult(
                status=AcquisitionOutcome.SUCCEEDED,
                records=tuple(acquired),
            )
        finally:
            if owns_client:
                client.close()
