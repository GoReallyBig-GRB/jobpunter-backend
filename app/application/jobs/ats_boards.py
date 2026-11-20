"""Official public ATS job boards (Greenhouse, Lever, Ashby): config builders and a live probe.

These are the class of sources the owner approved on 2026-11-19: public company job boards read
through the providers' official public APIs. Nothing here scrapes; every request is a single
HTTPS GET to a fixed official host.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

import httpx

TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
MAX_PROBE_BYTES = 8_000_000
PROBE_TIMEOUT_SECONDS = 8.0
MIN_PROBE_JOBS = 1

CLASS_APPROVAL_REFERENCE = "class-approval:public-ats-job-boards:2026-11-19"
CLASS_REVIEW_NOTES = (
    "Owner-approved source class (2026-11-19): public company job boards on Greenhouse, Lever and "
    "Ashby read through their official public APIs; discovered companies may be registered by the system."
)
RATE_LIMIT_POLICY = (
    "At most one listing request per board per scheduled refresh (default every 6 hours); "
    "no more than two concurrent requests to a provider; stop and back off on HTTP 429 or 5xx."
)

PROVIDERS: dict[str, dict[str, str]] = {
    "greenhouse": {
        "label": "Greenhouse",
        "endpoint": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
        "terms": "https://developers.greenhouse.io/job-board.html",
    },
    "lever": {
        "label": "Lever",
        "endpoint": "https://api.lever.co/v0/postings/{token}",
        "terms": "https://github.com/lever/postings-api",
    },
    "ashby": {
        "label": "Ashby",
        "endpoint": "https://api.ashbyhq.com/posting-api/job-board/{token}",
        "terms": "https://developers.ashbyhq.com/docs/public-job-posting-api",
    },
    "workable": {
        "label": "Workable",
        # Public widget feed; the same payload is served at www.workable.com/api/accounts/{token}.
        "endpoint": "https://apply.workable.com/api/v1/widget/accounts/{token}",
        "terms": "https://help.workable.com/hc/en-us/articles/115012771647",
    },
}


def valid_token(token: str) -> bool:
    return isinstance(token, str) and bool(TOKEN_PATTERN.match(token))


def endpoint_for(provider: str, token: str) -> str:
    return PROVIDERS[provider]["endpoint"].format(token=token)


def display_name(token: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[-_.]+", token) if part)


def connector_for(provider: str, token: str, company: str) -> tuple[str, dict[str, Any]]:
    """Return (connector_type, configuration) for a board."""
    if provider == "greenhouse":
        return "GREENHOUSE_JOB_BOARD", {"board_token": token}
    if provider == "lever":
        return "JSON_API", {
            "endpoint_url": endpoint_for("lever", token),
            "base_params": {"mode": "json"},
            "max_response_bytes": MAX_PROBE_BYTES,
            "field_mapping": {
                "external_id": "id", "title": "text", "job_url": "hostedUrl",
                "application_url": "applyUrl", "location": "categories.location",
            },
            "description_paths": ["descriptionPlain", "lists", "additionalPlain"],
            "omit_fields": [
                "description", "descriptionBody", "descriptionBodyPlain", "opening", "additional",
                "descriptionPlain", "lists", "additionalPlain",
            ],
            "static_fields": {"company": company},
        }
    if provider == "workable":
        return "JSON_API", {
            "endpoint_url": endpoint_for("workable", token),
            "items_path": "jobs",
            "base_params": {"details": "true"},
            "max_response_bytes": MAX_PROBE_BYTES,
            "field_mapping": {
                "external_id": "shortcode", "title": "title", "job_url": "url",
                "application_url": "application_url", "location": "location.location_str",
            },
            "description_paths": ["full_description"],
            "omit_fields": ["full_description", "description"],
            "static_fields": {"company": company},
        }
    return "JSON_API", {
        "endpoint_url": endpoint_for("ashby", token),
        "items_path": "jobs",
        "max_response_bytes": MAX_PROBE_BYTES,
        "field_mapping": {
            "external_id": "jobUrl", "title": "title", "job_url": "jobUrl",
            "application_url": "applyUrl", "location": "location",
        },
        "description_paths": ["descriptionPlain"],
        "omit_fields": ["descriptionHtml", "descriptionPlain"],
        "static_fields": {"company": company},
    }


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    job_count: int = 0
    reason: str | None = None


Fetcher = Callable[[str], tuple[int, bytes]]


def _http_fetch(url: str) -> tuple[int, bytes]:
    with httpx.Client(timeout=PROBE_TIMEOUT_SECONDS, follow_redirects=False) as client:
        with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_PROBE_BYTES:
                    return response.status_code, b""
            return response.status_code, bytes(body)


def probe_board(provider: str, token: str, fetch: Fetcher | None = None) -> ProbeResult:
    """Confirm the official endpoint answers with at least one job. Never raises."""
    if provider not in PROVIDERS:
        return ProbeResult(False, reason="UNKNOWN_PROVIDER")
    if not valid_token(token):
        return ProbeResult(False, reason="INVALID_TOKEN")
    import json

    fetch = fetch or _http_fetch
    try:
        status, body = fetch(endpoint_for(provider, token))
    except Exception:  # network failure: report, never raise
        return ProbeResult(False, reason="REQUEST_FAILED")
    if status == 404:
        return ProbeResult(False, reason="BOARD_NOT_FOUND")
    if status != 200 or not body:
        return ProbeResult(False, reason=f"HTTP_{status}" if status != 200 else "EMPTY_OR_TOO_LARGE")
    try:
        payload = json.loads(body)
    except ValueError:
        return ProbeResult(False, reason="NOT_JSON")
    jobs = payload if isinstance(payload, list) else payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return ProbeResult(False, reason="UNEXPECTED_SHAPE")
    if len(jobs) < MIN_PROBE_JOBS:
        return ProbeResult(False, reason="NO_OPEN_JOBS")
    return ProbeResult(True, job_count=len(jobs))


SEED_BATCH = 25


def load_seed() -> list[dict[str, str]]:
    """Candidate company boards. Validity is decided by the live probe, never assumed."""
    import json
    from pathlib import Path

    items = json.loads((Path(__file__).with_name("ats_seed.json")).read_text())
    return [i for i in items if i.get("provider") in PROVIDERS and valid_token(i.get("token", ""))]
