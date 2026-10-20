"""Real Chromium integration tests for Playwright acquisition security boundaries.

These tests use local fixtures only. CI installs Chromium; environments without the browser
binary skip the runtime test rather than treating mocked route tests as browser evidence.
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


def test_real_chromium_aborts_off_domain_main_frame_redirect(monkeypatch):
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
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><article class='job'><h2>Should not be extracted</h2></article></body></html>"
            )

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    start_url = f"http://127.0.0.1:{server.server_port}/redirect"

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
    # route handler against a loopback-only fixture. The off-domain redirect remains blocked.
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

    assert result.status == AcquisitionOutcome.RESTRICTED
    assert result.error_category == "REDIRECT_BLOCKED"
    assert result.records == ()
    assert requests == ["/redirect"]
