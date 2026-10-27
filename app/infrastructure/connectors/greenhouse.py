"""Public Greenhouse Job Board connector for published job postings.

Only unauthenticated GET endpoints are used. This connector never submits applications.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx

from app.infrastructure.acquisition.outcomes import classify_http_error
from app.application.jobs.target_policy import ForbiddenTargetError, assert_current_target_not_forbidden
from app.infrastructure.acquisition.public_http import PublicAddressHTTPTransport, ResponseTooLargeError, bounded_get
from app.ports.acquisition import (
    AcquisitionAdapterError,
    AcquisitionBatchResult,
    AcquisitionFailure,
    AcquisitionOutcome,
    RawAcquisition,
)


class GreenhouseConnectorError(AcquisitionAdapterError):
    """Sanitized upstream or payload failure from the public Greenhouse API."""

    def __init__(
        self,
        error_category: str,
        *,
        status: AcquisitionOutcome = AcquisitionOutcome.FAILED,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(status, error_category, retry_after_seconds=retry_after_seconds)


class GreenhouseJobBoardConnector:
    connector_type = "GREENHOUSE_JOB_BOARD"
    connector_version = "1"
    # The connector is board-token based; arbitrary URL targets are not executable.
    supported_target_kinds = frozenset()

    BASE_URL = "https://boards-api.greenhouse.io"
    TIMEOUT_SECONDS = 15.0
    MAX_RESPONSE_BYTES = 2_000_000
    MAX_ITEMS = 500
    _BOARD_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

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

    @classmethod
    def _new_http_client(cls) -> httpx.Client:
        return httpx.Client(
            timeout=cls.TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
            transport=PublicAddressHTTPTransport(),
        )

    @staticmethod
    def validate_context(context: dict[str, Any]) -> str:
        token = context.get("board_token") if isinstance(context, dict) else None
        if not isinstance(token, str) or not GreenhouseJobBoardConnector._BOARD_TOKEN.fullmatch(token):
            raise ValueError("A valid Greenhouse board_token is required")
        return token

    @staticmethod
    def _assert_target_policy(url: str, context: dict[str, Any], *, params: dict[str, str] | None = None) -> None:
        request_url = str(httpx.URL(url).copy_merge_params(params)) if params else url
        try:
            assert_current_target_not_forbidden(
                request_url, context,
                connector_type=GreenhouseJobBoardConnector.connector_type,
            )
        except ForbiddenTargetError as exc:
            raise GreenhouseConnectorError(
                "FORBIDDEN_TARGET_RULE_MATCHED", status=AcquisitionOutcome.RESTRICTED
            ) from exc
        except ValueError as exc:
            raise GreenhouseConnectorError(
                "FORBIDDEN_TARGET_POLICY_INVALID", status=AcquisitionOutcome.RESTRICTED
            ) from exc

    def health_check(self, *, context: dict[str, Any]) -> dict[str, Any]:
        """Verify the public board and jobs endpoints without persisting job records."""
        board_token = self.validate_context(context)
        owns_client = self._http_client is None
        if owns_client:
            client = self._new_http_client()
        else:
            client = self._http_client
        try:
            board_url = f"{self.BASE_URL}/v1/boards/{board_token}"
            self._assert_target_policy(board_url, context)
            board_payload = self._get_json(client, board_url)
            board = self._require_object(
                board_payload, "greenhouse_invalid_board_response",
            )
            board_name = board.get("name")
            if not isinstance(board_name, str) or not board_name.strip():
                raise GreenhouseConnectorError("greenhouse_invalid_board_response")
            jobs_url = f"{self.BASE_URL}/v1/boards/{board_token}/jobs"
            self._assert_target_policy(jobs_url, context, params={"content": "true"})
            jobs_payload = self._get_json(
                client, jobs_url, params={"content": "true"},
            )
            response = self._require_object(
                jobs_payload, "greenhouse_invalid_jobs_response",
            )
            jobs = response.get("jobs")
            if not isinstance(jobs, list):
                raise GreenhouseConnectorError("greenhouse_invalid_jobs_response")
            return {
                "status": "HEALTHY",
                "board_name": board_name.strip(),
                "job_count": len(jobs),
            }
        finally:
            if owns_client:
                client.close()

    @staticmethod
    def _require_object(value: Any, message: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise GreenhouseConnectorError(message)
        return value

    @staticmethod
    def _hash_content(content: dict[str, Any]) -> str:
        encoded = json.dumps(
            content, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _get_json(self, client: Any, url: str, *, params: dict[str, str] | None = None) -> Any:
        try:
            body = bounded_get(
                client,
                url,
                params=params,
                max_bytes=self.MAX_RESPONSE_BYTES,
            )
        except ResponseTooLargeError as exc:
            raise GreenhouseConnectorError("GREENHOUSE_RESPONSE_TOO_LARGE") from exc
        except httpx.HTTPError as exc:
            # Do not persist response bodies or query strings in acquisition errors.
            mapped = classify_http_error(exc)
            raise GreenhouseConnectorError(
                mapped.error_category,
                status=mapped.status,
                retry_after_seconds=mapped.retry_after_seconds,
            ) from exc
        try:
            return json.loads(body)
        except (ValueError, json.JSONDecodeError) as exc:
            raise GreenhouseConnectorError("GREENHOUSE_INVALID_JSON") from exc

    def acquire(self, *, source_id: UUID, context: dict[str, Any]) -> AcquisitionBatchResult:
        board_token = self.validate_context(context)
        owns_client = self._http_client is None
        if owns_client:
            client = self._new_http_client()
        else:
            client = self._http_client

        try:
            board_url = f"{self.BASE_URL}/v1/boards/{board_token}"
            self._assert_target_policy(board_url, context)
            board_payload = self._get_json(client, board_url)
            board = self._require_object(
                board_payload, "greenhouse_invalid_board_response",
            )
            board_name = board.get("name")
            if not isinstance(board_name, str) or not board_name.strip():
                raise GreenhouseConnectorError("greenhouse_invalid_board_response")

            jobs_url = f"{self.BASE_URL}/v1/boards/{board_token}/jobs"
            self._assert_target_policy(jobs_url, context, params={"content": "true"})
            jobs_payload = self._get_json(
                client, jobs_url, params={"content": "true"},
            )
            response = self._require_object(
                jobs_payload, "greenhouse_invalid_jobs_response",
            )
            jobs = response.get("jobs")
            if not isinstance(jobs, list):
                raise GreenhouseConnectorError("greenhouse_invalid_jobs_response")

            observed_at = datetime.now(timezone.utc)
            acquired: list[RawAcquisition] = []
            failures: list[AcquisitionFailure] = []
            truncated = len(jobs) > self.MAX_ITEMS
            for job in jobs[:self.MAX_ITEMS]:
                if not isinstance(job, dict):
                    raise GreenhouseConnectorError("greenhouse_invalid_job_record")
                job_id = job.get("id")
                title = job.get("title")
                absolute_url = job.get("absolute_url")
                parsed_url = urlparse(absolute_url) if isinstance(absolute_url, str) else None
                if (
                    job_id is None
                    or not isinstance(title, str)
                    or not title.strip()
                    or parsed_url is None
                    or parsed_url.scheme not in {"http", "https"}
                    or not parsed_url.netloc
                ):
                    failures.append(AcquisitionFailure("GREENHOUSE_INVALID_JOB_RECORD"))
                    continue

                location = job.get("location")
                location_name = location.get("name") if isinstance(location, dict) else None
                raw_content = {
                    "title": title.strip(),
                    "company": board_name.strip(),
                    "url": absolute_url,
                    "description": job.get("content") if isinstance(job.get("content"), str) else "",
                    "location": location_name if isinstance(location_name, str) else "",
                    "source_updated_at": job.get("updated_at"),
                    "requisition_id": job.get("requisition_id"),
                    "language": job.get("language"),
                    "departments": job.get("departments") if isinstance(job.get("departments"), list) else [],
                    "offices": job.get("offices") if isinstance(job.get("offices"), list) else [],
                    "metadata": job.get("metadata"),
                    "greenhouse_job_id": str(job_id),
                    "greenhouse_board_token": board_token,
                }
                acquired.append(RawAcquisition(
                    source_id=source_id,
                    external_record_id=str(job_id),
                    source_url=absolute_url,
                    raw_content=raw_content,
                    content_hash=self._hash_content(raw_content),
                    observed_at=observed_at,
                ))
            if truncated:
                failures.append(AcquisitionFailure("MAX_ITEMS_LIMIT_REACHED"))
            if failures:
                category = failures[0].category
                return AcquisitionBatchResult(
                    status=AcquisitionOutcome.PARTIAL if acquired else AcquisitionOutcome.FAILED,
                    records=tuple(acquired),
                    error_category=category,
                    failures=tuple(failures),
                )
            return AcquisitionBatchResult(
                status=AcquisitionOutcome.SUCCEEDED,
                records=tuple(acquired),
            )
        finally:
            if owns_client:
                client.close()
