"""PostgreSQL policy-mutation acceptance through the JSON API connector path."""
import json
import os
import socket
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.application.jobs.registered_acquisition import build_acquisition_policy_revalidator
from app.infrastructure.acquisition.json_api import JsonApiConnector
from app.infrastructure.db.models import Source, SourceConnector, User
from app.infrastructure.db.models.target_policy import ForbiddenTargetRule
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
from app.ports.acquisition import AcquisitionOutcome


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


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
def test_json_api_blocks_next_page_after_postgres_policy_mutation(
    monkeypatch, mutation, expected_category
):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))
        ],
    )
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=4)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    source_id = connector_id = user_id = None
    endpoint_url = f"https://api-{uuid4().hex}.example.com/jobs"
    second_page_url = f"{endpoint_url}?page=2"

    with SessionLocal() as seed_db:
        admin = User(display_name=f"JSON API policy admin {uuid4()}")
        seed_db.add(admin)
        seed_db.flush()
        user_id = admin.id
        source = Source(
            name=f"JSON API policy source {uuid4()}",
            source_type="JSON_API",
            base_url=endpoint_url,
            status="ACTIVE",
            permission_review_status="APPROVED",
            permission_review_reference="json-api-policy-test-review",
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
            connector_type="JSON_API",
            connector_version="1.0.0",
            status="ACTIVE",
            configuration={"request_method": "GET", "policy_marker": "before"},
            capabilities={"read_only_post": False},
            limits={},
        )
        seed_db.add(connector)
        seed_db.commit()
        source_id, connector_id = source.id, connector.id

    class MutatingClient:
        def __init__(self, mutate):
            self.mutate = mutate
            self.calls = []
            self.mutated = False

        @contextmanager
        def stream(
            self, method, url, *, params=None, headers=None, timeout=None, follow_redirects=None
        ):
            assert method == "GET"
            assert follow_redirects is False
            self.calls.append((url, params))
            if not self.mutated:
                self.mutated = True
                self.mutate()
            payload = {
                "jobs": [
                    {
                        "id": "first-page",
                        "title": "Technical Product Manager",
                        "company": {"name": "Fixture Company"},
                    }
                ]
            }
            response_url = str(url)
            if params:
                response_url = str(httpx.URL(url).copy_merge_params(params))
            yield httpx.Response(
                200,
                content=json.dumps(payload).encode("utf-8"),
                headers={"content-type": "application/json"},
                request=httpx.Request(method, response_url),
            )

    def mutate_policy():
        with SessionLocal() as mutation_db:
            current_source = mutation_db.get(Source, source_id)
            current_connector = mutation_db.get(SourceConnector, connector_id)
            if mutation == "source_configuration":
                current_source.configuration = {
                    **current_source.configuration,
                    "policy_marker": "after",
                }
            elif mutation == "source_deactivation":
                current_source.status = "INACTIVE"
            elif mutation == "connector_configuration":
                current_connector.configuration = {
                    "request_method": "GET",
                    "policy_marker": "after",
                }
            elif mutation == "connector_deactivation":
                current_connector.status = "INACTIVE"
            elif mutation == "permission_review_revocation":
                current_source.permission_review_status = "REVOKED"
            elif mutation == "forbidden_rule_added":
                mutation_db.add(
                    ForbiddenTargetRule(
                        rule_type="URL_EXACT",
                        pattern=second_page_url,
                        connector_scope="JSON_API",
                        reason="Blocked during JSON API acquisition",
                        active=True,
                        created_by_user_id=user_id,
                    )
                )
            else:
                raise AssertionError(f"unexpected test mutation: {mutation}")
            mutation_db.commit()

    try:
        with SessionLocal() as policy_db:
            repository = SqlAlchemyAcquisitionRepository(policy_db)
            source_row = repository.get_source(source_id)
            connector_row = repository.get_connector(connector_id)
            revalidator = build_acquisition_policy_revalidator(
                repository, source_id, connector_id, source_row, connector_row
            )
            client = MutatingClient(mutate_policy)
            result = JsonApiConnector(
                http_client=client, allow_unsafe_http_client=True
            ).acquire(
                source_id=source_id,
                context={
                    "endpoint_url": endpoint_url,
                    "page_param": "page",
                    "page_start": 1,
                    "max_pages": 2,
                    "acquisition_policy_revalidator": revalidator,
                },
            )

        assert result.status == AcquisitionOutcome.PARTIAL
        assert result.error_category == expected_category
        assert [record.external_record_id for record in result.records] == ["first-page"]
        assert len(client.calls) == 1
    finally:
        with SessionLocal() as cleanup_db:
            for rule in cleanup_db.scalars(
                select(ForbiddenTargetRule).where(
                    ForbiddenTargetRule.pattern == second_page_url
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


@pytest.mark.parametrize(
    ("method", "mutation", "expected_category"),
    [
        ("POST", "post_capability_revocation", "ACQUISITION_METHOD_NOT_ALLOWED"),
        ("GET", "get_method_changed", "ACQUISITION_METHOD_NOT_ALLOWED"),
        ("GET", "source_configuration", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("POST", "source_configuration", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("GET", "source_deactivation", "SOURCE_POLICY_CHANGED"),
        ("POST", "source_deactivation", "SOURCE_POLICY_CHANGED"),
        ("GET", "connector_configuration", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("POST", "connector_configuration", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("GET", "connector_deactivation", "CONNECTOR_POLICY_CHANGED"),
        ("POST", "connector_deactivation", "CONNECTOR_POLICY_CHANGED"),
        ("GET", "permission_review_revocation", "PERMISSION_REVIEW_REQUIRED"),
        ("POST", "permission_review_revocation", "PERMISSION_REVIEW_REQUIRED"),
        ("GET", "forbidden_rule_added", "FORBIDDEN_TARGET_RULE_MATCHED"),
        ("POST", "forbidden_rule_added", "FORBIDDEN_TARGET_RULE_MATCHED"),
    ],
)
def test_registered_json_api_persists_policy_denial_before_network_request(
    monkeypatch, method, mutation, expected_category
):
    """GET and approved POST must persist policy denials before transport."""
    from app.application.jobs.registered_acquisition import RegisteredAcquisitionService
    from app.infrastructure.acquisition.public_http import PublicAddressHTTPTransport
    from app.infrastructure.db.models import AcquisitionRun

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))
        ],
    )
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=4)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    source_id = connector_id = user_id = None
    run_key = f"registered-{method.lower()}-policy-mutation-{uuid4().hex}"
    endpoint_url = f"https://api-{uuid4().hex}.example.com/search"

    with SessionLocal() as seed_db:
        admin = User(display_name=f"JSON API policy admin {uuid4()}")
        seed_db.add(admin)
        seed_db.flush()
        user_id = admin.id
        source = Source(
            name=f"JSON API policy source {uuid4()}",
            source_type="JSON_API",
            base_url=endpoint_url,
            status="ACTIVE",
            permission_review_status="APPROVED",
            permission_review_reference="json-api-policy-test-review",
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
        connector_configuration = {
            "endpoint_url": endpoint_url,
            "items_path": "jobs",
            "request_method": method,
        }
        connector_capabilities = {"read_only_post": method == "POST"}
        if method == "POST":
            connector_configuration.update(
                {
                    "read_only_post_action": "search_jobs",
                    "request_body": {"query": "product manager", "limit": 10},
                }
            )
        connector = SourceConnector(
            source_id=source.id,
            connector_type="JSON_API",
            connector_version="1.0.0",
            status="ACTIVE",
            configuration=connector_configuration,
            capabilities=connector_capabilities,
            limits={},
        )
        seed_db.add(connector)
        seed_db.commit()
        source_id, connector_id = source.id, connector.id

    class NoRequestClient:
        def __init__(self):
            class TestPinnedTransport(PublicAddressHTTPTransport):
                def __init__(self):
                    pass

            self._transport = TestPinnedTransport()
            self._mounts = {}
            self.calls = []

        @contextmanager
        def stream(self, request_method, url, **kwargs):
            self.calls.append((request_method, str(url)))
            raise AssertionError("changed request policy must block before transport")
            yield  # pragma: no cover

    class MutatingRepository:
        """Change persisted policy immediately before the registered request guard."""

        def __init__(self, db):
            self.inner = SqlAlchemyAcquisitionRepository(db)
            self.get_source_calls = 0

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def get_source(self, requested_source_id):
            self.get_source_calls += 1
            if self.get_source_calls == 2:
                # The first read snapshots the registered source. The second read
                # is the live guard immediately before it evaluates current policy.
                with SessionLocal() as mutation_db:
                    current_source = mutation_db.get(Source, source_id)
                    current_connector = mutation_db.get(SourceConnector, connector_id)
                    if mutation == "post_capability_revocation":
                        current_connector.capabilities = {"read_only_post": False}
                    elif mutation == "get_method_changed":
                        current_connector.configuration = {
                            **current_connector.configuration,
                            "request_method": "POST",
                            "read_only_post_action": "search_jobs",
                        }
                    elif mutation == "source_configuration":
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
                                rule_type="URL_EXACT",
                                pattern=endpoint_url,
                                connector_scope="JSON_API",
                                reason="Blocked during registered acquisition",
                                active=True,
                                created_by_user_id=user_id,
                            )
                        )
                    else:
                        raise AssertionError(f"unexpected mutation: {mutation}")
                    mutation_db.commit()
            return self.inner.get_source(requested_source_id)

    client = NoRequestClient()
    try:
        with SessionLocal() as policy_db:
            repository = MutatingRepository(policy_db)
            service = RegisteredAcquisitionService(repository)
            monkeypatch.setattr(
                service.registry,
                "create",
                lambda *_: JsonApiConnector(
                    http_client=client, allow_unsafe_http_client=True
                ),
            )
            result = service.run(source_id, connector_id, run_key, {})

            assert result.status == AcquisitionOutcome.RESTRICTED
            assert result.error_category == expected_category
            assert client.calls == []
            persisted_run = repository.get_run(source_id, run_key)
            assert persisted_run is not None
            assert persisted_run.status == "RESTRICTED"
            assert (persisted_run.metrics or {}).get("error_category") == expected_category
    finally:
        with SessionLocal() as cleanup_db:
            persisted_run = cleanup_db.scalar(
                select(AcquisitionRun).where(AcquisitionRun.run_key == run_key)
            )
            if persisted_run is not None:
                cleanup_db.delete(persisted_run)
            for rule in cleanup_db.scalars(
                select(ForbiddenTargetRule).where(
                    ForbiddenTargetRule.pattern == endpoint_url
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


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_registered_json_api_persists_redirect_blocked_outcome_without_following_location(
    monkeypatch, method
):
    """Persist the redirect denial for both methods without requesting Location."""
    from app.application.jobs.registered_acquisition import RegisteredAcquisitionService
    from app.infrastructure.acquisition.public_http import PublicAddressHTTPTransport
    from app.infrastructure.db.models import AcquisitionRun

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))
        ],
    )
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=4)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    source_id = connector_id = None
    run_key = f"registered-{method.lower()}-redirect-{uuid4().hex}"
    endpoint_url = f"https://api-{uuid4().hex}.example.com/search"
    blocked_location = "http://127.0.0.1/admin"

    with SessionLocal() as seed_db:
        source = Source(
            name=f"JSON API redirect source {uuid4()}",
            source_type="JSON_API",
            base_url=endpoint_url,
            status="ACTIVE",
            permission_review_status="APPROVED",
            permission_review_reference="json-api-redirect-test-review",
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
        connector_configuration = {
            "endpoint_url": endpoint_url,
            "items_path": "jobs",
            "request_method": method,
        }
        if method == "POST":
            connector_configuration.update(
                {
                    "read_only_post_action": "search_jobs",
                    "request_body": {"query": "product manager", "limit": 10},
                }
            )
        connector = SourceConnector(
            source_id=source.id,
            connector_type="JSON_API",
            connector_version="1.0.0",
            status="ACTIVE",
            configuration=connector_configuration,
            capabilities={"read_only_post": method == "POST"},
            limits={},
        )
        seed_db.add(connector)
        seed_db.commit()
        source_id, connector_id = source.id, connector.id

    class RedirectClient:
        def __init__(self):
            class TestPinnedTransport(PublicAddressHTTPTransport):
                def __init__(self):
                    pass

            self._transport = TestPinnedTransport()
            self._mounts = {}
            self.calls = []

        @contextmanager
        def stream(
            self, request_method, url, *, params=None, headers=None, timeout=None,
            follow_redirects=None, content=None
        ):
            request_url = str(url)
            if params:
                request_url = str(httpx.URL(url).copy_merge_params(params))
            self.calls.append(
                (request_method, request_url, follow_redirects, content)
            )
            response = httpx.Response(
                302,
                headers={"location": blocked_location},
                request=httpx.Request(request_method, request_url),
            )
            yield response

    client = RedirectClient()
    try:
        with SessionLocal() as policy_db:
            repository = SqlAlchemyAcquisitionRepository(policy_db)
            service = RegisteredAcquisitionService(repository)
            monkeypatch.setattr(
                service.registry,
                "create",
                lambda *_: JsonApiConnector(
                    http_client=client, allow_unsafe_http_client=True
                ),
            )
            result = service.run(source_id, connector_id, run_key, {})

            assert result.status == AcquisitionOutcome.RESTRICTED
            assert result.error_category == "REDIRECT_BLOCKED"
            assert len(client.calls) == 1
            assert client.calls[0][0] == method
            assert client.calls[0][1] == endpoint_url
            assert client.calls[0][2] is False
            persisted_run = repository.get_run(source_id, run_key)
            assert persisted_run is not None
            assert persisted_run.status == "RESTRICTED"
            assert (persisted_run.metrics or {}).get("error_category") == "REDIRECT_BLOCKED"
    finally:
        with SessionLocal() as cleanup_db:
            persisted_run = cleanup_db.scalar(
                select(AcquisitionRun).where(AcquisitionRun.run_key == run_key)
            )
            if persisted_run is not None:
                cleanup_db.delete(persisted_run)
            current_connector = cleanup_db.get(SourceConnector, connector_id)
            current_source = cleanup_db.get(Source, source_id)
            if current_connector is not None:
                cleanup_db.delete(current_connector)
            if current_source is not None:
                cleanup_db.delete(current_source)
            cleanup_db.commit()
        engine.dispose()


@pytest.mark.parametrize(
    ("connector_type", "connector_version"),
    [("RSS_ATOM", "1.0.0"), ("GREENHOUSE_JOB_BOARD", "1")],
)
def test_registered_feed_connectors_persist_redirect_denial_without_following_location(
    monkeypatch, connector_type, connector_version
):
    """RSS/Atom and Greenhouse must persist redirect denial at the registered boundary."""
    from app.application.jobs.registered_acquisition import RegisteredAcquisitionService
    from app.infrastructure.acquisition.public_http import PublicAddressHTTPTransport
    from app.infrastructure.acquisition.rss_atom import RssAtomConnector
    from app.infrastructure.connectors.greenhouse import GreenhouseJobBoardConnector
    from app.infrastructure.db.models import AcquisitionRun

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))
        ],
    )
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=4)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    source_id = connector_id = None
    run_key = f"registered-{connector_type.lower()}-redirect-{uuid4().hex}"
    if connector_type == "RSS_ATOM":
        endpoint_url = f"https://feeds-{uuid4().hex}.example.com/jobs"
        connector_configuration = {"feed_url": endpoint_url}
        connector_factory = RssAtomConnector
    else:
        endpoint_url = "https://boards-api.greenhouse.io/v1/boards/example-board"
        connector_configuration = {"board_token": "example-board"}
        connector_factory = GreenhouseJobBoardConnector
    blocked_location = "http://127.0.0.1/admin"

    with SessionLocal() as seed_db:
        source = Source(
            name=f"{connector_type} redirect source {uuid4()}",
            source_type=connector_type,
            base_url=endpoint_url,
            status="ACTIVE",
            permission_review_status="APPROVED",
            permission_review_reference=f"{connector_type.lower()}-redirect-test-review",
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
            connector_type=connector_type,
            connector_version=connector_version,
            status="ACTIVE",
            configuration=connector_configuration,
            capabilities={"read_only_post": False},
            limits={},
        )
        seed_db.add(connector)
        seed_db.commit()
        source_id, connector_id = source.id, connector.id

    class RedirectClient:
        def __init__(self):
            class TestPinnedTransport(PublicAddressHTTPTransport):
                def __init__(self):
                    pass

            self._transport = TestPinnedTransport()
            self._mounts = {}
            self.calls = []

        @contextmanager
        def stream(
            self, request_method, url, *, params=None, headers=None, timeout=None,
            follow_redirects=None
        ):
            request_url = str(url)
            if params:
                request_url = str(httpx.URL(url).copy_merge_params(params))
            self.calls.append((request_method, request_url, follow_redirects))
            yield httpx.Response(
                302,
                headers={"location": blocked_location},
                request=httpx.Request(request_method, request_url),
            )

    client = RedirectClient()
    try:
        with SessionLocal() as policy_db:
            repository = SqlAlchemyAcquisitionRepository(policy_db)
            service = RegisteredAcquisitionService(repository)
            monkeypatch.setattr(
                service.registry,
                "create",
                lambda *_: connector_factory(
                    http_client=client, allow_unsafe_http_client=True
                ),
            )
            result = service.run(source_id, connector_id, run_key, {})

            assert result.status == AcquisitionOutcome.RESTRICTED
            assert result.error_category == "REDIRECT_BLOCKED"
            assert len(client.calls) == 1
            assert client.calls[0][0] == "GET"
            assert client.calls[0][2] is False
            assert all(call[1] != blocked_location for call in client.calls)
            persisted_run = repository.get_run(source_id, run_key)
            assert persisted_run is not None
            assert persisted_run.status == "RESTRICTED"
            assert (persisted_run.metrics or {}).get("error_category") == "REDIRECT_BLOCKED"
    finally:
        with SessionLocal() as cleanup_db:
            persisted_run = cleanup_db.scalar(
                select(AcquisitionRun).where(AcquisitionRun.run_key == run_key)
            )
            if persisted_run is not None:
                cleanup_db.delete(persisted_run)
            current_connector = cleanup_db.get(SourceConnector, connector_id)
            current_source = cleanup_db.get(Source, source_id)
            if current_connector is not None:
                cleanup_db.delete(current_connector)
            if current_source is not None:
                cleanup_db.delete(current_source)
            cleanup_db.commit()
        engine.dispose()


@pytest.mark.parametrize(
    ("connector_type", "connector_version", "mutation", "expected_category"),
    [
        ("RSS_ATOM", "1.0.0", "source_deactivation", "SOURCE_POLICY_CHANGED"),
        ("RSS_ATOM", "1.0.0", "permission_review_revocation", "PERMISSION_REVIEW_REQUIRED"),
        ("RSS_ATOM", "1.0.0", "forbidden_rule_added", "FORBIDDEN_TARGET_RULE_MATCHED"),
        ("GREENHOUSE_JOB_BOARD", "1", "source_deactivation", "SOURCE_POLICY_CHANGED"),
        ("GREENHOUSE_JOB_BOARD", "1", "permission_review_revocation", "PERMISSION_REVIEW_REQUIRED"),
        ("GREENHOUSE_JOB_BOARD", "1", "forbidden_rule_added", "FORBIDDEN_TARGET_RULE_MATCHED"),
    ],
)
def test_registered_feed_connectors_persist_live_policy_denial_before_request(
    monkeypatch, connector_type, connector_version, mutation, expected_category
):
    """Live policy changes must block registered RSS/Greenhouse requests and persist outcomes."""
    from app.application.jobs.registered_acquisition import RegisteredAcquisitionService
    from app.infrastructure.acquisition.public_http import PublicAddressHTTPTransport
    from app.infrastructure.acquisition.rss_atom import RssAtomConnector
    from app.infrastructure.connectors.greenhouse import GreenhouseJobBoardConnector
    from app.infrastructure.db.models import AcquisitionRun

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))
        ],
    )
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=4)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    source_id = connector_id = user_id = None
    run_key = f"registered-{connector_type.lower()}-{mutation}-{uuid4().hex}"
    if connector_type == "RSS_ATOM":
        endpoint_url = f"https://feeds-{uuid4().hex}.example.com/jobs"
        connector_configuration = {"feed_url": endpoint_url}
        connector_factory = RssAtomConnector
    else:
        endpoint_url = "https://boards-api.greenhouse.io/v1/boards/example-board"
        connector_configuration = {"board_token": "example-board"}
        connector_factory = GreenhouseJobBoardConnector

    with SessionLocal() as seed_db:
        admin = User(display_name=f"{connector_type} policy admin {uuid4()}")
        seed_db.add(admin)
        seed_db.flush()
        user_id = admin.id
        source = Source(
            name=f"{connector_type} policy source {uuid4()}",
            source_type=connector_type,
            base_url=endpoint_url,
            status="ACTIVE",
            permission_review_status="APPROVED",
            permission_review_reference=f"{connector_type.lower()}-policy-test-review",
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
            connector_type=connector_type,
            connector_version=connector_version,
            status="ACTIVE",
            configuration=connector_configuration,
            capabilities={"read_only_post": False},
            limits={},
        )
        seed_db.add(connector)
        seed_db.commit()
        source_id, connector_id = source.id, connector.id

    class NoRequestClient:
        def __init__(self):
            class TestPinnedTransport(PublicAddressHTTPTransport):
                def __init__(self):
                    pass

            self._transport = TestPinnedTransport()
            self._mounts = {}
            self.calls = []

        @contextmanager
        def stream(
            self, request_method, url, *, params=None, headers=None, timeout=None,
            follow_redirects=None
        ):
            self.calls.append((request_method, str(url), follow_redirects))
            raise AssertionError("live policy denial must occur before transport")
            yield  # pragma: no cover

    class MutatingRepository:
        def __init__(self, db):
            self.inner = SqlAlchemyAcquisitionRepository(db)
            self.get_source_calls = 0

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def get_source(self, requested_source_id):
            self.get_source_calls += 1
            if self.get_source_calls == 2:
                with SessionLocal() as mutation_db:
                    current_source = mutation_db.get(Source, source_id)
                    if mutation == "source_deactivation":
                        current_source.status = "INACTIVE"
                    elif mutation == "permission_review_revocation":
                        current_source.permission_review_status = "REVOKED"
                    elif mutation == "forbidden_rule_added":
                        mutation_db.add(
                            ForbiddenTargetRule(
                                rule_type="URL_EXACT",
                                pattern=endpoint_url,
                                connector_scope=connector_type,
                                reason="Blocked during registered feed acquisition",
                                active=True,
                                created_by_user_id=user_id,
                            )
                        )
                    else:
                        raise AssertionError(f"unexpected mutation: {mutation}")
                    mutation_db.commit()
            return self.inner.get_source(requested_source_id)

    client = NoRequestClient()
    try:
        with SessionLocal() as policy_db:
            repository = MutatingRepository(policy_db)
            service = RegisteredAcquisitionService(repository)
            monkeypatch.setattr(
                service.registry,
                "create",
                lambda *_: connector_factory(
                    http_client=client, allow_unsafe_http_client=True
                ),
            )
            result = service.run(source_id, connector_id, run_key, {})

            assert result.status == AcquisitionOutcome.RESTRICTED
            assert result.error_category == expected_category
            assert client.calls == []
            persisted_run = repository.get_run(source_id, run_key)
            assert persisted_run is not None
            assert persisted_run.status == "RESTRICTED"
            assert (persisted_run.metrics or {}).get("error_category") == expected_category
    finally:
        with SessionLocal() as cleanup_db:
            persisted_run = cleanup_db.scalar(
                select(AcquisitionRun).where(AcquisitionRun.run_key == run_key)
            )
            if persisted_run is not None:
                cleanup_db.delete(persisted_run)
            for rule in cleanup_db.scalars(
                select(ForbiddenTargetRule).where(
                    ForbiddenTargetRule.pattern == endpoint_url
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
