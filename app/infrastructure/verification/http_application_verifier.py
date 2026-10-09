"""Bounded public-web verifier for job application URLs.

This adapter intentionally uses explicit redirect handling, disables environment
proxy inheritance, and rejects non-public DNS results. Production deployments must
also enforce outbound network policy to mitigate DNS rebinding between preflight
resolution and connection establishment.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable
from urllib.parse import urljoin, urlsplit

import httpx

from app.application.jobs.job_verification import VerificationResult


MAX_REDIRECTS = 5
MAX_BODY_BYTES = 512_000
TIMEOUT_SECONDS = 8.0
VERIFIER_VERSION = "public-web-verifier-v1"
REDIRECT_CODES = {301, 302, 303, 307, 308}
CLOSED_PHRASES = (
    "this job is no longer available",
    "this position is no longer available",
    "this job posting has expired",
    "this vacancy is closed",
    "applications for this role are closed",
    "the position has been filled",
)
JOB_PAGE_MARKERS = (
    "job description",
    "responsibilities",
    "qualifications",
    "requirements",
)
APPLY_MARKERS = (
    "apply now",
    "apply for this job",
    "submit application",
    "start application",
)


def _resolve_public_addresses(host: str) -> list[str]:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        answers = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        addresses = sorted({answer[4][0].split("%", 1)[0] for answer in answers})
    else:
        addresses = [str(literal)]
    if not addresses:
        raise ValueError("hostname did not resolve")
    if any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("URL resolves to a non-public address")
    return addresses


def _validate_url(url: str, resolver: Callable[[str], list[str]]) -> None:
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("verification URL must be absolute HTTP(S)")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("verification URL must not contain credentials")
    if parsed.hostname.lower() in {"localhost"} or parsed.hostname.lower().endswith(
        (".localhost", ".local", ".internal")
    ):
        raise ValueError("verification URL must use a public hostname")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("verification URL has an invalid port") from exc
    resolver(parsed.hostname)


def _bounded_text(response: httpx.Response) -> str:
    content = bytearray()
    for chunk in response.iter_bytes():
        remaining = MAX_BODY_BYTES - len(content)
        if remaining <= 0:
            break
        content.extend(chunk[:remaining])
        if len(content) >= MAX_BODY_BYTES:
            break
    return content.decode(response.encoding or "utf-8", errors="replace").lower()


class PublicWebApplicationVerifier:
    """Verify a public URL conservatively; uncertain pages remain UNKNOWN."""

    def __init__(
        self,
        *,
        client_factory: Callable[[], httpx.Client] | None = None,
        resolver: Callable[[str], list[str]] = _resolve_public_addresses,
    ) -> None:
        self.client_factory = client_factory or (
            lambda: httpx.Client(
                timeout=TIMEOUT_SECONDS,
                follow_redirects=False,
                trust_env=False,
                headers={"User-Agent": "JobPunterJobVerifier/1.0"},
            )
        )
        self.resolver = resolver

    def __call__(self, url: str) -> VerificationResult:
        _validate_url(url, self.resolver)
        current_url = url
        redirects = 0
        try:
            with self.client_factory() as client:
                while True:
                    with client.stream("GET", current_url) as response:
                        status_code = response.status_code
                        headers = response.headers
                        if status_code in REDIRECT_CODES:
                            location = headers.get("location")
                            if not location or redirects >= MAX_REDIRECTS:
                                return VerificationResult(
                                    "UNKNOWN", url, current_url, status_code,
                                    "REDIRECT_LIMIT_OR_LOCATION_MISSING",
                                    {"redirect_count": redirects},
                                )
                            next_url = urljoin(current_url, location)
                            _validate_url(next_url, self.resolver)
                            current_url = next_url
                            redirects += 1
                            continue

                        body = _bounded_text(response)
                        evidence = {
                            "redirect_count": redirects,
                            "body_bytes_inspected": min(
                                len(body.encode("utf-8", errors="replace")), MAX_BODY_BYTES
                            ),
                            "closed_phrase_detected": any(
                                phrase in body for phrase in CLOSED_PHRASES
                            ),
                            "job_page_marker_detected": any(
                                marker in body for marker in JOB_PAGE_MARKERS
                            ),
                            "apply_marker_detected": any(
                                marker in body for marker in APPLY_MARKERS
                            ),
                        }
                        if status_code == 410 or evidence["closed_phrase_detected"]:
                            return VerificationResult(
                                "CLOSED", url, current_url, status_code,
                                "EXPLICIT_JOB_CLOSED_SIGNAL", evidence,
                            )
                        if (
                            200 <= status_code < 300
                            and evidence["job_page_marker_detected"]
                            and evidence["apply_marker_detected"]
                        ):
                            return VerificationResult(
                                "ACTIVE", url, current_url, status_code,
                                "JOB_PAGE_AND_APPLICATION_SIGNAL_PRESENT", evidence,
                            )
                        return VerificationResult(
                            "UNKNOWN", url, current_url, status_code,
                            "INSUFFICIENT_ACTIVE_OR_CLOSED_EVIDENCE", evidence,
                        )
        except (httpx.HTTPError, OSError, socket.gaierror):
            return VerificationResult(
                "UNKNOWN", url, current_url, None, "VERIFIER_REQUEST_FAILED",
                {"redirect_count": redirects},
            )
