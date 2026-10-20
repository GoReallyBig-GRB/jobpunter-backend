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
