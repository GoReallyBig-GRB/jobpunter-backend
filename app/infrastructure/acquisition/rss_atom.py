"""Bounded RSS/Atom acquisition connector for explicitly approved public feeds."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlparse
from uuid import UUID

import httpx

from app.infrastructure.acquisition.outcomes import classify_http_error
from app.application.jobs.target_policy import ForbiddenTargetError, assert_target_not_forbidden
from app.infrastructure.acquisition.public_http import (
    PublicAddressHTTPTransport,
    ResponseContentTypeError,
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

    def __init__(
        self,
        http_client: Any | None = None,
        *,
        allow_unsafe_http_client: bool = False,
    ):
        if http_client is not None and not allow_unsafe_http_client:
            raise ValueError(
                "Injected HTTP clients bypass the pinned public-address transport; "
                "set allow_unsafe_http_client=True only for controlled tests"
            )
        self._http_client = http_client

    @staticmethod
    def _is_sensitive_query_key(key: str) -> bool:
        normalized = "".join(character for character in key.casefold() if character.isalnum())
        markers = ("token", "apikey", "accesskey", "secret", "password", "signature", "credential")
        return any(marker in normalized for marker in markers) or normalized in {
            "auth", "authorization", "key", "credential"
        }

    @classmethod
    def _validate_url(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("context.feed_url must be a public HTTPS URL")
        parsed = urlparse(value.strip())
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("context.feed_url has an invalid port") from exc
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment or port not in (None, 443)
        ):
            raise ValueError("context.feed_url must be a public HTTPS URL on port 443 without credentials or fragment")
        if any(cls._is_sensitive_query_key(key) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
            raise ValueError("context.feed_url must not contain credentials in query parameters")
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
                answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
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

    @classmethod
    def validate_context(cls, context: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(context, dict):
            raise ValueError("RSS/Atom context must be an object")
        feed_url = cls._validate_url(context.get("feed_url"))
        max_items = context.get("max_items", cls.MAX_ITEMS)
        if not isinstance(max_items, int) or isinstance(max_items, bool) or not 1 <= max_items <= cls.MAX_ITEMS:
            raise ValueError(f"context.max_items must be an integer from 1 to {cls.MAX_ITEMS}")
        timeout_seconds = context.get("timeout_seconds", cls.TIMEOUT_SECONDS)
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not 1 <= timeout_seconds <= 60
        ):
            raise ValueError("context.timeout_seconds must be between 1 and 60")
        return {"feed_url": feed_url, "max_items": max_items, "timeout_seconds": float(timeout_seconds)}

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

    @staticmethod
    def _safe_link(value: Any, base_url: str) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        candidate = urljoin(base_url, value.strip())
        parsed = urlparse(candidate)
        try:
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or any(
                    RssAtomConnector._is_sensitive_query_key(key)
                    for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
                )
            ):
                return None
        except ValueError:
            return None
        return candidate

    @classmethod
    def _entry(cls, element: ET.Element, feed_url: str) -> dict[str, Any]:
        title = cls._text(cls._child(element, "title"))
        source_link = None
        application_link = None
        for link_element in element.iter():
            if link_element is element or link_element.tag.rsplit("}", 1)[-1].lower() != "link":
                continue
            href = link_element.attrib.get("href") or cls._text(link_element)
            safe_link = cls._safe_link(href, feed_url)
            relation = str(link_element.attrib.get("rel", "alternate")).strip().casefold()
            if relation in {"apply", "apply-now", "application", "apply_now"}:
                application_link = safe_link or application_link
            elif relation in {"alternate", ""} and source_link is None:
                source_link = safe_link

        for field_name in ("apply_url", "application_url", "apply-link", "application-link"):
            if application_link:
                break
            application_link = cls._safe_link(cls._text(cls._child(element, field_name)), feed_url)

        description = (
            cls._text(cls._child(element, "encoded"))
            or cls._text(cls._child(element, "content"))
            or cls._text(cls._child(element, "description"))
            or cls._text(cls._child(element, "summary"))
        )
        identifier = (
            cls._text(cls._child(element, "guid"))
            or cls._text(cls._child(element, "id"))
            or source_link
        )
        author = cls._text(cls._child(element, "author")) or cls._text(cls._child(element, "creator"))
        company = cls._text(cls._child(element, "company")) or cls._text(cls._child(element, "employer")) or author
        location = cls._text(cls._child(element, "location")) or cls._text(cls._child(element, "jobLocation")) or ""
        published = (
            cls._text(cls._child(element, "published"))
            or cls._text(cls._child(element, "updated"))
            or cls._text(cls._child(element, "pubdate"))
        )
        return {
            "title": title,
            "company": company,
            "location": location,
            "description": description or "",
            "source_url": source_link,
            "application_url": application_link,
            "url": application_link,
            "feed_url": feed_url,
            "external_id": identifier,
            "published_at": published,
        }

    def acquire(self, *, source_id: UUID, context: dict[str, Any]) -> AcquisitionBatchResult:
        rules = context.get("forbidden_target_rules", [])
        if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
            raise FeedConnectorError("forbidden_target_policy_invalid")
        raw_feed_url = context.get("feed_url")
        try:
            assert_target_not_forbidden(
                raw_feed_url, rules, connector_type=self.connector_type
            )
        except ForbiddenTargetError as exc:
            raise FeedConnectorError(
                "forbidden_target_rule_matched", status=AcquisitionOutcome.RESTRICTED
            ) from exc
        except (TypeError, ValueError) as exc:
            raise FeedConnectorError("forbidden_target_policy_invalid") from exc
        config = self.validate_context(context)
        feed_url = config["feed_url"]
        try:
            assert_target_not_forbidden(feed_url, rules, connector_type=self.connector_type)
        except ForbiddenTargetError as exc:
            raise FeedConnectorError(
                "forbidden_target_rule_matched",
                status=AcquisitionOutcome.RESTRICTED,
            ) from exc
        except ValueError as exc:
            raise FeedConnectorError("forbidden_target_policy_invalid") from exc
        max_items = config["max_items"]
        timeout_seconds = config["timeout_seconds"]
        owns_client = self._http_client is None
        client = self._http_client or httpx.Client(
            timeout=timeout_seconds,
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
                    timeout=timeout_seconds,
                    allowed_content_types=(
                        "application/atom+xml",
                        "application/rss+xml",
                        "application/xml",
                        "application/rdf+xml",
                        "text/xml",
                    ),
                )
            except ResponseContentTypeError as exc:
                raise FeedConnectorError("rss_atom_invalid_content_type") from exc
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
            root_name = root.tag.rsplit("}", 1)[-1].lower()
            if root_name not in {"rss", "feed", "rdf"}:
                raise FeedConnectorError("rss_atom_invalid_content_type")

            entries = [
                element for element in root.iter()
                if element.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}
            ]
            if not entries:
                return AcquisitionBatchResult(status=AcquisitionOutcome.SUCCEEDED, records=())
            truncated = len(entries) > max_items
            observed_at = datetime.now(timezone.utc)
            acquired: list[RawAcquisition] = []
            failures: list[AcquisitionFailure] = []
            for element in entries[:max_items]:
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
                    source_url=raw.get("source_url"),
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
            return AcquisitionBatchResult(status=AcquisitionOutcome.SUCCEEDED, records=tuple(acquired))
        finally:
            if owns_client:
                client.close()
