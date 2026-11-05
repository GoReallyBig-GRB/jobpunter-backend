"""Bounded public-HTTP extraction of schema.org JobPosting JSON-LD."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from app.application.jobs.source_governance import normalize_public_target_url
from app.application.jobs.target_policy import ForbiddenTargetError, assert_target_not_forbidden
from app.infrastructure.acquisition.outcomes import classify_http_error
from app.infrastructure.acquisition.public_http import (
    PublicAddressHTTPTransport,
    ResponseContentTypeError,
    ResponseTooLargeError,
    bounded_get,
)
from app.ports.acquisition import (
    AcquisitionAdapterError,
    AcquisitionBatchResult,
    AcquisitionOutcome,
    RawAcquisition,
)


class PublicHttpJsonLdError(AcquisitionAdapterError):
    def __init__(
        self,
        error_category: str,
        *,
        status: AcquisitionOutcome = AcquisitionOutcome.FAILED,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(status, error_category, retry_after_seconds=retry_after_seconds)


class _JsonLdScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._capturing = False
        self._parts: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        values = {key.casefold(): (value or "") for key, value in attrs}
        self._capturing = (
            values.get("type", "").split(";", 1)[0].strip().casefold() == "application/ld+json"
        )
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._capturing:
            self.scripts.append("".join(self._parts))
            self._capturing = False
            self._parts = []


class PublicHttpJsonLdConnector:
    connector_type = "PUBLIC_HTTP"
    connector_version = "1.0.0"
    supported_target_kinds = frozenset({"CRAWL", "SEARCH"})
    TIMEOUT_SECONDS = 15.0
    MAX_RESPONSE_BYTES = 2_000_000
    MAX_ITEMS = 100

    def __init__(
        self, http_client: Any | None = None, *, allow_unsafe_http_client: bool = False
    ) -> None:
        if http_client is not None and not allow_unsafe_http_client:
            raise ValueError(
                "Injected HTTP clients bypass the pinned public-address transport; "
                "set allow_unsafe_http_client=True only for controlled tests"
            )
        self._http_client = http_client

    @classmethod
    def validate_context(cls, context: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(context, dict):
            raise TypeError("public HTTP context must be an object")
        page_url = normalize_public_target_url(context.get("page_url"), require_https=True)
        max_items = context.get("max_items", cls.MAX_ITEMS)
        if (
            not isinstance(max_items, int)
            or isinstance(max_items, bool)
            or not 1 <= max_items <= cls.MAX_ITEMS
        ):
            raise ValueError(f"context.max_items must be an integer from 1 to {cls.MAX_ITEMS}")
        timeout_seconds = context.get("timeout_seconds", cls.TIMEOUT_SECONDS)
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not 1 <= timeout_seconds <= 60
        ):
            raise ValueError("context.timeout_seconds must be between 1 and 60")
        return {
            "page_url": page_url,
            "max_items": max_items,
            "timeout_seconds": float(timeout_seconds),
        }

    @staticmethod
    def _job_postings(value: Any):
        if isinstance(value, list):
            for item in value:
                yield from PublicHttpJsonLdConnector._job_postings(item)
            return
        if not isinstance(value, dict):
            return
        item_type = value.get("@type")
        types = item_type if isinstance(item_type, list) else [item_type]
        if any(isinstance(kind, str) and kind.casefold() == "jobposting" for kind in types):
            yield value
        graph = value.get("@graph")
        if isinstance(graph, list):
            yield from PublicHttpJsonLdConnector._job_postings(graph)

    @staticmethod
    def _text(value: Any) -> str | None:
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, dict):
            for key in ("name", "value", "@value"):
                candidate = PublicHttpJsonLdConnector._text(value.get(key))
                if candidate:
                    return candidate
        return None

    @classmethod
    def _location(cls, posting: dict[str, Any]) -> str | None:
        locations = posting.get("jobLocation")
        if not isinstance(locations, list):
            locations = [locations]
        rendered: list[str] = []
        for location in locations:
            if not isinstance(location, dict):
                continue
            address = location.get("address")
            if not isinstance(address, dict):
                continue
            parts = [
                cls._text(address.get(key))
                for key in ("addressLocality", "addressRegion", "addressCountry")
            ]
            value = ", ".join(item for item in parts if item)
            if value and value not in rendered:
                rendered.append(value)
        return "; ".join(rendered) or None

    @staticmethod
    def _safe_source_url(candidate: str | None, page_url: str) -> str:
        if not candidate:
            return page_url
        try:
            normalized = normalize_public_target_url(candidate, require_https=True)
        except (TypeError, ValueError):
            return page_url
        if urlsplit(normalized).hostname != urlsplit(page_url).hostname:
            return page_url
        return normalized

    def acquire(self, *, source_id: UUID, context: dict[str, Any]) -> AcquisitionBatchResult:
        rules = context.get("forbidden_target_rules", [])
        if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
            raise PublicHttpJsonLdError("FORBIDDEN_TARGET_POLICY_INVALID")
        raw_url = context.get("page_url")
        try:
            assert_target_not_forbidden(raw_url, rules, connector_type=self.connector_type)
        except ForbiddenTargetError as exc:
            raise PublicHttpJsonLdError(
                "FORBIDDEN_TARGET_RULE_MATCHED", status=AcquisitionOutcome.RESTRICTED
            ) from exc
        except (TypeError, ValueError) as exc:
            raise PublicHttpJsonLdError("FORBIDDEN_TARGET_POLICY_INVALID") from exc
        revalidator = context.get("acquisition_policy_revalidator")
        if callable(revalidator):
            revalidator(url=raw_url, connector_type=self.connector_type, method="GET")
        config = self.validate_context(context)
        owns_client = self._http_client is None
        client = self._http_client or httpx.Client(
            timeout=config["timeout_seconds"],
            follow_redirects=False,
            trust_env=False,
            transport=PublicAddressHTTPTransport(),
        )
        try:
            try:
                body = bounded_get(
                    client,
                    config["page_url"],
                    max_bytes=self.MAX_RESPONSE_BYTES,
                    headers={"Accept": "text/html, application/xhtml+xml"},
                    timeout=config["timeout_seconds"],
                    allowed_content_types=("text/html", "application/xhtml+xml"),
                )
            except ResponseTooLargeError as exc:
                raise PublicHttpJsonLdError("PUBLIC_HTTP_RESPONSE_TOO_LARGE") from exc
            except ResponseContentTypeError as exc:
                raise PublicHttpJsonLdError("PUBLIC_HTTP_INVALID_CONTENT_TYPE") from exc
            except httpx.HTTPError as exc:
                mapped = classify_http_error(exc)
                raise PublicHttpJsonLdError(
                    mapped.error_category,
                    status=mapped.status,
                    retry_after_seconds=mapped.retry_after_seconds,
                ) from exc
        finally:
            if owns_client:
                client.close()

        try:
            html = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PublicHttpJsonLdError("PUBLIC_HTTP_INVALID_ENCODING") from exc
        parser = _JsonLdScriptParser()
        parser.feed(html)
        postings: list[dict[str, Any]] = []
        invalid_scripts = 0
        for script in parser.scripts:
            try:
                payload = json.loads(script)
            except (TypeError, ValueError, json.JSONDecodeError):
                invalid_scripts += 1
                continue
            postings.extend(self._job_postings(payload))
            if len(postings) >= config["max_items"]:
                postings = postings[: config["max_items"]]
                break
        if not postings and invalid_scripts:
            raise PublicHttpJsonLdError("PUBLIC_HTTP_INVALID_JSON_LD")

        observed_at = datetime.now(UTC)
        records: list[RawAcquisition] = []
        for posting in postings:
            source_url = self._safe_source_url(self._text(posting.get("url")), config["page_url"])
            identifier = self._text(posting.get("identifier")) or self._text(posting.get("@id"))
            normalized = {
                "title": self._text(posting.get("title")),
                "company": self._text(posting.get("hiringOrganization")),
                "location": self._location(posting),
                "description": self._text(posting.get("description")),
                "source_url": source_url,
                "application_url": None,
                "external_id": identifier,
                "published_at": self._text(posting.get("datePosted")),
            }
            raw = {
                "source_record": posting,
                "source_url": source_url,
                "application_url": None,
                "url": None,
                "jobpunter_normalized": normalized,
                "connector": self.connector_type,
                "connector_version": self.connector_version,
            }
            encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            records.append(
                RawAcquisition(
                    source_id=source_id,
                    external_record_id=identifier,
                    source_url=source_url,
                    raw_content=raw,
                    content_hash=hashlib.sha256(encoded.encode()).hexdigest(),
                    observed_at=observed_at,
                )
            )
        return AcquisitionBatchResult(AcquisitionOutcome.SUCCEEDED, tuple(records))
