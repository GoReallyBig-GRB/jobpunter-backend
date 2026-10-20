"""PostgreSQL acceptance tests for the canonical discovery-target lifecycle."""
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from app.api.deps import get_principal
from app.infrastructure.db.models import (
    AdminAuditEvent,
    AdminGrant,
    DiscoveryTarget,
    DiscoveryTargetEvent,
    ForbiddenTargetRule,
    Source,
    SourceConnector,
    User,
)
from app.infrastructure.db.session import get_db
from app.main import app
from app.ports.identity import AuthenticatedPrincipal

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL")


@pytest.fixture
def db():
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


@pytest.fixture
def admin_client(db):
    admin = User(display_name=f"Discovery target admin {uuid4()}")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(user_id=admin.id, auth_provider="test")
    connection = db.connection()
    def override_db():
        request_session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield request_session
        finally:
            request_session.close()
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        yield client, admin
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()


def _source_and_connector(db, approved=True, connector_type="SCRAPY", connector_version="1.0.0"):
    source = Source(name=f"Test source {uuid4()}", source_type="PUBLIC_JOB_BOARD",
        base_url="https://jobs.example.org/",
        permission_review_status="APPROVED" if approved else "PENDING",
        permission_review_reference="test-policy-review" if approved else None,
        permission_reviewed_at=datetime.now(timezone.utc) if approved else None,
        configuration={"permission_review": {
            "access_method": "PUBLIC_JOB_BOARD_API",
            "terms_reference": "https://example.org/terms",
            "rate_limit_policy": "10 requests per minute",
            "rate_limit_reference": "https://example.org/rate-limits",
        }} if approved else {})
    db.add(source)
    db.flush()
    configuration = (
        {"search_terms": ["product manager"], "location": "remote"}
        if connector_type == "JOBSPY"
        else {"start_urls": ["https://jobs.example.org/"]}
    )
    connector = SourceConnector(source_id=source.id, connector_type=connector_type,
        connector_version=connector_version, status="ACTIVE", configuration=configuration)
    db.add(connector)
    db.flush()
    return source, connector


def test_create_target_normalizes_url_and_persists_audited_history(db, admin_client):
    client, admin = admin_client
    source, connector = _source_and_connector(db)
    response = client.post("/api/v1/admin/discovery-targets",
        headers={"x-request-id": "target-create-test"},
        json={"source_id": str(source.id), "source_connector_id": str(connector.id),
              "url": "HTTPS://Jobs.Example.Org", "target_kind": "CRAWL", "purpose": "job-listings"})
    assert response.status_code == 201, response.text
    target = response.json()["data"]
    assert target["url"] == "https://jobs.example.org/"
    assert target["status"] == "DRAFT"
    assert target["validation_status"] == "VALIDATED"
    assert target["validation_error_category"] is None
    assert target["validated_at"] is not None
    detail = client.get(f"/api/v1/admin/discovery-targets/{target['id']}")
    assert detail.status_code == 200, detail.text
    assert [event["action"] for event in detail.json()["history"]] == ["CREATED"]
    assert detail.json()["history"][0]["request_id"] == "target-create-test"
    audit = db.scalar(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "DISCOVERY_TARGET_CREATED", AdminAuditEvent.resource_id == target["id"]))
    assert audit is not None and audit.actor_user_id == admin.id
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(update(DiscoveryTargetEvent)
                .where(DiscoveryTargetEvent.target_id == target["id"])
                .values(reason="tampered history"))


def test_activation_requires_source_approval_and_retirement_is_terminal(db, admin_client):
    client, _admin = admin_client
    source, connector = _source_and_connector(db, approved=False)
    created = client.post("/api/v1/admin/discovery-targets",
        json={"source_id": str(source.id), "source_connector_id": str(connector.id),
              "url": "https://jobs.example.org/careers", "target_kind": "SEARCH", "purpose": "career-search"})
    assert created.status_code == 201, created.text
    target_id = created.json()["data"]["id"]
    blocked = client.patch(f"/api/v1/admin/discovery-targets/{target_id}/status",
        json={"status": "ACTIVE", "reason": "Attempt activation before approval"})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "SOURCE_PERMISSION_REVIEW_REQUIRED"
    blocked_event = db.scalar(select(DiscoveryTargetEvent).where(
        DiscoveryTargetEvent.target_id == target_id,
        DiscoveryTargetEvent.action == "BLOCKED",
    ))
    assert blocked_event is not None
    assert blocked_event.previous_status == "DRAFT"
    assert blocked_event.new_status == "DRAFT"
    assert blocked_event.reason == "SOURCE_PERMISSION_REVIEW_REQUIRED"
    blocked_audit = db.scalar(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "DISCOVERY_TARGET_BLOCKED",
        AdminAuditEvent.resource_id == target_id,
        AdminAuditEvent.outcome == "DENIED",
    ))
    assert blocked_audit is not None
    assert blocked_audit.details["error_code"] == "SOURCE_PERMISSION_REVIEW_REQUIRED"
    source.permission_review_status = "APPROVED"
    source.permission_review_reference = "test-policy-review"
    source.permission_reviewed_at = datetime.now(timezone.utc)
    db.flush()
    for target_status, reason in [("ACTIVE", "Permission review approved"),
                                  ("PAUSED", "Pause for review"),
                                  ("RETIRED", "Retire outdated target")]:
        response = client.patch(f"/api/v1/admin/discovery-targets/{target_id}/status",
            json={"status": target_status, "reason": reason})
        assert response.status_code == 200, response.text
    rejected = client.patch(f"/api/v1/admin/discovery-targets/{target_id}/status",
        json={"status": "ACTIVE", "reason": "Should not reactivate"})
    assert rejected.status_code == 409
    events = db.scalars(select(DiscoveryTargetEvent).where(
        DiscoveryTargetEvent.target_id == target_id).order_by(
            DiscoveryTargetEvent.created_at, DiscoveryTargetEvent.id)).all()
    assert [event.action for event in events] == [
        "CREATED", "BLOCKED", "ACTIVATED", "PAUSED", "RETIRED", "BLOCKED"
    ]
    assert events[-1].reason == "INVALID_DISCOVERY_TARGET_TRANSITION"
    assert events[-1].previous_status == "RETIRED"
    assert events[-1].new_status == "RETIRED"
    blocked_audits = db.scalars(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "DISCOVERY_TARGET_BLOCKED",
        AdminAuditEvent.resource_id == target_id,
        AdminAuditEvent.outcome == "DENIED",
    )).all()
    assert len(blocked_audits) == 2


def test_forbidden_target_validation_outcome_and_registration_denial_are_persisted(db, admin_client):
    client, admin = admin_client
    source, connector = _source_and_connector(db)
    created = client.post("/api/v1/admin/discovery-targets",
        json={"source_id": str(source.id), "source_connector_id": str(connector.id),
              "url": "https://jobs.example.org/careers", "target_kind": "CRAWL", "purpose": "careers"})
    assert created.status_code == 201, created.text
    target_id = created.json()["data"]["id"]

    rule = ForbiddenTargetRule(
        rule_type="HOST_EXACT", pattern="jobs.example.org", connector_scope="ALL",
        reason="Policy excludes this host", active=True, created_by_user_id=admin.id,
    )
    db.add(rule)
    db.flush()

    blocked_activation = client.patch(f"/api/v1/admin/discovery-targets/{target_id}/status",
        json={"status": "ACTIVE", "reason": "Attempt activation while excluded"})
    assert blocked_activation.status_code == 409
    assert blocked_activation.json()["detail"]["code"] == "FORBIDDEN_TARGET_RULE_MATCHED"
    db.expire_all()
    target = db.get(DiscoveryTarget, UUID(target_id))
    assert target.status == "DRAFT"
    assert target.validation_status == "BLOCKED"
    assert target.validation_error_category == "FORBIDDEN_TARGET_RULE_MATCHED"
    detail = client.get(f"/api/v1/admin/discovery-targets/{target_id}")
    assert detail.status_code == 200
    assert detail.json()["history"][-1]["reason"] == "FORBIDDEN_TARGET_RULE_MATCHED"

    blocked_registration = client.post(
        "/api/v1/admin/discovery-targets",
        headers={"x-request-id": "blocked-target-registration"},
        json={"source_id": str(source.id), "source_connector_id": str(connector.id),
              "url": "https://jobs.example.org/other", "target_kind": "CRAWL", "purpose": "blocked"},
    )
    assert blocked_registration.status_code == 409
    registration_audit = db.scalar(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "DISCOVERY_TARGET_REGISTRATION_BLOCKED",
        AdminAuditEvent.resource_type == "discovery_target_registration",
        AdminAuditEvent.outcome == "DENIED",
        AdminAuditEvent.request_id == "blocked-target-registration",
    ))
    assert registration_audit is not None
    assert registration_audit.details["error_code"] == "FORBIDDEN_TARGET_RULE_MATCHED"
    assert "url" not in registration_audit.details

    rule.active = False
    db.flush()
    reactivated = client.patch(f"/api/v1/admin/discovery-targets/{target_id}/status",
        json={"status": "ACTIVE", "reason": "Policy exclusion removed"})
    assert reactivated.status_code == 200, reactivated.text
    db.expire_all()
    target = db.get(DiscoveryTarget, UUID(target_id))
    assert target.validation_status == "VALIDATED"
    assert target.validation_error_category is None
    assert target.validated_at is not None


def test_target_registration_rejects_connector_without_url_target_capability(db, admin_client):
    client, _admin = admin_client
    source, connector = _source_and_connector(db, connector_type="JOBSPY")
    response = client.post("/api/v1/admin/discovery-targets",
        json={"source_id": str(source.id), "source_connector_id": str(connector.id),
              "url": "https://jobs.example.org/", "target_kind": "CRAWL", "purpose": "careers"})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "UNSUPPORTED_DISCOVERY_TARGET_KIND"
    audits = db.scalars(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "DISCOVERY_TARGET_REGISTRATION_BLOCKED",
        AdminAuditEvent.outcome == "DENIED",
    )).all()
    audit = next(
        event for event in audits
        if event.details.get("error_code") == "UNSUPPORTED_DISCOVERY_TARGET_KIND"
    )
    assert audit.details["connector_type"] == "JOBSPY"
    assert audit.details["target_kind"] == "CRAWL"
