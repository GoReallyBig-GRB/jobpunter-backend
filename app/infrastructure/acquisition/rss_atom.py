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

from app.ports.acquisition import RawAcquisition


class FeedConnectorError(RuntimeError):
    """Sanitized upstream or payload failure from an RSS/Atom connector."""


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

    def acquire(self, *, source_id: UUID, context: dict[str, Any]) -> list[RawAcquisition]:
        if not isinstance(context, dict):
            raise ValueError("RSS/Atom context must be an object")
        feed_url = self._validate_url(context.get("feed_url"))
        owns_client = self._http_client is None
        client = self._http_client or httpx.Client(timeout=self.TIMEOUT_SECONDS, follow_redirects=False)
        try:
            try:
                response = client.get(feed_url, headers={"Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml"})
                response.raise_for_status()
                body = response.content
            except httpx.HTTPError as exc:
                raise FeedConnectorError("rss_atom_request_failed") from exc
            if len(body) > self.MAX_RESPONSE_BYTES:
                raise FeedConnectorError("rss_atom_response_too_large")
            try:
                root = ET.fromstring(body)
            except ET.ParseError as exc:
                raise FeedConnectorError("rss_atom_invalid_xml") from exc

            entries = [
                element for element in root.iter()
                if element.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}
            ]
            if not entries:
                raise FeedConnectorError("rss_atom_no_entries")
            observed_at = datetime.now(timezone.utc)
            acquired: list[RawAcquisition] = []
            for element in entries[: self.MAX_ITEMS]:
                raw = self._entry(element, feed_url)
                if not raw["title"]:
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
            return acquired
        finally:
            if owns_client:
                client.close()
