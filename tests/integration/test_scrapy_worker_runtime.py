"""End-to-end tests for the one-shot Scrapy worker against a local fixture site.

The fixture server is local and deterministic; this test never depends on a third-party
job board or presents synthetic fixture data as real job discovery.
"""
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/robots.txt":
            self.send_response(404)
            self.end_headers()
            return
        if self.path == "/jobs":
            body = b"""<!doctype html>
            <html><body><article class="job">
              <h2 class="title">Fixture Product Manager</h2>
              <span class="company">Fixture Company</span>
              <span class="location">Remote</span>
              <p class="description">Local test fixture only.</p>
              <a class="job-link" href="/job/fixture-1">View job</a>
            </article></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return


def test_one_shot_worker_extracts_from_local_fixture_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    config = {
        "start_urls": [f"{base_url}/jobs"],
        "selectors": {
            "job_card": "article.job",
            "title": ".title::text",
            "company": ".company::text",
            "location": ".location::text",
            "description": ".description::text",
            "job_url": "a.job-link::attr(href)",
        },
        "allowed_domains": ["127.0.0.1"],
        "max_pages": 1,
        "download_delay_seconds": 1.0,
        "user_agent": "JobPunterLocalTest/1.0",
    }
    try:
        # Separate worker launches model consecutive acquisitions in a long-lived API worker.
        payloads = []
        for _ in range(2):
            completed = subprocess.run(
                [sys.executable, "-m", "app.infrastructure.acquisition.scrapy_worker"],
                input=json.dumps(config),
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            assert completed.returncode == 0, completed.stderr
            payloads.append(json.loads(completed.stdout))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert len(payloads) == 2
    for payload in payloads:
        assert len(payload["records"]) == 1
        record = payload["records"][0]
        assert record["title"] == "Fixture Product Manager"
        assert record["company"] == "Fixture Company"
        assert record["location"] == "Remote"
        assert record["description"] == "Local test fixture only."
        assert record["job_url"] == f"{base_url}/job/fixture-1"
