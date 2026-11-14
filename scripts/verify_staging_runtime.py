"""Collect bounded, secret-free evidence from a deployed staging backend."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

MAX_RESPONSE_BYTES = 1024 * 1024


def _request_json(
    base_url: str,
    path: str,
    *,
    token: str | None = None,
    query: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> tuple[int, dict[str, Any]]:
    if not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError("timeout must be finite and between 0 (exclusive) and 60 seconds")
    suffix = f"?{urlencode(query)}" if query else ""
    request = Request(f"{base_url.rstrip('/')}{path}{suffix}")
    request.add_header("Accept", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        status = exc.code
        try:
            with exc:
                body = exc.read(MAX_RESPONSE_BYTES + 1)
        except OSError as read_error:
            return 0, {"transport_error": type(read_error).__name__}
    except (OSError, URLError) as exc:
        return 0, {"transport_error": type(exc).__name__}
    if len(body) > MAX_RESPONSE_BYTES:
        return status, {"response_error": "RESPONSE_TOO_LARGE"}
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {"response_error": "NON_JSON_RESPONSE"}
    return status, payload if isinstance(payload, dict) else {"response_error": "NON_OBJECT_RESPONSE"}


def _result(name: str, passed: bool, details: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "status": "PASS" if passed else "FAIL", "details": details}


def _observation_is_fresh(value: Any, *, now: datetime, max_age_seconds: int) -> bool:
    if not isinstance(value, str):
        return False
    try:
        observed_at = datetime.fromisoformat(value)
        if observed_at.tzinfo is None:
            return False
        age = (now - observed_at).total_seconds()
    except (ValueError, OverflowError):
        return False
    return 0 <= age <= max_age_seconds


def verify_runtime(
    *,
    base_url: str,
    expected_revision: str,
    admin_token: str | None,
    expected_workers: set[str],
    required_integrations: set[str],
    stale_after_seconds: int,
    timeout: float,
    integration_max_age_seconds: int = 3600,
) -> dict[str, Any]:
    if not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError("timeout must be finite and between 0 (exclusive) and 60 seconds")
    if not 1 <= integration_max_age_seconds <= 86400:
        raise ValueError("integration_max_age_seconds must be between 1 and 86400")
    checks: list[dict[str, Any]] = []

    status, payload = _request_json(base_url, "/api/v1/health", timeout=timeout)
    data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
    checks.append(_result("liveness", status == 200 and data.get("status") == "ok", {
        "http_status": status,
        "reported_revision": data.get("deployment_revision", "unknown"),
    }))

    status, payload = _request_json(base_url, "/api/v1/readiness", timeout=timeout)
    data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
    readiness_checks = data.get("checks", {}) if isinstance(data.get("checks"), dict) else {}
    readiness_passed = (
        status == 200
        and data.get("status") == "ready"
        and data.get("deployment_revision") == expected_revision
        and readiness_checks.get("database") is True
        and readiness_checks.get("schema_current") is True
        and readiness_checks.get("deployment_revision_configured") is True
    )
    checks.append(_result("readiness", readiness_passed, {
        "http_status": status,
        "reported_revision": data.get("deployment_revision", "unknown"),
        "expected_revision": expected_revision,
        "environment": data.get("environment", "unknown"),
        "checks": readiness_checks,
        "schema": data.get("schema", {}),
    }))

    admin_requested = bool(expected_workers or required_integrations)
    if admin_requested and not admin_token:
        checks.append(_result("admin_evidence", False, {
            "reason": "ADMIN_TOKEN_NOT_CONFIGURED",
        }))
    elif admin_token:
        status, payload = _request_json(
            base_url, "/api/v1/admin/operations/summary", token=admin_token, timeout=timeout,
        )
        data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
        summary_passed = (
            status == 200
            and data.get("stale_running_acquisition_runs") == 0
            and data.get("expired_processing_outbox_events") == 0
            and isinstance(data.get("workers"), dict)
            and data["workers"].get("stale_count") == 0
        )
        checks.append(_result("operations_summary", summary_passed, {
            "http_status": status,
            "stale_running_acquisition_runs": data.get("stale_running_acquisition_runs"),
            "expired_processing_outbox_events": data.get("expired_processing_outbox_events"),
            "stale_worker_count": data.get("workers", {}).get("stale_count")
            if isinstance(data.get("workers"), dict) else None,
        }))

        if expected_workers:
            status, payload = _request_json(
                base_url,
                "/api/v1/admin/operations/workers",
                token=admin_token,
                query={"stale_after_seconds": stale_after_seconds, "limit": 100},
                timeout=timeout,
            )
            items = payload.get("data", []) if isinstance(payload.get("data"), list) else []
            latest_by_name: dict[str, dict[str, Any]] = {}
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("worker_name"), str):
                    latest_by_name.setdefault(item["worker_name"], item)
            missing = sorted(expected_workers - latest_by_name.keys())
            unhealthy = sorted(
                name for name in expected_workers
                if name in latest_by_name
                and latest_by_name[name].get("status") not in {"RUNNING", "IDLE"}
            )
            checks.append(_result("worker_heartbeats", status == 200 and not missing and not unhealthy, {
                "http_status": status,
                "expected": sorted(expected_workers),
                "missing": missing,
                "unhealthy": unhealthy,
                "observed_statuses": {
                    name: latest_by_name[name].get("status")
                    for name in sorted(expected_workers & latest_by_name.keys())
                },
                "stale_after_seconds": stale_after_seconds,
            }))

        if required_integrations:
            status, payload = _request_json(
                base_url, "/api/v1/admin/integrations/status", token=admin_token, timeout=timeout,
            )
            data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
            integrations = data.get("integrations", []) if isinstance(data.get("integrations"), list) else []
            by_key = {
                item["key"]: item for item in integrations
                if isinstance(item, dict) and isinstance(item.get("key"), str)
            }
            observed_now = datetime.now(UTC)
            missing = sorted(required_integrations - by_key.keys())
            unverified = sorted(
                key for key in required_integrations
                if key in by_key
                and not (
                    by_key[key].get("configured") is True
                    and by_key[key].get("observed_status") == "HEALTHY"
                    and _observation_is_fresh(
                        by_key[key].get("last_tested_at"), now=observed_now,
                        max_age_seconds=integration_max_age_seconds,
                    )
                )
            )
            checks.append(_result("integration_observations", status == 200 and not missing and not unverified, {
                "http_status": status,
                "required": sorted(required_integrations),
                "missing": missing,
                "not_healthy": unverified,
                "max_observation_age_seconds": integration_max_age_seconds,
                "observed": {
                    key: {
                        "configured": by_key[key].get("configured"),
                        "observed_status": by_key[key].get("observed_status"),
                        "last_tested_at": by_key[key].get("last_tested_at"),
                        "last_error_category": by_key[key].get("last_error_category"),
                    }
                    for key in sorted(required_integrations & by_key.keys())
                },
            }))

    passed = all(check["status"] == "PASS" for check in checks)
    return {
        "status": "PASS" if passed else "FAIL",
        "collected_at": datetime.now(UTC).isoformat(),
        "target": base_url.rstrip("/"),
        "expected_revision": expected_revision,
        "checks": checks,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument(
        "--admin-token-env",
        default="JOBPUNTER_STAGING_ADMIN_TOKEN",
        help="Environment variable containing a staging Admin bearer token; never printed.",
    )
    parser.add_argument("--expected-worker", action="append", default=[])
    parser.add_argument("--required-integration", action="append", default=[])
    parser.add_argument("--stale-after-seconds", type=int, default=120)
    parser.add_argument("--integration-max-age-seconds", type=int, default=3600)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--output", help="Optional JSON evidence output path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 60:
        raise SystemExit("--timeout must be finite and between 0 (exclusive) and 60 seconds")
    if not 1 <= args.integration_max_age_seconds <= 86400:
        raise SystemExit("--integration-max-age-seconds must be between 1 and 86400")
    if not 30 <= args.stale_after_seconds <= 3600:
        raise SystemExit("--stale-after-seconds must be between 30 and 3600")
    report = verify_runtime(
        base_url=args.base_url,
        expected_revision=args.expected_revision,
        admin_token=os.getenv(args.admin_token_env),
        expected_workers={name.strip().lower() for name in args.expected_worker if name.strip()},
        required_integrations={key.strip().lower() for key in args.required_integration if key.strip()},
        stale_after_seconds=args.stale_after_seconds,
        timeout=args.timeout,
        integration_max_age_seconds=args.integration_max_age_seconds,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    print(rendered)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
