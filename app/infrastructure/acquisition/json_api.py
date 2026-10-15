"""Bounded JSON API acquisition connector for explicitly approved public feeds.

Configuration:
- endpoint_url: HTTPS URL returning a JSON object or array
- items_path: optional dot-separated path to an array (default: jobs, items, data)
- field_mapping: optional canonical-field -> dot-separated source-field map
- page_param/page_start/max_pages: optional bounded page-number pagination
- base_params: optional string-to-string query parameters

Redirects are rejected; URLs must resolve exclusively to globally routable addresses.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx

from app.infrastructure.acquisition.outcomes import classify_http_error, result_after_failure
from app.ports.acquisition import (
    AcquisitionAdapterError,
    AcquisitionBatchResult,
    AcquisitionOutcome,
    RawAcquisition,
)


class JsonApiConnectorError(AcquisitionAdapterError):
    """Sanitized upstream or payload failure from the JSON API connector."""

    def __init__(
        self,
        error_category: str,
        *,
        status: AcquisitionOutcome = AcquisitionOutcome.FAILED,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(status, error_category, retry_after_seconds=retry_after_seconds)


class JsonApiConnector:
    connector_type = "JSON_API"
    connector_version = "1.0.0"
    TIMEOUT_SECONDS = 15.0
    MAX_RESPONSE_BYTES = 2_000_000
    MAX_PAGES = 10
    MAX_ITEMS = 500
    DEFAULT_FIELDS = {
        "external_id": "id",
        "title": "title",
        "company": "company",
        "location": "location",
        "description": "description",
        "job_url": "url",
    }

    def __init__(self, http_client: Any | None = None):
        self._http_client = http_client

    @staticmethod
    def _field(value: Any, label: str, *, required: bool = False) -> str | None:
        if value is None and not required:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"context.{label} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _validate_url(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("context.endpoint_url must be a public HTTPS URL")
        parsed = urlparse(value.strip())
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("context.endpoint_url must be a public HTTPS URL without credentials or fragment")
        host = parsed.hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".test")):
            raise ValueError("context.endpoint_url must not target a local or reserved hostname")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("context.endpoint_url must not target a private or non-public address")
        if address is None:
            try:
                answers = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
            except OSError as exc:
                raise ValueError("context.endpoint_url hostname did not resolve") from exc
            addresses = set()
            for answer in answers:
                try:
                    addresses.add(ipaddress.ip_address(answer[4][0]))
                except (IndexError, ValueError, TypeError) as exc:
                    raise ValueError("context.endpoint_url returned an invalid DNS address") from exc
            if not addresses or any(not item.is_global for item in addresses):
                raise ValueError("context.endpoint_url resolves to a private or non-public address")
        return value.strip()

    @staticmethod
    def _path(value: Any, path: str | None) -> Any:
        if not path:
            return value
        current = value
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list) and part.isdigit():
                index = int(part)
                current = current[index] if index < len(current) else None
            else:
                return None
        return current

    @staticmethod
    def _string(value: Any) -> str | None:
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, dict):
            for key in ("name", "value", "label", "url"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
        return None

    @classmethod
    def _extract_items(cls, payload: Any, items_path: str | None) -> list[dict[str, Any]]:
        candidate = cls._path(payload, items_path)
        if items_path is None and isinstance(payload, dict):
            for key in ("jobs", "items", "results", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    candidate = value
                    break
                if isinstance(value, dict):
                    for nested in ("jobs", "items", "results"):
                        if isinstance(value.get(nested), list):
                            candidate = value[nested]
                            break
        if not isinstance(candidate, list):
            raise JsonApiConnectorError("json_api_invalid_items")
        if any(not isinstance(item, dict) for item in candidate):
            raise JsonApiConnectorError("json_api_invalid_record")
        return candidate

    def acquire(self, *, source_id: UUID, context: dict[str, Any]) -> AcquisitionBatchResult:
        if not isinstance(context, dict):
            raise ValueError("JSON API context must be an object")
        endpoint = self._validate_url(context.get("endpoint_url"))
        items_path = self._field(context.get("items_path"), "items_path")
        mapping = context.get("field_mapping", {})
        if not isinstance(mapping, dict) or any(
            not isinstance(key, str) or key not in {*self.DEFAULT_FIELDS, "application_url"}
            or not isinstance(value, str) or not value.strip()
            for key, value in mapping.items()
        ):
            raise ValueError("context.field_mapping must map supported canonical fields to non-empty paths")
        fields = {**self.DEFAULT_FIELDS, **mapping}
        base_params = context.get("base_params", {})
        if not isinstance(base_params, dict) or any(
            not isinstance(key, str) or not key.strip() or not isinstance(value, str)
            for key, value in base_params.items()
        ):
            raise ValueError("context.base_params must contain string keys and values")
        page_param = context.get("page_param")
        if page_param is not None and (not isinstance(page_param, str) or not page_param.strip()):
            raise ValueError("context.page_param must be a non-empty string")
        page_start = context.get("page_start", 1)
        max_pages = context.get("max_pages", 1)
        if not isinstance(page_start, int) or isinstance(page_start, bool) or page_start < 0:
            raise ValueError("context.page_start must be a non-negative integer")
        if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= self.MAX_PAGES:
            raise ValueError(f"context.max_pages must be an integer from 1 to {self.MAX_PAGES}")
        if page_param is None:
            max_pages = 1

        owns_client = self._http_client is None
        client = self._http_client or httpx.Client(timeout=self.TIMEOUT_SECONDS, follow_redirects=False)
        observed_at = datetime.now(timezone.utc)
        acquired: list[RawAcquisition] = []
        try:
            try:
                for page_number in range(max_pages):
                    params = dict(base_params)
                    if page_param:
                        params[page_param] = str(page_start + page_number)
                    try:
                        response = client.get(endpoint, params=params)
                        response.raise_for_status()
                        body = response.content
                    except httpx.HTTPError as exc:
                        mapped = classify_http_error(exc)
                        raise JsonApiConnectorError(
                            mapped.error_category,
                            status=mapped.status,
                            retry_after_seconds=mapped.retry_after_seconds,
                        ) from exc
                    if len(body) > self.MAX_RESPONSE_BYTES:
                        raise JsonApiConnectorError("json_api_response_too_large")
                    try:
                        payload = response.json()
                    except (ValueError, json.JSONDecodeError) as exc:
                        raise JsonApiConnectorError("json_api_invalid_json") from exc
                    records = self._extract_items(payload, items_path)
                    for record in records:
                        raw = dict(record)
                        normalized = {
                            key: self._string(self._path(record, path))
                            for key, path in fields.items()
                        }
                        raw["jobpunter_normalized"] = normalized
                        raw["connector"] = self.connector_type
                        raw["connector_version"] = self.connector_version
                        encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                        content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
                        posting_url = normalized.get("job_url")
                        acquired.append(RawAcquisition(
                            source_id=source_id,
                            external_record_id=normalized.get("external_id"),
                            source_url=posting_url,
                            raw_content=raw,
                            content_hash=content_hash,
                            observed_at=observed_at,
                        ))
                        if len(acquired) >= self.MAX_ITEMS:
                            return result_after_failure(
                                acquired,
                                status=AcquisitionOutcome.FAILED,
                                error_category="MAX_ITEMS_LIMIT_REACHED",
                            )
                    if not records:
                        break
                else:
                    if page_param and acquired:
                        return result_after_failure(
                            acquired,
                            status=AcquisitionOutcome.FAILED,
                            error_category="MAX_PAGES_LIMIT_REACHED",
                        )
                return AcquisitionBatchResult(
                    status=AcquisitionOutcome.SUCCEEDED,
                    records=tuple(acquired),
                )

            except AcquisitionAdapterError as exc:
                return result_after_failure(
                    acquired,
                    status=exc.status,
                    error_category=exc.error_category,
                    retry_after_seconds=exc.retry_after_seconds,
                )
        finally:
            if owns_client:
                client.close()
