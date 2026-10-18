"""Bounded JSON API acquisition connector for explicitly approved public feeds.

Configuration:
- endpoint_url: HTTPS URL returning a JSON object or array
- items_path: optional dot-separated path to an array (default: jobs, items, data)
- field_mapping: optional canonical-field -> dot-separated source-field map
- page_param/page_start/max_pages: optional bounded page-number pagination
- base_params/query_params: optional non-secret query parameters
- page_size_param/page_size: optional bounded page-size pagination
- bearer_token_env: optional environment variable name for bearer authentication
- timeout_seconds: bounded request timeout

Redirects are rejected; URLs must resolve exclusively to globally routable addresses.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import socket
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlparse
from uuid import UUID

import httpx

from app.infrastructure.acquisition.outcomes import classify_http_error, result_after_failure
from app.application.jobs.target_policy import ForbiddenTargetError, assert_target_not_forbidden
from app.infrastructure.acquisition.public_http import (
    PublicAddressHTTPTransport,
    ResponseTooLargeError,
    bounded_get,
)
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
    MAX_PAGE_SIZE = 100
    MAX_TIMEOUT_SECONDS = 60
    SENSITIVE_QUERY_MARKERS = ("token", "apikey", "accesskey", "secret", "password", "signature", "credential")
    DEFAULT_FIELDS = {
        "external_id": "id",
        "title": "title",
        "company": "company",
        "location": "location",
        "description": "description",
        "job_url": "url",
        "application_url": "application_url",
    }

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
    def _field(value: Any, label: str, *, required: bool = False) -> str | None:
        if value is None and not required:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"context.{label} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _is_sensitive_query_key(key: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
        return (
            any(marker in normalized for marker in JsonApiConnector.SENSITIVE_QUERY_MARKERS)
            or normalized in {"auth", "authorization", "key", "credential"}
        )

    @classmethod
    def _validate_url(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("context.endpoint_url must be a public HTTPS URL")
        parsed = urlparse(value.strip())
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("context.endpoint_url has an invalid port") from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or port not in (None, 443)
        ):
            raise ValueError(
                "context.endpoint_url must be a public HTTPS URL on port 443 without credentials or fragment"
            )
        if any(cls._is_sensitive_query_key(key) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
            raise ValueError("context.endpoint_url must not contain credentials in query parameters")
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
                answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
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

    @classmethod
    def validate_context(cls, context: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize connector configuration before any network request."""
        if not isinstance(context, dict):
            raise ValueError("JSON API context must be an object")
        endpoint = cls._validate_url(context.get("endpoint_url"))
        items_path = cls._field(context.get("items_path"), "items_path")
        mapping = context.get("field_mapping", {})
        allowed_fields = {*cls.DEFAULT_FIELDS, "source_url"}
        if not isinstance(mapping, dict) or any(
            not isinstance(key, str)
            or key not in allowed_fields
            or not isinstance(value, str)
            or not value.strip()
            for key, value in mapping.items()
        ):
            raise ValueError("context.field_mapping must map supported canonical fields to non-empty paths")
        fields = dict(cls.DEFAULT_FIELDS)
        if "source_url" in mapping and "job_url" not in mapping:
            fields["job_url"] = mapping["source_url"]
        fields.update(mapping)

        if "base_params" in context and "query_params" in context:
            raise ValueError("Use only one of context.base_params or context.query_params")
        raw_params = context.get("base_params", context.get("query_params", {}))
        if not isinstance(raw_params, dict):
            raise ValueError("context query parameters must be an object")
        params: dict[str, str] = {}
        for key, value in raw_params.items():
            if (
                not isinstance(key, str)
                or not key.strip()
                or not isinstance(value, (str, int, float, bool))
                or (isinstance(value, float) and not math.isfinite(value))
            ):
                raise ValueError("context query parameters must contain scalar keys and values")
            if cls._is_sensitive_query_key(key):
                raise ValueError("Credentials must not be supplied as query parameters")
            params[key.strip()] = str(value)

        page_param = cls._field(context.get("page_param"), "page_param")
        page_size_param = cls._field(context.get("page_size_param"), "page_size_param")
        if page_param and page_size_param and page_param == page_size_param:
            raise ValueError("context.page_param and context.page_size_param must differ")
        page_start = context.get("page_start", 1)
        max_pages = context.get("max_pages", 1)
        if not isinstance(page_start, int) or isinstance(page_start, bool) or page_start < 0:
            raise ValueError("context.page_start must be a non-negative integer")
        if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= cls.MAX_PAGES:
            raise ValueError(f"context.max_pages must be an integer from 1 to {cls.MAX_PAGES}")
        if page_param is None:
            max_pages = 1

        page_size = context.get("page_size", 50 if page_size_param else None)
        if page_size_param is None and page_size is not None:
            raise ValueError("context.page_size requires context.page_size_param")
        if page_size_param and (
            not isinstance(page_size, int)
            or isinstance(page_size, bool)
            or not 1 <= page_size <= cls.MAX_PAGE_SIZE
        ):
            raise ValueError(f"context.page_size must be an integer from 1 to {cls.MAX_PAGE_SIZE}")
        if page_size_param and page_size * max_pages > cls.MAX_ITEMS:
            raise ValueError("context.page_size multiplied by max_pages exceeds the item limit")

        timeout_seconds = context.get("timeout_seconds", cls.TIMEOUT_SECONDS)
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not 1 <= timeout_seconds <= cls.MAX_TIMEOUT_SECONDS
        ):
            raise ValueError(f"context.timeout_seconds must be between 1 and {cls.MAX_TIMEOUT_SECONDS}")

        bearer_token_env = cls._field(context.get("bearer_token_env"), "bearer_token_env")
        if bearer_token_env:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", bearer_token_env):
                raise ValueError("context.bearer_token_env must be an environment variable name")
            if not os.environ.get(bearer_token_env):
                raise ValueError("context.bearer_token_env must name a configured environment variable")

        return {
            "endpoint_url": endpoint,
            "items_path": items_path,
            "fields": fields,
            "base_params": params,
            "page_param": page_param,
            "page_start": page_start,
            "max_pages": max_pages,
            "page_size_param": page_size_param,
            "page_size": page_size,
            "timeout_seconds": float(timeout_seconds),
            "bearer_token_env": bearer_token_env,
        }

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
        config = self.validate_context(context)
        rules = context.get("forbidden_target_rules", [])
        if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
            raise JsonApiConnectorError("forbidden_target_policy_invalid")
        endpoint = config["endpoint_url"]
        items_path = config["items_path"]
        fields = config["fields"]
        base_params = config["base_params"]
        page_param = config["page_param"]
        page_start = config["page_start"]
        max_pages = config["max_pages"]
        page_size_param = config["page_size_param"]
        page_size = config["page_size"]
        timeout_seconds = config["timeout_seconds"]
        bearer_token_env = config["bearer_token_env"]

        headers = {"Accept": "application/json"}
        if bearer_token_env:
            headers["Authorization"] = f"Bearer {os.environ[bearer_token_env]}"

        owns_client = self._http_client is None
        client = self._http_client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=PublicAddressHTTPTransport(),
        )
        observed_at = datetime.now(timezone.utc)
        acquired: list[RawAcquisition] = []
        seen_page_hashes: set[str] = set()
        try:
            try:
                for page_number in range(max_pages):
                    params = dict(base_params)
                    if page_param:
                        params[page_param] = str(page_start + page_number)
                    if page_size_param:
                        params[page_size_param] = str(page_size)
                    request_url = str(httpx.URL(endpoint).copy_merge_params(params)) if params else endpoint
                    try:
                        assert_target_not_forbidden(request_url, rules, connector_type=self.connector_type)
                    except ForbiddenTargetError as exc:
                        raise JsonApiConnectorError(
                            "forbidden_target_rule_matched",
                            status=AcquisitionOutcome.RESTRICTED,
                        ) from exc
                    except ValueError as exc:
                        raise JsonApiConnectorError("forbidden_target_policy_invalid") from exc
                    try:
                        body = bounded_get(
                            client,
                            endpoint,
                            max_bytes=self.MAX_RESPONSE_BYTES,
                            params=params or None,
                            headers=headers,
                            timeout=timeout_seconds,
                        )
                    except ResponseTooLargeError as exc:
                        raise JsonApiConnectorError("json_api_response_too_large") from exc
                    except httpx.HTTPError as exc:
                        mapped = classify_http_error(exc)
                        raise JsonApiConnectorError(
                            mapped.error_category,
                            status=mapped.status,
                            retry_after_seconds=mapped.retry_after_seconds,
                        ) from exc
                    try:
                        payload = json.loads(body)
                    except (ValueError, UnicodeDecodeError) as exc:
                        raise JsonApiConnectorError("json_api_invalid_json") from exc

                    records = self._extract_items(payload, items_path)
                    page_hash = hashlib.sha256(
                        json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                    ).hexdigest()
                    if page_hash in seen_page_hashes:
                        if acquired:
                            return result_after_failure(
                                acquired,
                                status=AcquisitionOutcome.FAILED,
                                error_category="JSON_API_REPEATED_PAGE",
                            )
                        raise JsonApiConnectorError("json_api_repeated_page")
                    seen_page_hashes.add(page_hash)

                    for record in records:
                        source_record = dict(record)
                        normalized = {
                            key: self._string(self._path(record, path))
                            for key, path in fields.items()
                        }
                        posting_url = normalized.get("source_url") or normalized.get("job_url")
                        application_url = normalized.get("application_url")
                        raw = dict(record)
                        raw["source_record"] = source_record
                        raw["source_url"] = posting_url
                        raw["application_url"] = application_url
                        # The shared normalizer treats url as an application destination.
                        # Never put the posting page there unless the source explicitly maps it as apply URL.
                        raw["url"] = application_url
                        raw["jobpunter_normalized"] = normalized
                        raw["connector"] = self.connector_type
                        raw["connector_version"] = self.connector_version
                        encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                        content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
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
                    if page_size_param and len(records) < page_size:
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
