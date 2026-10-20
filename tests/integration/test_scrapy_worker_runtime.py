"""End-to-end tests for the one-shot Scrapy worker against a local fixture site.

The fixture server is local and deterministic; this test never depends on a third-party
job board or presents synthetic fixture data as real job discovery.
"""
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.application.jobs.registered_acquisition import build_acquisition_policy_revalidator
from app.infrastructure.db.models import Source, SourceConnector
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository


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
            # Production guards must reject loopback. This test-only runner bypasses
            # the address and target-policy guards so the real Scrapy crawl can exercise
            # parsing against the local fixture; separate tests verify both protections.
            runner = (
                "import app.infrastructure.acquisition.scrapy_worker_runtime as runtime; "
                "runtime._require_public_resolved_address = lambda address: address; runtime.assert_target_not_forbidden = lambda *args, **kwargs: None; "
                "import app.infrastructure.acquisition.scrapy_worker as worker; "
                "worker.main()"
            )
            completed = subprocess.run(
                [sys.executable, "-c", runner],
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

def test_one_shot_worker_bounds_redirect_loop_against_local_fixture():
    requests = []

    class RedirectLoopHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path == "/robots.txt":
                self.send_response(404)
                self.end_headers()
                return
            if self.path == "/loop":
                self.send_response(302)
                self.send_header("Location", "/loop")
                self.end_headers()
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectLoopHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    config = {
        "start_urls": [f"{base_url}/loop"],
        "selectors": {"job_card": "article.job", "title": ".title::text"},
        "allowed_domains": ["127.0.0.1"],
        "max_pages": 1,
        "download_delay_seconds": 0.0,
        "user_agent": "JobPunterLocalTest/1.0",
    }
    try:
        runner = (
            "import app.infrastructure.acquisition.scrapy_worker_runtime as runtime; "
            "runtime._require_public_resolved_address = lambda address: address; "
            "runtime.assert_target_not_forbidden = lambda *args, **kwargs: None; "
            "import app.infrastructure.acquisition.scrapy_worker as worker; "
            "worker.main()"
        )
        completed = subprocess.run(
            [sys.executable, "-c", runner],
            input=json.dumps(config),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["records"] == []
    assert any(
        failure["category"] == "SCRAPY_REDIRECT_LIMIT_REACHED"
        for failure in payload["failures"]
    )
    # Scrapy allows the initial request plus at most REDIRECT_MAX_TIMES redirects.
    assert requests.count("/loop") <= 21



def test_one_shot_worker_revalidates_redirect_destination_before_network():
    requests = []

    class RedirectToBlockedHostHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path == "/robots.txt":
                self.send_response(404)
                self.end_headers()
                return
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "https://blocked.example/private-jobs")
                self.end_headers()
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectToBlockedHostHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    config = {
        "start_urls": [f"{base_url}/redirect"],
        "selectors": {"job_card": "article.job", "title": ".title::text"},
        "allowed_domains": ["127.0.0.1", "blocked.example"],
        "max_pages": 1,
        "download_delay_seconds": 0.0,
        "user_agent": "JobPunterLocalTest/1.0",
        # Presence of a snapshot activates the worker's live-policy guard. The
        # subprocess replaces only the DB-backed guard factory with a deterministic
        # test guard; the real downloader middleware and redirect flow remain active.
        "policy_snapshot": {},
    }
    runner = """
from app.ports.acquisition import AcquisitionAdapterError, AcquisitionOutcome
import app.infrastructure.acquisition.scrapy_worker_runtime as runtime

def guard(*, url, connector_type, method):
    if url.startswith("https://blocked.example/"):
        raise AcquisitionAdapterError(
            AcquisitionOutcome.RESTRICTED, "FORBIDDEN_TARGET_RULE_MATCHED"
        )

runtime._require_public_resolved_address = lambda address: address
runtime.assert_target_not_forbidden = lambda *args, **kwargs: None
runtime._build_worker_policy_guard = lambda _config: (None, guard, None)
import app.infrastructure.acquisition.scrapy_worker as worker
worker.main()
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", runner],
            input=json.dumps(config),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["records"] == []
    assert any(
        failure["category"] == "FORBIDDEN_TARGET_RULE_MATCHED"
        for failure in payload["failures"]
    )
    # The local redirect endpoint is reached, but the forbidden HTTPS destination
    # is rejected by the real downloader middleware before any network request.
    assert requests.count("/redirect") == 1
    assert all(path in {"/robots.txt", "/redirect"} for path in requests)


def test_one_shot_worker_blocks_newly_forbidden_pagination_url_before_request():
    requests = []

    class PaginationPolicyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path == "/robots.txt":
                self.send_response(404)
                self.end_headers()
                return
            if self.path == "/jobs":
                body = b"""<!doctype html><html><body>
                <article class="job"><h2 class="title">Fixture role</h2></article>
                <a rel="next" href="/next">Next</a>
                </body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/next":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<html><body>must not be fetched</body></html>")
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), PaginationPolicyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    config = {
        "start_urls": [f"{base_url}/jobs"],
        "selectors": {
            "job_card": "article.job",
            "title": ".title::text",
            "next_page": "a[rel='next']::attr(href)",
        },
        "allowed_domains": ["127.0.0.1"],
        "max_pages": 2,
        "download_delay_seconds": 0.0,
        "user_agent": "JobPunterLocalTest/1.0",
        "policy_snapshot": {},
    }
    runner = """
from app.ports.acquisition import AcquisitionAdapterError, AcquisitionOutcome
import app.infrastructure.acquisition.scrapy_worker_runtime as runtime

def guard(*, url, connector_type, method):
    if url.endswith("/next"):
        raise AcquisitionAdapterError(
            AcquisitionOutcome.RESTRICTED, "ACQUISITION_CONFIGURATION_CHANGED"
        )

runtime._require_public_resolved_address = lambda address: address
runtime.assert_target_not_forbidden = lambda *args, **kwargs: None
runtime._build_worker_policy_guard = lambda _config: (None, guard, None)
import app.infrastructure.acquisition.scrapy_worker as worker
worker.main()
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", runner],
            input=json.dumps(config),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["records"]
    assert any(
        failure["category"] == "ACQUISITION_CONFIGURATION_CHANGED"
        for failure in payload["failures"]
    )
    assert requests.count("/jobs") == 1
    assert "/next" not in requests


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)
def test_one_shot_worker_revalidates_real_postgres_policy_mutation_between_pages():
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=4)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    source_id = connector_id = None
    requests = []

    with SessionLocal() as db:
        source = Source(
            name=f"Scrapy worker policy source {__import__('uuid').uuid4()}",
            source_type="SCRAPY",
            base_url="https://jobs.example",
            status="ACTIVE",
            permission_review_status="APPROVED",
            permission_review_reference="scrapy-worker-test-review",
            permission_reviewed_at=datetime.now(timezone.utc),
            configuration={},
        )
        db.add(source)
        db.flush()
        connector = SourceConnector(
            source_id=source.id,
            connector_type="SCRAPY",
            connector_version="1.0.0",
            status="ACTIVE",
            configuration={"policy_marker": "before"},
            capabilities={},
            limits={},
        )
        db.add(connector)
        db.commit()
        source_id, connector_id = source.id, connector.id
        repository = SqlAlchemyAcquisitionRepository(db)
        policy_snapshot = build_acquisition_policy_revalidator(
            repository, source_id, connector_id, source, connector
        ).policy_snapshot

    class MutatingPaginationHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path == "/robots.txt":
                self.send_response(404)
                self.end_headers()
                return
            if self.path == "/jobs":
                # This separate database session commits while the worker is running.
                # The worker must observe the changed connector policy before /next.
                with SessionLocal() as mutation_db:
                    current = mutation_db.get(SourceConnector, connector_id)
                    current.configuration = {"policy_marker": "after"}
                    mutation_db.commit()
                body = b"""<!doctype html><html><body>
                <article class="job"><h2 class="title">First page role</h2></article>
                <a rel="next" href="https://blocked.example/next">Next</a>
                </body></html>"""
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

    server = ThreadingHTTPServer(("127.0.0.1", 0), MutatingPaginationHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    config = {
        "start_urls": [f"{base_url}/jobs"],
        "selectors": {
            "job_card": "article.job",
            "title": ".title::text",
            "next_page": "a[rel='next']::attr(href)",
        },
        "allowed_domains": ["127.0.0.1", "blocked.example"],
        "max_pages": 2,
        "download_delay_seconds": 0.0,
        "user_agent": "JobPunterLocalPostgresTest/1.0",
        "source_id": str(source_id),
        "connector_id": str(connector_id),
        "policy_snapshot": policy_snapshot,
    }
    runner = """
import app.infrastructure.acquisition.scrapy_worker_runtime as runtime
import app.application.jobs.registered_acquisition as registered

# The local fixture must bypass network-address policy only. Persisted source/connector
# snapshots and per-request PostgreSQL revalidation remain real.
runtime._require_public_resolved_address = lambda address: address
runtime.assert_target_not_forbidden = lambda *args, **kwargs: None
registered.assert_target_not_forbidden = lambda *args, **kwargs: None
import app.infrastructure.acquisition.scrapy_worker as worker
worker.main()
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", runner],
            input=json.dumps(config),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        payload = json.loads(completed.stdout)
        assert any(record.get("title") == "First page role" for record in payload["records"])
        assert any(
            failure["category"] == "ACQUISITION_CONFIGURATION_CHANGED"
            for failure in payload["failures"]
        )
        assert requests.count("/jobs") == 1
        assert "/next" not in requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        with SessionLocal() as cleanup_db:
            current_connector = cleanup_db.get(SourceConnector, connector_id)
            current_source = cleanup_db.get(Source, source_id)
            if current_connector is not None:
                cleanup_db.delete(current_connector)
            if current_source is not None:
                cleanup_db.delete(current_source)
            cleanup_db.commit()
        engine.dispose()
