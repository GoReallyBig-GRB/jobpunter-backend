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
    AdminAuditEvent, AdminGrant, Source, SourceConnector,
    SourceConnectorConfigurationVersion, User,
)
from app.infrastructure.db.session import get_db
from app.main import app
from app.ports.identity import AuthenticatedPrincipal


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


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

        approved = client.patch(
            f"/api/v1/admin/sources/{source.id}/permission-review",
            headers={"x-request-id": "source-permission-approved"},
            json={
                "status": "APPROVED",
                "reference": "policy-review://approved-source-123",
            },
        )
        assert approved.status_code == 200, approved.text
        approved_data = approved.json()["data"]
        assert approved_data["permission_review_status"] == "APPROVED"
        assert approved_data["permission_review_reference"] == "policy-review://approved-source-123"
        assert approved_data["permission_reviewed_at"] is not None
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
        assert revoked_data["operational_state"] == "RESTRICTED"
        assert revoked_data["last_error_category"] == "PERMISSION_REVIEW_REQUIRED"

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
        assert audit_actions.count("SOURCE_ACQUISITION_BLOCKED_PERMISSION_REVIEW") == 2
    finally:
        client.close()
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
