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
from uuid import uuid4
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.application.jobs.registered_acquisition import (
    RegisteredAcquisitionService,
    build_acquisition_policy_revalidator,
)
from app.infrastructure.db.models import Source, SourceConnector, User
from app.infrastructure.db.models.target_policy import ForbiddenTargetRule
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
        "allowed_domains": ["127.0.0.1", blocked_host],
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
@pytest.mark.parametrize(
    ("mutation", "expected_category"),
    [
        ("connector_configuration", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("source_deactivation", "SOURCE_POLICY_CHANGED"),
        ("connector_deactivation", "CONNECTOR_POLICY_CHANGED"),
        ("permission_review_revocation", "PERMISSION_REVIEW_REQUIRED"),
        ("forbidden_rule_added", "FORBIDDEN_TARGET_RULE_MATCHED"),
    ],
)
def test_one_shot_worker_revalidates_real_postgres_policy_mutation_between_pages(
    mutation, expected_category
):
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=4)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    source_id = connector_id = None
    requests = []

    blocked_host = f"blocked-{uuid4().hex}.example.com"

    with SessionLocal() as db:
        admin = User(display_name=f"Scrapy worker policy admin {uuid4()}")
        db.add(admin)
        db.flush()
        user_id = admin.id
        source = Source(
            name=f"Scrapy worker policy source {uuid4()}",
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
                    current_source = mutation_db.get(Source, source_id)
                    current_connector = mutation_db.get(SourceConnector, connector_id)
                    if mutation == "connector_configuration":
                        current_connector.configuration = {"policy_marker": "after"}
                    elif mutation == "source_deactivation":
                        current_source.status = "INACTIVE"
                    elif mutation == "connector_deactivation":
                        current_connector.status = "INACTIVE"
                    elif mutation == "permission_review_revocation":
                        current_source.permission_review_status = "REVOKED"
                    elif mutation == "forbidden_rule_added":
                        mutation_db.add(
                            ForbiddenTargetRule(
                                rule_type="HOST_EXACT",
                                pattern=blocked_host,
                                connector_scope="SCRAPY",
                                reason="Blocked during worker execution",
                                active=True,
                                created_by_user_id=user_id,
                            )
                        )
                    else:
                        raise AssertionError(f"unexpected test mutation: {mutation}")
                    mutation_db.commit()
                body = f"""<!doctype html><html><body>
                <article class="job"><h2 class="title">First page role</h2></article>
                <a rel="next" href="https://{blocked_host}/next">Next</a>
                </body></html>""".encode()
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
        "allowed_domains": ["127.0.0.1", blocked_host],
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

# Bypass address-policy checks only for the loopback fixture. Keep the real deny-rule
# matcher active for non-loopback pagination targets and keep persisted revalidation live.
runtime._require_public_resolved_address = lambda address: address
runtime.assert_target_not_forbidden = lambda *args, **kwargs: None
original_target_guard = registered.assert_target_not_forbidden
def fixture_aware_target_guard(url, rules, *, connector_type):
    if url.startswith("http://127.0.0.1:"):
        return
    original_target_guard(url, rules, connector_type=connector_type)
registered.assert_target_not_forbidden = fixture_aware_target_guard
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
            failure["category"] == expected_category
            for failure in payload["failures"]
        )
        assert requests.count("/jobs") == 1
        assert "/next" not in requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        with SessionLocal() as cleanup_db:
            for rule in cleanup_db.scalars(
                select(ForbiddenTargetRule).where(ForbiddenTargetRule.pattern == blocked_host)
            ).all():
                cleanup_db.delete(rule)
            current_connector = cleanup_db.get(SourceConnector, connector_id)
            current_source = cleanup_db.get(Source, source_id)
            current_admin = cleanup_db.get(User, user_id)
            if current_connector is not None:
                cleanup_db.delete(current_connector)
            if current_source is not None:
                cleanup_db.delete(current_source)
            if current_admin is not None:
                cleanup_db.delete(current_admin)
            cleanup_db.commit()
        engine.dispose()


@pytest.mark.parametrize(
    ("mutation", "expected_category"),
    [
        ("source_configuration", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("source_deactivation", "SOURCE_POLICY_CHANGED"),
        ("connector_configuration", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("connector_deactivation", "CONNECTOR_POLICY_CHANGED"),
        ("permission_review_revocation", "PERMISSION_REVIEW_REQUIRED"),
        ("forbidden_rule_added", "FORBIDDEN_TARGET_RULE_MATCHED"),
    ],
)
def test_registered_scrapy_persists_worker_policy_mutation_before_request(
    monkeypatch, mutation, expected_category
):
    """Persist live-policy denials from the actual Scrapy worker before target fetch."""
    import app.application.jobs.registered_acquisition as registered_module
    import app.infrastructure.acquisition.scrapy as scrapy_module

    engine = create_engine(os.environ["DATABASE_URL"], pool_size=4)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    source_id = connector_id = user_id = None
    requests = []
    blocked_host = f"blocked-{uuid4().hex}.example"

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path == "/robots.txt":
                # Mutate persisted policy after the worker's robots request but before
                # it attempts the configured start URL.
                with SessionLocal() as mutation_db:
                    current_source = mutation_db.get(Source, source_id)
                    current_connector = mutation_db.get(SourceConnector, connector_id)
                    if mutation == "source_configuration":
                        current_source.configuration = {
                            **current_source.configuration,
                            "policy_marker": "changed",
                        }
                    elif mutation == "source_deactivation":
                        current_source.status = "INACTIVE"
                    elif mutation == "connector_configuration":
                        current_connector.configuration = {
                            **current_connector.configuration,
                            "policy_marker": "changed",
                        }
                    elif mutation == "connector_deactivation":
                        current_connector.status = "INACTIVE"
                    elif mutation == "permission_review_revocation":
                        current_source.permission_review_status = "REVOKED"
                    elif mutation == "forbidden_rule_added":
                        mutation_db.add(
                            ForbiddenTargetRule(
                                rule_type="HOST_EXACT",
                                pattern=blocked_host,
                                connector_scope="SCRAPY",
                                reason="Blocked during registered worker execution",
                                active=True,
                                created_by_user_id=user_id,
                            )
                        )
                    else:
                        raise AssertionError(f"unexpected mutation: {mutation}")
                    mutation_db.commit()
                self.send_response(404)
                self.end_headers()
                return
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", f"https://{blocked_host}/private-jobs")
                self.end_headers()
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    start_url = f"http://127.0.0.1:{server.server_port}/redirect"

    with SessionLocal() as seed_db:
        admin = User(display_name=f"Scrapy registered policy admin {uuid4()}")
        seed_db.add(admin)
        seed_db.flush()
        user_id = admin.id
        source = Source(
            name=f"Scrapy registered policy source {uuid4()}",
            source_type="SCRAPY",
            base_url=start_url,
            status="ACTIVE",
            permission_review_status="APPROVED",
            permission_review_reference="scrapy-registered-policy-test-review",
            permission_reviewed_at=datetime.now(timezone.utc),
            configuration={
                "permission_review": {
                    "access_method": "PUBLIC_JOB_BOARD_API",
                    "terms_reference": "https://example.com/terms",
                    "rate_limit_policy": "Respect documented provider rate limits",
                    "rate_limit_reference": "https://example.com/rate-limits",
                }
            },
        )
        seed_db.add(source)
        seed_db.flush()
        connector = SourceConnector(
            source_id=source.id,
            connector_type="SCRAPY",
            connector_version="1.0.0",
            status="ACTIVE",
            configuration={
                "start_urls": [start_url],
                "selectors": {"job_card": "article.job", "title": ".title::text"},
                "allowed_domains": ["127.0.0.1", blocked_host],
                "max_pages": 1,
                "download_delay_seconds": 1.0,
                "user_agent": "JobPunterRegisteredScrapyTest/1.0",
                "timeout_seconds": 30,
            },
            capabilities={},
            limits={},
        )
        seed_db.add(connector)
        seed_db.commit()
        source_id, connector_id = source.id, connector.id

    original_validate_public_host = scrapy_module._validate_public_host
    original_validate_resolved_host = scrapy_module._validate_resolved_host
    original_registered_target_guard = registered_module.assert_target_not_forbidden
    original_subprocess_run = subprocess.run

    def validate_public_host(hostname):
        if hostname == "127.0.0.1":
            return hostname
        return original_validate_public_host(hostname)

    def validate_resolved_host(hostname):
        if hostname in {"127.0.0.1", blocked_host}:
            return None
        return original_validate_resolved_host(hostname)

    def fixture_aware_target_guard(url, rules, *, connector_type):
        if urlparse(url).hostname == "127.0.0.1":
            return
        original_registered_target_guard(url, rules, connector_type=connector_type)

    monkeypatch.setattr(scrapy_module, "_validate_public_host", validate_public_host)
    monkeypatch.setattr(scrapy_module, "_validate_resolved_host", validate_resolved_host)
    monkeypatch.setattr(scrapy_module, "assert_target_not_forbidden", fixture_aware_target_guard)
    monkeypatch.setattr(registered_module, "assert_target_not_forbidden", fixture_aware_target_guard)

    runner = """
from urllib.parse import urlparse
import app.infrastructure.acquisition.scrapy_worker_runtime as runtime
import app.application.jobs.registered_acquisition as registered

original_target_guard = registered.assert_target_not_forbidden
def fixture_aware_target_guard(url, rules, *, connector_type):
    if urlparse(url).hostname == "127.0.0.1":
        return
    original_target_guard(url, rules, connector_type=connector_type)
registered.assert_target_not_forbidden = fixture_aware_target_guard
runtime.assert_target_not_forbidden = fixture_aware_target_guard
original_resolver = runtime._require_public_resolved_address
def fixture_aware_resolver(address):
    if address == "127.0.0.1":
        return address
    return original_resolver(address)
runtime._require_public_resolved_address = fixture_aware_resolver
import app.infrastructure.acquisition.scrapy_worker as worker
worker.main()
"""

    def run_test_worker(_args, **kwargs):
        return original_subprocess_run(
            [sys.executable, "-c", runner],
            **kwargs,
        )

    monkeypatch.setattr(scrapy_module.subprocess, "run", run_test_worker)
    run_key = f"registered-scrapy-{mutation}-{uuid4().hex}"
    try:
        with SessionLocal() as policy_db:
            repository = SqlAlchemyAcquisitionRepository(policy_db)
            service = RegisteredAcquisitionService(repository)
            result = service.run(source_id, connector_id, run_key, {})

            assert result.status == "RESTRICTED"
            assert result.error_category == expected_category
            assert result.acquired_count == 0
            persisted_run = repository.get_run(source_id, run_key)
            assert persisted_run is not None
            assert persisted_run.status == "RESTRICTED"
            assert (persisted_run.metrics or {}).get("error_category") == expected_category
            assert requests == ["/robots.txt"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        with SessionLocal() as cleanup_db:
            from app.infrastructure.db.models import AcquisitionRun

            persisted_run = cleanup_db.scalar(
                select(AcquisitionRun).where(AcquisitionRun.run_key == run_key)
            )
            if persisted_run is not None:
                cleanup_db.delete(persisted_run)
            for rule in cleanup_db.scalars(
                select(ForbiddenTargetRule).where(
                    ForbiddenTargetRule.pattern == blocked_host
                )
            ).all():
                cleanup_db.delete(rule)
            current_connector = cleanup_db.get(SourceConnector, connector_id)
            current_source = cleanup_db.get(Source, source_id)
            current_admin = cleanup_db.get(User, user_id)
            if current_connector is not None:
                cleanup_db.delete(current_connector)
            if current_source is not None:
                cleanup_db.delete(current_source)
            if current_admin is not None:
                cleanup_db.delete(current_admin)
            cleanup_db.commit()
        engine.dispose()
