"""Bounded transport primitive for explicitly approved read-only POST operations.

This module is intentionally separate from the JSON API connector, which remains
GET-only. Callers must enforce persisted connector method/capability policy before
using this transport.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.infrastructure.acquisition.public_http import ResponseTooLargeError


MAX_REQUEST_BODY_BYTES = 64_000
MAX_RESPONSE_BYTES = 2_000_000


def validate_read_only_post_body(body: Any, *, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> bytes:
    """Serialize a JSON object body with strict type and byte-size limits."""
    if not isinstance(body, dict):
        raise ValueError("read-only POST body must be a JSON object")
    try:
        encoded = json.dumps(
            body,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("read-only POST body must contain finite JSON values") from exc
    if len(encoded) > max_bytes:
        raise ValueError("read-only POST body exceeds configured byte limit")
    return encoded


def bounded_read_only_post_json(
    client: Any,
    url: str,
    *,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    max_request_bytes: int = MAX_REQUEST_BODY_BYTES,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
) -> bytes:
    """POST a bounded JSON object and stream a bounded JSON response.

    Redirects are rejected. The caller remains responsible for HTTPS/public-target
    validation and for revalidating persisted authorization immediately before each
    network request. No non-JSON body or arbitrary request method is accepted.
    """
    encoded = validate_read_only_post_body(body, max_bytes=max_request_bytes)
    stream = getattr(client, "stream", None)
    if not callable(stream):
        raise httpx.UnsupportedProtocol("HTTP client must support streaming responses")

    request_headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    # Disable redirects at the HTTPX request boundary. Inspecting response.history
    # after the fact is not sufficient if a caller supplied a redirect-following client.
    options: dict[str, Any] = {
        "headers": request_headers,
        "content": encoded,
        "follow_redirects": False,
    }
    if timeout is not None:
        options["timeout"] = timeout

    with stream("POST", url, **options) as response:
        if getattr(response, "history", ()):
            raise httpx.HTTPError("Redirects are not permitted")
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type and content_type not in {"application/json", "application/problem+json"}:
            raise ValueError("read-only POST returned an unexpected content type")
        result = bytearray()
        for chunk in response.iter_bytes(chunk_size=64 * 1024):
            if len(result) + len(chunk) > max_response_bytes:
                raise ResponseTooLargeError("Response exceeded configured byte limit")
            result.extend(chunk)
        return bytes(result)


def authorized_read_only_post_json(
    client: Any,
    url: str,
    *,
    body: dict[str, Any],
    action: str,
    connector_type: str,
    persisted_configuration: Any,
    persisted_capabilities: Any,
    policy_revalidator: Any,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    max_request_bytes: int = MAX_REQUEST_BODY_BYTES,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
) -> bytes:
    """Run POST only when persisted configuration and a fresh policy guard allow it."""
    configuration = persisted_configuration
    capabilities = persisted_capabilities
    if not isinstance(configuration, dict) or not isinstance(capabilities, dict):
        raise PermissionError("read-only POST is not authorized")
    configured_action = configuration.get("read_only_post_action")
    if (
        configuration.get("request_method") != "POST"
        or capabilities.get("read_only_post") is not True
        or not isinstance(configured_action, str)
        or not configured_action.strip()
        or not isinstance(action, str)
        or action != configured_action
    ):
        raise PermissionError("read-only POST is not authorized")
    if not callable(policy_revalidator):
        raise PermissionError("read-only POST requires live policy revalidation")

    # Revalidate immediately before the only network call. The persisted guard
    # independently compares current DB state against the run's authorized snapshot.
    policy_revalidator(url=url, connector_type=connector_type, method="POST")
    return bounded_read_only_post_json(
        client,
        url,
        body=body,
        headers=headers,
        timeout=timeout,
        max_request_bytes=max_request_bytes,
        max_response_bytes=max_response_bytes,
    )
