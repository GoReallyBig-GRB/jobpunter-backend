import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.infrastructure.db.models.governance import AdminAuditEvent, AdminGrant, GovernanceConfigVersion
from app.infrastructure.db.models.identity import User
from app.infrastructure.db.session import get_db
from app.main import app
from app.ports.identity import AuthenticatedPrincipal


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
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


def test_config_publish_retires_previous_version_and_rollback_creates_audited_draft(db):
    admin = User(display_name=f"Governance admin {uuid4()}")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()

    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=admin.id, auth_provider="firebase"
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
        first_draft_response = client.post(
            "/api/v1/admin/configs",
            headers={"x-request-id": "governance-test-1"},
            json={"config_key": "system.discovery", "payload": {"enabled": True}},
        )
        assert first_draft_response.status_code == 201
        first_draft = first_draft_response.json()
        assert first_draft["version"] == 1
        assert first_draft["status"] == "DRAFT"

        first_publish = client.post(
            f"/api/v1/admin/configs/{first_draft['id']}/publish",
            headers={"x-request-id": "governance-test-2"},
        )
        assert first_publish.status_code == 200
        assert first_publish.json()["status"] == "PUBLISHED"
        assert first_publish.json()["published_by_user_id"] == str(admin.id)

        second_draft_response = client.post(
            "/api/v1/admin/configs",
            json={"config_key": "system.discovery", "payload": {"enabled": False}},
        )
        assert second_draft_response.status_code == 201
        second_draft = second_draft_response.json()
        assert second_draft["version"] == 2

        second_publish = client.post(
            f"/api/v1/admin/configs/{second_draft['id']}/publish"
        )
        assert second_publish.status_code == 200
        assert second_publish.json()["status"] == "PUBLISHED"

        db.expire_all()
        versions = db.scalars(
            select(GovernanceConfigVersion)
            .where(GovernanceConfigVersion.config_key == "system.discovery")
            .order_by(GovernanceConfigVersion.version)
        ).all()
        assert [(row.version, row.status) for row in versions] == [
            (1, "RETIRED"),
            (2, "PUBLISHED"),
        ]

        rollback_response = client.post(
            f"/api/v1/admin/configs/{first_draft['id']}/rollback",
            headers={"x-request-id": "governance-test-3"},
        )
        assert rollback_response.status_code == 201
        rollback = rollback_response.json()
        assert rollback["version"] == 3
        assert rollback["status"] == "DRAFT"
        assert rollback["supersedes_id"] == first_draft["id"]
        assert rollback["payload"] == {"enabled": True}

        db.expire_all()
        audit_events = db.scalars(
            select(AdminAuditEvent).order_by(AdminAuditEvent.created_at, AdminAuditEvent.id)
        ).all()
        actions = [event.action for event in audit_events]
        assert actions.count("CONFIG_DRAFT_CREATED") == 2
        assert actions.count("CONFIG_VERSION_PUBLISHED") == 2
        assert actions.count("CONFIG_ROLLBACK_DRAFT_CREATED") == 1
        assert any(event.request_id == "governance-test-1" for event in audit_events)
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()
