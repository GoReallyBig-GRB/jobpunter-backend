"""Real Chromium integration tests for Playwright acquisition security boundaries.

These tests use local fixtures only. CI installs Chromium; environments without the browser
binary skip runtime tests rather than treating mocked route tests as browser evidence.
"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.infrastructure.acquisition import playwright as playwright_module
from app.infrastructure.acquisition.playwright import PlaywrightConnector
from app.ports.acquisition import AcquisitionOutcome


@pytest.mark.parametrize(
    ("scenario", "expected_status", "expected_record_count", "expected_request_path"),
    [
        ("main_frame_redirect", AcquisitionOutcome.RESTRICTED, 0, "/redirect"),
        ("off_domain_subresource", AcquisitionOutcome.PARTIAL, 1, "/jobs"),
    ],
)
def test_real_chromium_blocks_off_domain_navigation_and_subresource(
    monkeypatch, scenario, expected_status, expected_record_count, expected_request_path
):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip("Chromium browser binary is not installed in this environment")

    requests = []

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "http://blocked.invalid/private-jobs")
                self.end_headers()
                return
            body = b"""<html><body>
                <article class="job"><h2>Fixture Product Manager</h2></article>
                <img src="http://blocked.invalid/tracker.gif">
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    path_for_scenario = "/redirect" if scenario == "main_frame_redirect" else "/jobs"
    start_url = f"http://127.0.0.1:{server.server_port}{path_for_scenario}"

    def host_is_allowed(url, allowed_hosts):
        try:
            parsed = urlparse(url)
        except (TypeError, ValueError):
            return False
        return (
            parsed.scheme in {"http", "https"}
            and parsed.hostname is not None
            and parsed.hostname.lower() in allowed_hosts
        )

    # Isolate public-DNS and production-port policy so real Chromium can exercise the
    # route handler against a loopback-only fixture. Off-domain requests remain blocked.
    monkeypatch.setattr(playwright_module, "_public_host_resolver_rules", lambda _hosts: "")
    monkeypatch.setattr(playwright_module, "_host_is_allowed", host_is_allowed)
    monkeypatch.setattr(
        playwright_module, "assert_current_target_not_forbidden", lambda *args, **kwargs: None
    )

    try:
        result = PlaywrightConnector().acquire(
            source_id=uuid4(),
            context={
                "start_urls": [start_url],
                "allowed_domains": ["127.0.0.1"],
                "selectors": {"job_card": "article.job", "title": "h2"},
                "max_pages": 1,
                "timeout_ms": 5000,
                "request_delay_seconds": 1,
                "user_agent": "JobPunterLocalBrowserTest/1.0",
                "forbidden_target_rules": [],
            },
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.status == expected_status
    assert result.error_category == "REDIRECT_BLOCKED"
    assert len(result.records) == expected_record_count
    assert requests == [expected_request_path]
