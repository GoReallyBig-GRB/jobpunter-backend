"""Opt-in live canaries for approved acquisition sources.

These tests never contact external sites in normal CI. To opt in, set both
JOBPUNTER_RUN_LIVE_CANARIES=1 and JOBPUNTER_LIVE_CANARY_APPROVAL=I_APPROVE_EXTERNAL_REQUESTS,
then provide JOBPUNTER_LIVE_CANARY_CONFIG as JSON. See docs/operations/acquisition-live-canaries.md.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.infrastructure.acquisition.jobspy import JobSpyConnector
from app.infrastructure.acquisition.playwright import PlaywrightConnector

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("JOBPUNTER_RUN_LIVE_CANARIES") != "1",
        reason="live acquisition canaries are opt-in and disabled by default",
    ),
]

_APPROVAL = "I_APPROVE_EXTERNAL_REQUESTS"


def _load_config() -> dict:
    if os.getenv("JOBPUNTER_LIVE_CANARY_APPROVAL") != _APPROVAL:
        pytest.fail("Set JOBPUNTER_LIVE_CANARY_APPROVAL to the explicit approval phrase.")
    raw = os.getenv("JOBPUNTER_LIVE_CANARY_CONFIG")
    if not raw:
        pytest.fail("JOBPUNTER_LIVE_CANARY_CONFIG must contain the approved canary JSON.")
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        pytest.fail(f"JOBPUNTER_LIVE_CANARY_CONFIG is invalid JSON: {exc}")
    if not isinstance(config, dict):
        pytest.fail("Live canary config must be a JSON object.")
    if config.get("connector") not in {"JOBSPY", "PLAYWRIGHT"}:
        pytest.fail("connector must be JOBSPY or PLAYWRIGHT.")
    if not isinstance(config.get("context"), dict):
        pytest.fail("context must be an object.")
    minimum = config.get("minimum_records", 1)
    if not isinstance(minimum, int) or isinstance(minimum, bool) or not 1 <= minimum <= 100:
        pytest.fail("minimum_records must be an integer from 1 to 100.")
    return config


def _require_public_playwright_hosts(context: dict) -> None:
    urls = context.get("start_urls")
    allowed = context.get("allowed_domains")
    if not isinstance(urls, list) or not urls or not isinstance(allowed, list) or not allowed:
        pytest.fail("PLAYWRIGHT canary requires explicit start_urls and allowed_domains.")

    allowed_hosts = {str(host).strip().lower() for host in allowed}
    for value in urls:
        parsed = urlparse(value) if isinstance(value, str) else None
        if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.hostname:
            pytest.fail("Every Playwright start URL must be an absolute HTTP(S) URL.")
        host = parsed.hostname.lower()
        if host not in allowed_hosts:
            pytest.fail(f"Start URL host {host!r} must exactly match an allowed domain.")
        try:
            literal_ip = ipaddress.ip_address(host)
        except ValueError:
            literal_ip = None
        if literal_ip is not None:
            pytest.fail("Use an approved public hostname, not an IP literal.")
        if host in {"localhost"} or host.endswith((".localhost", ".local", ".internal", ".test")):
            pytest.fail("Local, internal, and reserved test hostnames are not permitted.")
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
            }
        except OSError as exc:
            pytest.fail(f"Could not resolve approved hostname {host!r}: {exc}")
        if not addresses or any(not address.is_global for address in addresses):
            pytest.fail(f"Hostname {host!r} resolves to a non-public or mixed address set.")


def test_approved_live_acquisition_canary():
    config = _load_config()
    connector_type = config["connector"]
    context = config["context"]
    if connector_type == "JOBSPY":
        if not context.get("site_names"):
            pytest.fail("JOBSPY canary requires explicit site_names; implicit defaults are not accepted.")
        records = JobSpyConnector().acquire(source_id=uuid4(), context=context)
    else:
        _require_public_playwright_hosts(context)
        records = PlaywrightConnector().acquire(source_id=uuid4(), context=context)

    minimum = config.get("minimum_records", 1)
    assert len(records) >= minimum, (
        f"{connector_type} returned {len(records)} records; expected at least {minimum}. "
        "A zero-result run is not live-source verification."
    )
    for record in records:
        assert record.source_id is not None
        assert record.source_url and urlparse(record.source_url).scheme in {"http", "https"}
        assert len(record.content_hash) == 64
        assert record.raw_content
    assert any(
        isinstance(record.raw_content.get("title"), str)
        and record.raw_content["title"].strip()
        for record in records
    ), "At least one live record must contain a non-empty job title."
