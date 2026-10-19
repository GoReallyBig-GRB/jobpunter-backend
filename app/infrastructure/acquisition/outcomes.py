"""Shared mapping from connector failures to explicit acquisition outcomes."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from app.ports.acquisition import (
    AcquisitionAdapterError,
    AcquisitionBatchResult,
    AcquisitionFailure,
    AcquisitionOutcome,
    RawAcquisition,
)


def _retry_after_seconds(response: httpx.Response | None) -> int | None:
    if response is None:
        return None
    raw = response.headers.get("Retry-After", "").strip()
    if not raw:
        return None
    if raw.isdigit():
        # Keep provider-controlled delay hints bounded before persisting them.
        return min(int(raw), 86_400)
    # HTTP-date is the other standard Retry-After form. Treat malformed dates as
    # absent rather than allowing provider-controlled parsing errors to escape.
    try:
        retry_at = parsedate_to_datetime(raw)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return None
    return min(max(0, int(seconds)), 86_400)


def classify_http_error(exc: httpx.HTTPError) -> AcquisitionAdapterError:
    """Classify HTTP/network failures without retaining response bodies or URLs."""
    response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
    retry_after = _retry_after_seconds(response)
    if response is not None and response.status_code in {401, 403, 407, 429}:
        return AcquisitionAdapterError(
            AcquisitionOutcome.RESTRICTED,
            "SOURCE_RESTRICTED",
            retry_after_seconds=retry_after,
        )
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)) or (
        response is not None and (
            response.status_code in {408, 425} or response.status_code >= 500
        )
    ):
        return AcquisitionAdapterError(
            AcquisitionOutcome.UNAVAILABLE,
            "UPSTREAM_UNAVAILABLE",
            retry_after_seconds=retry_after,
        )
    return AcquisitionAdapterError(
        AcquisitionOutcome.FAILED,
        "UPSTREAM_REQUEST_FAILED",
        retry_after_seconds=retry_after,
    )


def result_after_failure(
    records: list[RawAcquisition],
    *,
    status: AcquisitionOutcome,
    error_category: str,
    retry_after_seconds: int | None = None,
) -> AcquisitionBatchResult:
    """Retain records acquired before a later failure instead of discarding them."""
    if records:
        return AcquisitionBatchResult(
            status=AcquisitionOutcome.PARTIAL,
            records=tuple(records),
            error_category=error_category,
            failures=(
                AcquisitionFailure(
                    category=error_category,
                    retryable=status == AcquisitionOutcome.UNAVAILABLE or retry_after_seconds is not None,
                ),
            ),
            retry_after_seconds=retry_after_seconds,
        )
    return AcquisitionBatchResult(
        status=status,
        error_category=error_category,
        retry_after_seconds=retry_after_seconds,
    )
