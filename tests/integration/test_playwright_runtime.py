"""Real Chromium integration tests for Playwright acquisition security boundaries.

These tests use local fixtures only. CI installs Chromium; environments without the browser
binary skip runtime tests rather than treating mocked route tests as browser evidence.
"""
import os
import threading
from datetime import datetime, timezone
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
            if os.environ.get("CI"):
                pytest.fail("Chromium browser binary must be installed in CI")
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

    original_urlparse = playwright_module.urlparse

    def fixture_urlparse(url):
        parsed = original_urlparse(url)
        if parsed.hostname == "127.0.0.1" and parsed.port is not None:
            return parsed._replace(netloc="127.0.0.1")
        return parsed

    monkeypatch.setattr(playwright_module, "urlparse", fixture_urlparse)
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


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL-backed browser acceptance requires DATABASE_URL",
)
@pytest.mark.parametrize(
    (
        "scenario",
        "mutation",
        "expected_status",
        "expected_record_count",
        "expected_request_path",
        "expected_error_category",
    ),
    [
        ("main_frame_redirect", None, AcquisitionOutcome.RESTRICTED, 0, "/redirect", "REDIRECT_BLOCKED"),
        ("off_domain_subresource", None, AcquisitionOutcome.PARTIAL, 1, "/jobs", "REDIRECT_BLOCKED"),
        ("policy_mutation", "source_configuration", AcquisitionOutcome.PARTIAL, 1, "/jobs", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("policy_mutation", "source_deactivation", AcquisitionOutcome.PARTIAL, 1, "/jobs", "SOURCE_POLICY_CHANGED"),
        ("policy_mutation", "connector_configuration", AcquisitionOutcome.PARTIAL, 1, "/jobs", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("policy_mutation", "connector_deactivation", AcquisitionOutcome.PARTIAL, 1, "/jobs", "CONNECTOR_POLICY_CHANGED"),
        ("policy_mutation", "permission_review_revocation", AcquisitionOutcome.PARTIAL, 1, "/jobs", "PERMISSION_REVIEW_REQUIRED"),
        ("policy_mutation", "forbidden_rule_added", AcquisitionOutcome.PARTIAL, 1, "/jobs", "FORBIDDEN_TARGET_RULE_MATCHED"),
    ],
)
def test_registered_playwright_persists_redirect_and_policy_outcomes(
    monkeypatch,
    scenario,
    mutation,
    expected_status,
    expected_record_count,
    expected_request_path,
    expected_error_category,
):
    """Persist real Chromium redirect and live-policy outcomes through registered acquisition."""
    from sqlalchemy import select, create_engine
    from sqlalchemy.orm import sessionmaker

    from app.application.jobs import registered_acquisition as registered_acquisition_module
    from app.application.jobs.registered_acquisition import RegisteredAcquisitionService
    from app.infrastructure.db.models import (
        AcquisitionRun,
        ForbiddenTargetRule,
        JobDiscoveryEvent,
        OutboxEvent,
        RawSourceRecord,
        Source,
        SourceConnector,
        User,
    )
    from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            if os.environ.get("CI"):
                pytest.fail("Chromium browser binary must be installed in CI")
            pytest.skip("Chromium browser binary is not installed in this environment")

    engine = create_engine(os.environ["DATABASE_URL"], pool_size=4)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    source_id = connector_id = user_id = None
    run_key = f"registered-playwright-{scenario}-{mutation}-{uuid4().hex}"
    blocked_host = f"blocked-{uuid4().hex}.example"
    requests = []

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", f"http://{blocked_host}/private-jobs")
                self.end_headers()
                return
            if mutation is not None:
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
                                connector_scope="PLAYWRIGHT",
                                reason="Blocked during browser runtime",
                                active=True,
                                created_by_user_id=user_id,
                            )
                        )
                    else:
                        raise AssertionError(f"unexpected mutation: {mutation}")
                    mutation_db.commit()
            body = f"""<html><body>
                <article class="job"><h2>Fixture Product Manager</h2></article>
                <img src="http://{blocked_host}/tracker.gif">
            </body></html>""".encode()
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

    original_urlparse = playwright_module.urlparse

    def fixture_urlparse(url):
        parsed = original_urlparse(url)
        if parsed.hostname == "127.0.0.1" and parsed.port is not None:
            return parsed._replace(netloc="127.0.0.1")
        return parsed

    monkeypatch.setattr(playwright_module, "urlparse", fixture_urlparse)
    monkeypatch.setattr(playwright_module, "_public_host_resolver_rules", lambda _hosts: "")
    monkeypatch.setattr(playwright_module, "_host_is_allowed", host_is_allowed)
    original_target_guard = registered_acquisition_module.assert_target_not_forbidden

    def fixture_aware_target_guard(url, rules, *, connector_type):
        if urlparse(url).hostname == "127.0.0.1":
            return
        original_target_guard(url, rules, connector_type=connector_type)

    monkeypatch.setattr(
        registered_acquisition_module,
        "assert_target_not_forbidden",
        fixture_aware_target_guard,
    )

    try:
        with SessionLocal() as seed_db:
            admin = User(display_name=f"Playwright policy admin {uuid4()}")
            seed_db.add(admin)
            seed_db.flush()
            user_id = admin.id
            source = Source(
                name=f"Playwright redirect source {uuid4()}",
                source_type="PLAYWRIGHT",
                base_url=start_url,
                status="ACTIVE",
                permission_review_status="APPROVED",
                permission_review_reference="playwright-redirect-test-review",
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
                connector_type="PLAYWRIGHT",
                connector_version="1.0.0",
                status="ACTIVE",
                configuration={
                    "start_urls": [start_url],
                    "allowed_domains": ["127.0.0.1"],
                    "selectors": {"job_card": "article.job", "title": "h2"},
                    "max_pages": 1,
                    "timeout_ms": 5000,
                    "request_delay_seconds": 1,
                    "user_agent": "JobPunterLocalBrowserTest/1.0",
                },
                capabilities={},
                limits={},
            )
            seed_db.add(connector)
            seed_db.commit()
            source_id, connector_id = source.id, connector.id

        with SessionLocal() as policy_db:
            repository = SqlAlchemyAcquisitionRepository(policy_db)
            service = RegisteredAcquisitionService(repository)
            monkeypatch.setattr(
                service.registry, "create", lambda *_: PlaywrightConnector()
            )
            result = service.run(source_id, connector_id, run_key, {})

            assert result.status == expected_status
            assert result.error_category == expected_error_category
            assert result.acquired_count == expected_record_count
            persisted_run = repository.get_run(source_id, run_key)
            assert persisted_run is not None
            assert persisted_run.status == expected_status.value
            assert (persisted_run.metrics or {}).get("error_category") == expected_error_category
            assert requests == [expected_request_path]
    finally:
        if source_id is not None:
            with SessionLocal() as cleanup_db:
                persisted_run = cleanup_db.scalar(
                    select(AcquisitionRun).where(AcquisitionRun.run_key == run_key)
                )
                if persisted_run is not None:
                    raw_records = list(
                        cleanup_db.scalars(
                            select(RawSourceRecord).where(
                                RawSourceRecord.acquisition_run_id == persisted_run.id
                            )
                        ).all()
                    )
                    discovery_events = list(
                        cleanup_db.scalars(
                            select(JobDiscoveryEvent).where(
                                JobDiscoveryEvent.acquisition_run_id == persisted_run.id
                            )
                        ).all()
                    )
                    aggregate_ids = [
                        record.id
                        for record in raw_records
                    ] + [event.id for event in discovery_events]
                    if aggregate_ids:
                        for outbox_event in cleanup_db.scalars(
                            select(OutboxEvent).where(
                                OutboxEvent.aggregate_id.in_(aggregate_ids)
                            )
                        ).all():
                            cleanup_db.delete(outbox_event)
                    for event in discovery_events:
                        cleanup_db.delete(event)
                    for record in raw_records:
                        cleanup_db.delete(record)
                    cleanup_db.delete(persisted_run)
                for rule in cleanup_db.scalars(
                    select(ForbiddenTargetRule).where(
                        ForbiddenTargetRule.pattern == blocked_host,
                        ForbiddenTargetRule.created_by_user_id == user_id,
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
        server.shutdown()
        server.server_close()
