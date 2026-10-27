import hashlib
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.infrastructure.connectors.greenhouse import GreenhouseJobBoardConnector
from app.infrastructure.db.models import (
    AcquisitionRun, AdminAuditEvent, AdminGrant, Source, SourceConnector,
    SourceConnectorConfigurationVersion, User,
)
from app.infrastructure.db.session import get_db
from app.main import app
from app.ports.identity import AuthenticatedPrincipal


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


def _approved_permission_review_configuration():
    return {
        "permission_review": {
            "access_method": "PUBLIC_JOB_BOARD_API",
            "terms_reference": "https://example.test/source-terms",
            "rate_limit_policy": "Respect documented provider rate limits",
            "rate_limit_reference": "https://example.test/rate-limits",
        }
    }


def test_permission_review_blocks_health_check_until_approved_and_records_health(monkeypatch):
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    admin = User(display_name=f"Source governance admin {uuid4()}")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    source = Source(
        name=f"Governed source {uuid4()}",
        source_type="GREENHOUSE",
        base_url=GreenhouseJobBoardConnector.BASE_URL,
        status="ACTIVE",
        configuration={},
    )
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id,
        connector_type=GreenhouseJobBoardConnector.connector_type,
        connector_version=GreenhouseJobBoardConnector.connector_version,
        status="ACTIVE",
        configuration={"board_token": "test-board"},
        capabilities={"published_jobs": True},
        limits={},
    )
    db.add(connector)
    db.flush()
    canonical_configuration = json.dumps(
        connector.configuration, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    db.add(SourceConnectorConfigurationVersion(
        source_connector_id=connector.id,
        version=1,
        configuration=connector.configuration,
        configuration_fingerprint=hashlib.sha256(
            canonical_configuration.encode("utf-8")
        ).hexdigest(),
        status="PUBLISHED",
        created_by_user_id=admin.id,
        reason="Initial test connector configuration",
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()

    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=admin.id, auth_provider="firebase"
    )

    def override_db():
        request_session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    health_calls = []

    def fake_health_check(self, *, context):
        health_calls.append(context)
        return {"board_name": "test-board", "job_count": 2}

    monkeypatch.setattr(GreenhouseJobBoardConnector, "health_check", fake_health_check)
    try:
        blocked = client.post(
            f"/api/v1/admin/sources/{source.id}/connectors/{connector.id}/health-check",
            headers={"x-request-id": "source-health-blocked"},
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "SOURCE_PERMISSION_REVIEW_REQUIRED"
        assert health_calls == []

        acquisition_blocked = client.post(
            f"/api/v1/admin/sources/{source.id}/acquisition-runs",
            headers={"x-request-id": "source-acquisition-blocked"},
            json={"connector_id": str(connector.id), "run_key": "permission-review-blocked"},
        )
        assert acquisition_blocked.status_code == 409
        assert acquisition_blocked.json()["detail"]["code"] == "SOURCE_PERMISSION_REVIEW_REQUIRED"
        assert health_calls == []

        db.expire_all()
        current = db.get(Source, source.id)
        assert current.operational_state == "RESTRICTED"
        assert current.last_error_category == "PERMISSION_REVIEW_REQUIRED"

        incomplete_approval = client.patch(
            f"/api/v1/admin/sources/{source.id}/permission-review",
            headers={"x-request-id": "source-permission-incomplete"},
            json={
                "status": "APPROVED",
                "reference": "policy-review://approved-source-123",
            },
        )
        assert incomplete_approval.status_code == 422
        db.expire_all()
        pending = db.get(Source, source.id)
        assert pending.permission_review_status == "PENDING"
        assert pending.permission_reviewed_at is None
        assert db.scalars(select(AdminAuditEvent).where(
            AdminAuditEvent.action == "SOURCE_PERMISSION_REVIEW_CHANGED",
            AdminAuditEvent.resource_id == source.id,
        )).all() == []

        approved = client.patch(
            f"/api/v1/admin/sources/{source.id}/permission-review",
            headers={"x-request-id": "source-permission-approved"},
            json={
                "status": "APPROVED",
                "reference": "policy-review://approved-source-123",
                "access_method": "PUBLIC_JOB_BOARD_API",
                "terms_reference": "https://example.test/source-terms",
                "rate_limit_policy": "Configured connector limit: 10 requests per minute",
                "rate_limit_reference": "https://example.test/rate-limits",
                "review_notes": "Synthetic review record for integration test",
            },
        )
        assert approved.status_code == 200, approved.text
        approved_data = approved.json()["data"]
        assert approved_data["permission_review_status"] == "APPROVED"
        assert approved_data["permission_review_reference"] == "policy-review://approved-source-123"
        assert approved_data["permission_reviewed_at"] is not None
        assert approved_data["permission_review_details"] == {
            "access_method": "PUBLIC_JOB_BOARD_API",
            "terms_reference": "https://example.test/source-terms",
            "rate_limit_policy": "Configured connector limit: 10 requests per minute",
            "rate_limit_reference": "https://example.test/rate-limits",
            "review_notes": "Synthetic review record for integration test",
        }
        assert approved_data["operational_state"] == "UNAVAILABLE"

        checked = client.post(
            f"/api/v1/admin/sources/{source.id}/connectors/{connector.id}/health-check",
            headers={"x-request-id": "source-health-success"},
        )
        assert checked.status_code == 200, checked.text
        assert checked.json()["data"]["status"] == "ACTIVE"
        assert checked.json()["data"]["board_name"] == "test-board"
        assert len(health_calls) == 1

        db.expire_all()
        current = db.get(Source, source.id)
        assert current.operational_state == "AVAILABLE"
        assert current.last_error_category is None
        assert current.permission_review_status == "APPROVED"

        actions = db.scalars(select(AdminAuditEvent.action).where(
            AdminAuditEvent.resource_type == "source",
            AdminAuditEvent.resource_id == source.id,
        )).all()
        assert "SOURCE_PERMISSION_REVIEW_CHANGED" in actions
        assert "SOURCE_ACQUISITION_BLOCKED_PERMISSION_REVIEW" in actions
        approval_audit = db.scalar(select(AdminAuditEvent).where(
            AdminAuditEvent.action == "SOURCE_PERMISSION_REVIEW_CHANGED",
            AdminAuditEvent.resource_id == source.id,
            AdminAuditEvent.request_id == "source-permission-approved",
        ))
        assert approval_audit is not None
        assert approval_audit.actor_user_id == admin.id
        assert approval_audit.details["previous"]["status"] == "PENDING"
        assert approval_audit.details["permission_review_details"] == approved_data["permission_review_details"]

        revoked = client.patch(
            f"/api/v1/admin/sources/{source.id}/permission-review",
            headers={"x-request-id": "source-permission-revoked"},
            json={
                "status": "REJECTED",
                "reference": "policy-review://source-revoked-123",
            },
        )
        assert revoked.status_code == 200, revoked.text
        revoked_data = revoked.json()["data"]
        assert revoked_data["permission_review_status"] == "REJECTED"
        assert revoked_data["permission_reviewed_at"] is None
        assert revoked_data["permission_review_details"] == approved_data["permission_review_details"]
        assert revoked_data["operational_state"] == "RESTRICTED"
        assert revoked_data["last_error_category"] == "PERMISSION_REVIEW_REQUIRED"
        revocation_audit = db.scalar(select(AdminAuditEvent).where(
            AdminAuditEvent.action == "SOURCE_PERMISSION_REVIEW_CHANGED",
            AdminAuditEvent.resource_id == source.id,
            AdminAuditEvent.request_id == "source-permission-revoked",
        ))
        assert revocation_audit is not None
        assert revocation_audit.actor_user_id == admin.id
        assert revocation_audit.details["previous"]["details"] == approved_data["permission_review_details"]
        assert revocation_audit.details["permission_review_details"] == approved_data["permission_review_details"]

        blocked_after_revoke = client.post(
            f"/api/v1/admin/sources/{source.id}/acquisition-runs",
            headers={"x-request-id": "source-acquisition-after-revoke"},
            json={
                "connector_id": str(connector.id),
                "run_key": "permission-review-revoked",
            },
        )
        assert blocked_after_revoke.status_code == 409
        assert blocked_after_revoke.json()["detail"]["code"] == "SOURCE_PERMISSION_REVIEW_REQUIRED"
        assert len(health_calls) == 1
        assert db.scalar(select(AcquisitionRun).where(
            AcquisitionRun.source_id == source.id,
            AcquisitionRun.run_key == "permission-review-revoked",
        )) is None

        db.expire_all()
        revoked_source = db.get(Source, source.id)
        assert revoked_source.operational_state == "RESTRICTED"
        assert revoked_source.last_error_category == "PERMISSION_REVIEW_REQUIRED"
        audit_actions = db.scalars(select(AdminAuditEvent.action).where(
            AdminAuditEvent.resource_id == source.id,
        )).all()
        assert audit_actions.count("SOURCE_PERMISSION_REVIEW_CHANGED") == 2
        assert audit_actions.count("SOURCE_ACQUISITION_BLOCKED_PERMISSION_REVIEW") == 3
    finally:
        client.close()
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_postgres_policy_revalidator_observes_permission_revocation_committed_mid_run():
    from app.application.jobs.registered_acquisition import build_acquisition_policy_revalidator
    from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
    from app.ports.acquisition import AcquisitionAdapterError

    engine = create_engine(os.environ["DATABASE_URL"])
    source_id = connector_id = None
    now = datetime.now(timezone.utc)
    try:
        with Session(engine) as seed_db:
            source = Source(
                name=f"Mid-run policy revocation {uuid4()}",
                source_type="JSON_API",
                status="ACTIVE",
                permission_review_status="APPROVED",
                permission_review_reference=f"test-policy-review://{uuid4()}",
                permission_reviewed_at=now,
                configuration=_approved_permission_review_configuration(),
            )
            seed_db.add(source)
            seed_db.flush()
            connector = SourceConnector(
                source_id=source.id,
                connector_type="JSON_API",
                connector_version="1.0.0",
                status="ACTIVE",
                configuration={"endpoint_url": "https://api.example.com/jobs"},
                capabilities={},
                limits={},
            )
            seed_db.add(connector)
            seed_db.commit()
            source_id, connector_id = source.id, connector.id

        with Session(engine) as run_db:
            repository = SqlAlchemyAcquisitionRepository(run_db)
            source = repository.get_source(source_id)
            connector = repository.get_connector(connector_id)
            guard = build_acquisition_policy_revalidator(
                repository, source_id, connector_id, source, connector
            )
            guard(
                url="https://api.example.com/jobs",
                connector_type="JSON_API",
                method="GET",
            )

            with Session(engine) as policy_db:
                current_source = policy_db.get(Source, source_id)
                current_source.permission_review_status = "REVOKED"
                policy_db.commit()

            with pytest.raises(AcquisitionAdapterError) as exc_info:
                guard(
                    url="https://api.example.com/jobs?page=2",
                    connector_type="JSON_API",
                    method="GET",
                )
            assert exc_info.value.error_category == "PERMISSION_REVIEW_REQUIRED"
    finally:
        if source_id is not None:
            with Session(engine) as cleanup_db:
                connector = cleanup_db.get(SourceConnector, connector_id)
                if connector is not None:
                    cleanup_db.delete(connector)
                source = cleanup_db.get(Source, source_id)
                if source is not None:
                    cleanup_db.delete(source)
                cleanup_db.commit()
        engine.dispose()


def test_postgres_policy_revalidator_observes_forbidden_rule_added_mid_run():
    from app.application.jobs.registered_acquisition import build_acquisition_policy_revalidator
    from app.infrastructure.db.models.target_policy import ForbiddenTargetRule
    from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
    from app.ports.acquisition import AcquisitionAdapterError

    engine = create_engine(os.environ["DATABASE_URL"])
    source_id = connector_id = user_id = rule_id = None
    now = datetime.now(timezone.utc)
    try:
        with Session(engine) as seed_db:
            user = User(display_name=f"Policy test admin {uuid4()}")
            seed_db.add(user)
            seed_db.flush()
            source = Source(
                name=f"Mid-run forbidden rule {uuid4()}",
                source_type="JSON_API",
                status="ACTIVE",
                permission_review_status="APPROVED",
                permission_review_reference=f"test-policy-review://{uuid4()}",
                permission_reviewed_at=now,
                configuration=_approved_permission_review_configuration(),
            )
            seed_db.add(source)
            seed_db.flush()
            connector = SourceConnector(
                source_id=source.id,
                connector_type="JSON_API",
                connector_version="1.0.0",
                status="ACTIVE",
                configuration={"endpoint_url": "https://api.example.com/jobs"},
                capabilities={},
                limits={},
            )
            seed_db.add(connector)
            seed_db.commit()
            source_id, connector_id, user_id = source.id, connector.id, user.id

        with Session(engine) as run_db:
            repository = SqlAlchemyAcquisitionRepository(run_db)
            source = repository.get_source(source_id)
            connector = repository.get_connector(connector_id)
            guard = build_acquisition_policy_revalidator(
                repository, source_id, connector_id, source, connector
            )
            guard(
                url="https://api.example.com/jobs",
                connector_type="JSON_API",
                method="GET",
            )

            with Session(engine) as policy_db:
                rule = ForbiddenTargetRule(
                    rule_type="HOST_EXACT",
                    pattern="api.example.com",
                    connector_scope="JSON_API",
                    reason="Synthetic mid-run policy regression",
                    active=True,
                    created_by_user_id=user_id,
                )
                policy_db.add(rule)
                policy_db.commit()
                rule_id = rule.id

            with pytest.raises(AcquisitionAdapterError) as exc_info:
                guard(
                    url="https://api.example.com/jobs?page=2",
                    connector_type="JSON_API",
                    method="GET",
                )
            assert exc_info.value.error_category == "FORBIDDEN_TARGET_RULE_MATCHED"
    finally:
        if source_id is not None:
            with Session(engine) as cleanup_db:
                if rule_id is not None:
                    rule = cleanup_db.get(ForbiddenTargetRule, rule_id)
                    if rule is not None:
                        cleanup_db.delete(rule)
                connector = cleanup_db.get(SourceConnector, connector_id)
                if connector is not None:
                    cleanup_db.delete(connector)
                source = cleanup_db.get(Source, source_id)
                if source is not None:
                    cleanup_db.delete(source)
                if user_id is not None:
                    user = cleanup_db.get(User, user_id)
                    if user is not None:
                        cleanup_db.delete(user)
                cleanup_db.commit()
        engine.dispose()

@pytest.mark.parametrize(
    ("mutation", "expected_category"),
    [
        ("source_configuration", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("connector_configuration", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("source_deactivated", "SOURCE_POLICY_CHANGED"),
        ("connector_deactivated", "CONNECTOR_POLICY_CHANGED"),
        ("read_only_post_revoked", "ACQUISITION_METHOD_NOT_ALLOWED"),
    ],
)
def test_postgres_policy_revalidator_stops_after_persisted_configuration_or_method_change(
    mutation, expected_category
):
    from app.application.jobs.registered_acquisition import build_acquisition_policy_revalidator
    from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
    from app.ports.acquisition import AcquisitionAdapterError

    engine = create_engine(os.environ["DATABASE_URL"])
    source_id = connector_id = None
    now = datetime.now(timezone.utc)
    method = "POST" if mutation == "read_only_post_revoked" else "GET"
    configuration = {
        "endpoint_url": "https://api.example.com/jobs",
        "request_method": method,
    }
    capabilities = {}
    if method == "POST":
        configuration["read_only_post_action"] = "search_jobs"
        configuration["request_body"] = {"query": "jobs"}
        capabilities["read_only_post"] = True

    try:
        with Session(engine) as seed_db:
            source = Source(
                name=f"Mid-run configuration change {uuid4()}",
                source_type="JSON_API",
                status="ACTIVE",
                configuration=_approved_permission_review_configuration(),
                permission_review_status="APPROVED",
                permission_review_reference=f"test-policy-review://{uuid4()}",
                permission_reviewed_at=now,
            )
            seed_db.add(source)
            seed_db.flush()
            connector = SourceConnector(
                source_id=source.id,
                connector_type="JSON_API",
                connector_version="1.0.0",
                status="ACTIVE",
                configuration=configuration,
                capabilities=capabilities,
                limits={},
            )
            seed_db.add(connector)
            seed_db.commit()
            source_id, connector_id = source.id, connector.id

        with Session(engine) as run_db:
            repository = SqlAlchemyAcquisitionRepository(run_db)
            source = repository.get_source(source_id)
            connector = repository.get_connector(connector_id)
            guard = build_acquisition_policy_revalidator(
                repository, source_id, connector_id, source, connector
            )
            guard(
                url="https://api.example.com/jobs",
                connector_type="JSON_API",
                method=method,
            )

            with Session(engine) as policy_db:
                current_source = policy_db.get(Source, source_id)
                current_connector = policy_db.get(SourceConnector, connector_id)
                if mutation == "source_configuration":
                    current_source.configuration = {
                        **_approved_permission_review_configuration(),
                        "operator_change": True,
                    }
                elif mutation == "connector_configuration":
                    current_connector.configuration = {
                        "endpoint_url": "https://other.example.com/jobs",
                        "request_method": "GET",
                    }
                elif mutation == "source_deactivated":
                    current_source.status = "INACTIVE"
                elif mutation == "connector_deactivated":
                    current_connector.status = "INACTIVE"
                elif mutation == "read_only_post_revoked":
                    current_connector.capabilities = {"read_only_post": False}
                policy_db.commit()

            with pytest.raises(AcquisitionAdapterError) as exc_info:
                guard(
                    url="https://api.example.com/jobs?page=2",
                    connector_type="JSON_API",
                    method=method,
                )
            assert exc_info.value.error_category == expected_category
    finally:
        if source_id is not None:
            with Session(engine) as cleanup_db:
                connector = cleanup_db.get(SourceConnector, connector_id)
                if connector is not None:
                    cleanup_db.delete(connector)
                source = cleanup_db.get(Source, source_id)
                if source is not None:
                    cleanup_db.delete(source)
                cleanup_db.commit()
        engine.dispose()


@pytest.mark.parametrize(
    ("http_status", "expected_category", "expected_source_state"),
    [
        (301, "REDIRECT_BLOCKED", "RESTRICTED"),
        (302, "REDIRECT_BLOCKED", "RESTRICTED"),
        (303, "REDIRECT_BLOCKED", "RESTRICTED"),
        (307, "REDIRECT_BLOCKED", "RESTRICTED"),
        (308, "REDIRECT_BLOCKED", "RESTRICTED"),
        (304, "UPSTREAM_REQUEST_FAILED", "FAILED"),
        (500, "UPSTREAM_REQUEST_FAILED", "UNAVAILABLE"),
    ],
)
def test_greenhouse_health_check_persists_http_failure_outcome(
    monkeypatch, http_status, expected_category, expected_source_state
):
    from contextlib import contextmanager

    import httpx

    from app.infrastructure.acquisition.public_http import PublicAddressHTTPTransport

    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    admin = User(display_name=f"Greenhouse redirect health admin {uuid4()}")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    source = Source(
        name=f"Greenhouse redirect health source {uuid4()}",
        source_type="GREENHOUSE",
        base_url=GreenhouseJobBoardConnector.BASE_URL,
        status="ACTIVE",
        permission_review_status="APPROVED",
        permission_review_reference=f"policy-review://greenhouse-{uuid4()}",
        permission_reviewed_at=datetime.now(timezone.utc),
        configuration={
            "permission_review": {
                "access_method": "PUBLIC_JOB_BOARD_API",
                "terms_reference": "https://example.test/source-terms",
                "rate_limit_policy": "Configured connector limit: 10 requests per minute",
                "rate_limit_reference": "https://example.test/rate-limits",
                "review_notes": "Synthetic review record for integration test",
            }
        },
    )
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id,
        connector_type=GreenhouseJobBoardConnector.connector_type,
        connector_version=GreenhouseJobBoardConnector.connector_version,
        status="ACTIVE",
        configuration={"board_token": "test-board"},
        capabilities={"published_jobs": True},
        limits={},
    )
    db.add(connector)
    db.flush()
    canonical_configuration = json.dumps(
        connector.configuration,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    db.add(
        SourceConnectorConfigurationVersion(
            source_connector_id=connector.id,
            version=1,
            configuration=connector.configuration,
            configuration_fingerprint=hashlib.sha256(
                canonical_configuration.encode("utf-8")
            ).hexdigest(),
            status="PUBLISHED",
            created_by_user_id=admin.id,
            reason="Initial test connector configuration",
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=admin.id, auth_provider="firebase"
    )

    def override_db():
        request_session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    blocked_location = "http://127.0.0.1/admin"

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
            self, method, url, *, params=None, headers=None, timeout=None,
            follow_redirects=None
        ):
            self.calls.append((method, str(url), follow_redirects))
            headers = {"location": blocked_location} if 300 <= http_status < 400 else {}
            yield httpx.Response(
                http_status,
                headers=headers,
                request=httpx.Request(method, str(url)),
            )

    redirect_client = RedirectClient()
    original_health_check = GreenhouseJobBoardConnector.health_check

    def health_check_with_redirect(self, *, context):
        test_connector = GreenhouseJobBoardConnector(
            http_client=redirect_client,
            allow_unsafe_http_client=True,
        )
        return original_health_check(test_connector, context=context)

    monkeypatch.setattr(
        GreenhouseJobBoardConnector, "health_check", health_check_with_redirect
    )
    request_id = f"greenhouse-health-http-failure-{http_status}"
    try:
        response = client.post(
            f"/api/v1/admin/sources/{source.id}/connectors/{connector.id}/health-check",
            headers={"x-request-id": request_id},
        )
        assert response.status_code == 502, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "SOURCE_HEALTH_CHECK_FAILED"
        assert "error_code" not in detail
        assert redirect_client.calls == [
            ("GET", "https://boards-api.greenhouse.io/v1/boards/test-board", False)
        ]

        db.expire_all()
        current_source = db.get(Source, source.id)
        current_connector = db.get(SourceConnector, connector.id)
        assert current_source.operational_state == expected_source_state
        assert current_source.last_error_category == expected_category
        assert current_connector.status == "DEGRADED"

        audit = db.scalar(
            select(AdminAuditEvent).where(
                AdminAuditEvent.action == "SOURCE_CONNECTOR_HEALTH_CHECK_FAILED",
                AdminAuditEvent.resource_id == connector.id,
                AdminAuditEvent.request_id == request_id,
            )
        )
        assert audit is not None
        assert audit.actor_user_id == admin.id
        assert audit.outcome == "FAILED"
        assert audit.details["error_code"] == expected_category
        assert blocked_location not in repr(audit.details)
    finally:
        client.close()
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("connector_type", "expected_http_status"),
    [("JSON_API", 200), ("RSS_ATOM", 502)],
)
def test_registered_health_check_redirect_denial_persists_for_json_api_and_rss(
    monkeypatch, connector_type, expected_http_status
):
    from contextlib import contextmanager
    import socket

    import httpx

    from app.infrastructure.acquisition.json_api import JsonApiConnector
    from app.infrastructure.acquisition.public_http import PublicAddressHTTPTransport
    from app.infrastructure.acquisition.rss_atom import RssAtomConnector

    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    admin = User(display_name=f"{connector_type} health redirect admin {uuid4()}")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))

    if connector_type == "JSON_API":
        endpoint_url = f"https://api-{uuid4().hex}.example.com/jobs"
        connector_configuration = {
            "endpoint_url": endpoint_url,
            "items_path": "jobs",
            "request_method": "GET",
        }
        connector_version = JsonApiConnector.connector_version
    else:
        endpoint_url = f"https://feeds-{uuid4().hex}.example.com/feed.xml"
        connector_configuration = {"feed_url": endpoint_url}
        connector_version = RssAtomConnector.connector_version

    source = Source(
        name=f"{connector_type} health redirect source {uuid4()}",
        source_type=connector_type,
        base_url=endpoint_url,
        status="ACTIVE",
        permission_review_status="APPROVED",
        permission_review_reference=f"policy-review://{connector_type.lower()}-{uuid4()}",
        permission_reviewed_at=datetime.now(timezone.utc),
        configuration={
            "permission_review": {
                "access_method": "PUBLIC_JOB_BOARD_API",
                "terms_reference": "https://example.test/source-terms",
                "rate_limit_policy": "Configured connector limit: 10 requests per minute",
                "rate_limit_reference": "https://example.test/rate-limits",
                "review_notes": "Synthetic review record for integration test",
            }
        },
    )
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id,
        connector_type=connector_type,
        connector_version=connector_version,
        status="ACTIVE",
        configuration=connector_configuration,
        capabilities={},
        limits={},
    )
    db.add(connector)
    db.flush()
    canonical_configuration = json.dumps(
        connector.configuration,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    db.add(
        SourceConnectorConfigurationVersion(
            source_connector_id=connector.id,
            version=1,
            configuration=connector.configuration,
            configuration_fingerprint=hashlib.sha256(
                canonical_configuration.encode("utf-8")
            ).hexdigest(),
            status="PUBLISHED",
            created_by_user_id=admin.id,
            reason="Initial test connector configuration",
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=admin.id, auth_provider="firebase"
    )

    def override_db():
        request_session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    blocked_location = "http://127.0.0.1/admin"

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
            self, method, url, *, params=None, headers=None, timeout=None,
            follow_redirects=None, content=None
        ):
            self.calls.append((method, str(url), follow_redirects))
            yield httpx.Response(
                302,
                headers={"location": blocked_location},
                request=httpx.Request(method, str(url)),
            )

    redirect_client = RedirectClient()
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))
        ],
    )

    from app.api.v1 import admin as admin_module

    def create_with_redirect(self, requested_type, requested_version):
        if requested_type == "JSON_API":
            return JsonApiConnector(
                http_client=redirect_client, allow_unsafe_http_client=True
            )
        if requested_type == "RSS_ATOM":
            return RssAtomConnector(
                http_client=redirect_client, allow_unsafe_http_client=True
            )
        raise AssertionError(f"unexpected connector type: {requested_type}")

    monkeypatch.setattr(
        admin_module.AcquisitionConnectorRegistry, "create", create_with_redirect
    )
    request_id = f"{connector_type.lower()}-health-redirect"
    try:
        response = client.post(
            f"/api/v1/admin/sources/{source.id}/connectors/{connector.id}/health-check",
            headers={"x-request-id": request_id},
        )
        assert response.status_code == expected_http_status, response.text
        if connector_type == "JSON_API":
            response_data = response.json()["data"]
            assert response_data["source_operational_state"] == "RESTRICTED"
            assert response_data["source_last_error_category"] == "REDIRECT_BLOCKED"
            assert response_data["status"] == "DEGRADED"
        else:
            assert response.json()["detail"]["code"] == "SOURCE_HEALTH_CHECK_FAILED"

        assert redirect_client.calls == [("GET", endpoint_url, False)]
        db.expire_all()
        current_source = db.get(Source, source.id)
        current_connector = db.get(SourceConnector, connector.id)
        assert current_source.operational_state == "RESTRICTED"
        assert current_source.last_error_category == "REDIRECT_BLOCKED"
        assert current_connector.status == "DEGRADED"

        audit = db.scalar(
            select(AdminAuditEvent).where(
                AdminAuditEvent.resource_id == connector.id,
                AdminAuditEvent.request_id == request_id,
            )
        )
        assert audit is not None
        if connector_type == "JSON_API":
            assert audit.action == "SOURCE_CONNECTOR_HEALTH_CHECKED"
            assert audit.details["error_category"] == "REDIRECT_BLOCKED"
        else:
            assert audit.action == "SOURCE_CONNECTOR_HEALTH_CHECK_FAILED"
            assert audit.details["error_code"] == "REDIRECT_BLOCKED"
        assert blocked_location not in repr(audit.details)
    finally:
        client.close()
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_jobspy_health_check_does_not_report_healthy_when_registered_runs_fail_closed(monkeypatch):
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    admin = User(display_name=f"JobSpy health admin {uuid4()}")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    source = Source(
        name=f"JobSpy source {uuid4()}",
        source_type="JOB_BOARD",
        base_url="https://www.indeed.com/jobs",
        status="ACTIVE",
        permission_review_status="APPROVED",
        permission_review_reference="approved-jobspy-source",
        permission_reviewed_at=datetime.now(timezone.utc),
        configuration={"permission_review": {
            "access_method": "PUBLIC_JOB_BOARD_API",
            "terms_reference": "https://example.test/source-terms",
            "rate_limit_policy": "Configured connector limit: 10 requests per minute",
            "rate_limit_reference": "https://example.test/rate-limits",
        }},
    )
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id,
        connector_type="JOBSPY",
        connector_version="1.0.0",
        status="ACTIVE",
        configuration={"search_terms": ["product manager"], "location": "Nigeria"},
        capabilities={},
        limits={},
    )
    db.add(connector)
    db.flush()
    connector_configuration = dict(connector.configuration)
    canonical_configuration = json.dumps(
        connector_configuration, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    db.add(SourceConnectorConfigurationVersion(
        source_connector_id=connector.id,
        version=1,
        configuration=connector_configuration,
        configuration_fingerprint=hashlib.sha256(
            canonical_configuration.encode("utf-8")
        ).hexdigest(),
        status="PUBLISHED",
        created_by_user_id=admin.id,
        reason="Initial JobSpy health test configuration",
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()

    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=admin.id, auth_provider="firebase"
    )

    def override_db():
        request_session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    monkeypatch.setattr(
        "app.infrastructure.acquisition.jobspy.JobSpyConnector.acquire",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("health check must not bypass registered acquisition policy")
        ),
    )
    try:
        response = client.post(
            f"/api/v1/admin/sources/{source.id}/connectors/{connector.id}/health-check",
            headers={"x-request-id": "jobspy-health-policy"},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "JOBSPY_LIVE_POLICY_UNSUPPORTED"

        db.expire_all()
        persisted_source = db.get(Source, source.id)
        persisted_connector = db.get(SourceConnector, connector.id)
        assert persisted_source.operational_state == "RESTRICTED"
        assert persisted_source.last_error_category == "JOBSPY_LIVE_POLICY_UNSUPPORTED"
        assert persisted_connector.status == "DEGRADED"
        event = db.scalars(select(AdminAuditEvent).where(
            AdminAuditEvent.action == "SOURCE_CONNECTOR_HEALTH_CHECK_BLOCKED",
            AdminAuditEvent.resource_id == connector.id,
        )).one()
        assert event.outcome == "BLOCKED"
        assert event.details["error_code"] == "JOBSPY_LIVE_POLICY_UNSUPPORTED"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
