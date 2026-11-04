"""PostgreSQL acceptance for discovery-strategy validation and publication."""
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.infrastructure.db.models import (
    AdminAuditEvent, AdminGrant, DiscoveryStrategyVersion, User,
)
from app.infrastructure.db.session import get_db
from app.main import app
from app.ports.identity import AuthenticatedPrincipal

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="PostgreSQL integration test requires DATABASE_URL"
)


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
    admin = User(display_name=f"Strategy admin {uuid4()}")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=admin.id, auth_provider="test"
    )
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


def _payload():
    return {
        "schema_version": "v1",
        "roles": [{
            "role_key": "STRUCTURED_ACQUISITION", "order": 1,
            "tool_keys": ["GREENHOUSE_JOB_BOARD"],
            "required_capabilities": ["structured_acquisition"],
            "limits": {"max_requests": 10, "max_records": 100},
        }],
        "global_limits": {"max_requests": 10, "max_records": 100,
                          "max_duration_seconds": 300},
    }


def test_strategy_publish_requires_validation_and_published_content_is_immutable(db, admin_client):
    client, admin = admin_client
    created = client.post("/api/v1/admin/discovery/strategies", json={
        "strategy_key": f"test.{uuid4().hex}", "name": "Controlled acquisition",
        "purpose": "Exercise a bounded structured acquisition role.",
        "payload": _payload(), "reason": "Create deterministic test strategy",
    })
    assert created.status_code == 201, created.text
    strategy_id = created.json()["data"]["id"]
    version_id = created.json()["version"]["id"]
    blocked = client.post(f"/api/v1/admin/discovery/strategies/{strategy_id}/publish",
                          json={"version_id": version_id, "reason": "Premature publish"})
    assert blocked.status_code == 409
    validated = client.post(f"/api/v1/admin/discovery/strategies/{strategy_id}/validate",
                            json={"version_id": version_id, "reason": "Validate contract"})
    assert validated.status_code == 200, validated.text
    published = client.post(f"/api/v1/admin/discovery/strategies/{strategy_id}/publish",
                            json={"version_id": version_id, "reason": "Activate tested strategy"})
    assert published.status_code == 200, published.text
    assert published.json()["data"]["status"] == "PUBLISHED"
    assert published.json()["data"]["published_by_user_id"] == str(admin.id)
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(update(DiscoveryStrategyVersion).where(
                DiscoveryStrategyVersion.id == version_id
            ).values(payload={"tampered": True}))
    audit = db.scalar(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "DISCOVERY_STRATEGY_PUBLISHED",
        AdminAuditEvent.resource_id == version_id,
    ))
    assert audit is not None and audit.actor_user_id == admin.id

