import os
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4
from cryptography.fernet import Fernet

import pytest
from fastapi import Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.exc import DBAPIError

from app.api.deps import get_principal
from app.infrastructure.db.models.governance import AIExecution, AdminAuditEvent, AdminGrant, AdminImpersonationSession, AuditEvent, GovernanceConfigVersion
from app.infrastructure.db.models.opportunities import Application, CandidateEvaluation, SavedJob
from app.infrastructure.db.models.identity import Identity, User
from app.infrastructure.db.models.candidate import CandidateProfile
from app.infrastructure.db.models.preferences import CandidatePreferences
from app.infrastructure.db.models.documents import Document
from app.infrastructure.db.models.jobs import Job
from app.infrastructure.db.models.application_packages import ApplicationPackage, ApplicationPackageItem
from app.infrastructure.db.models.support import SupportTicket
from app.infrastructure.db.models.notifications import (
    Notification, NotificationEmailDelivery, NotificationEmailPreference, SMTPProviderConfiguration,
)
from app.infrastructure.db.models.source import Source, SourceConnector, SourceConnectorConfigurationVersion
from app.infrastructure.db.models.target_policy import ForbiddenTargetRule
from app.infrastructure.db.models.integration_health import IntegrationHealthObservation
from app.infrastructure.db.models.outbox import OutboxEvent
from app.infrastructure.db.models.worker_heartbeat import WorkerHeartbeat
from app.infrastructure.identity.firebase import FirebaseIdentityProvider
from app.config.settings import Settings
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


def test_config_publish_retires_previous_version_and_rollback_activates_new_audited_version(db):
    config_key = f"ai.task.cv_generation.{uuid4().hex}"
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
            json={"config_key": "ai.task.cv_generation", "payload": {"temperature": 0.2}},
        )
        assert first_draft_response.status_code == 201
        first_draft = first_draft_response.json()
        assert first_draft["version"] == 1
        assert first_draft["status"] == "DRAFT"
        assert len(first_draft["payload_fingerprint"]) == 64
        assert all(c in "0123456789abcdef" for c in first_draft["payload_fingerprint"])

        first_publish = client.post(
            f"/api/v1/admin/configs/{first_draft['id']}/publish",
            headers={"x-request-id": "governance-test-2"},
        )
        assert first_publish.status_code == 200
        assert first_publish.json()["status"] == "PUBLISHED"
        assert first_publish.json()["published_by_user_id"] == str(admin.id)

        second_draft_response = client.post(
            "/api/v1/admin/configs",
            json={"config_key": "ai.task.cv_generation", "payload": {"temperature": 0.4}},
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
            .where(GovernanceConfigVersion.config_key == "ai.task.cv_generation")
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
        assert rollback["status"] == "PUBLISHED"
        assert rollback["supersedes_id"] == first_draft["id"]
        assert rollback["payload"] == {"temperature": 0.2}
        assert rollback["payload_fingerprint"] == first_draft["payload_fingerprint"]
        assert rollback["published_by_user_id"] == str(admin.id)
        assert rollback["published_at"] is not None

        db.expire_all()
        versions = db.scalars(
            select(GovernanceConfigVersion)
            .where(GovernanceConfigVersion.config_key == "ai.task.cv_generation")
            .order_by(GovernanceConfigVersion.version)
        ).all()
        assert [(row.version, row.status) for row in versions] == [
            (1, "RETIRED"),
            (2, "RETIRED"),
            (3, "PUBLISHED"),
        ]
        resource_ids = {first_draft["id"], second_draft["id"], rollback["id"]}
        audit_events = db.scalars(
            select(AdminAuditEvent)
            .where(AdminAuditEvent.resource_id.in_(resource_ids))
            .order_by(AdminAuditEvent.created_at, AdminAuditEvent.id)
        ).all()
        actions = [event.action for event in audit_events]
        assert actions.count("CONFIG_DRAFT_CREATED") == 2
        assert actions.count("CONFIG_VERSION_PUBLISHED") == 2
        assert actions.count("CONFIG_ROLLBACK_ACTIVATED") == 1
        assert any(event.request_id == "governance-test-1" for event in audit_events)
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()




def test_decision_rule_config_uses_shared_lifecycle_and_rejects_unsafe_payloads(db):
    config_key = "decision.rules.candidate_evaluation"
    admin = User(display_name=f"Decision rules admin {uuid4()}")
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
        invalid_thresholds = client.post(
            "/api/v1/admin/configs",
            json={
                "config_key": config_key,
                "payload": {"apply_min_score": 35, "review_min_score": 35},
            },
        )
        assert invalid_thresholds.status_code == 422

        attempted_hard_rule_override = client.post(
            "/api/v1/admin/configs",
            json={
                "config_key": config_key,
                "payload": {
                    "apply_min_score": 80,
                    "review_min_score": 35,
                    "excluded_industries": [],
                },
            },
        )
        assert attempted_hard_rule_override.status_code == 422

        draft_response = client.post(
            "/api/v1/admin/configs",
            json={
                "config_key": config_key,
                "payload": {"apply_min_score": 80, "review_min_score": 35},
            },
        )
        assert draft_response.status_code == 201, draft_response.text
        draft = draft_response.json()
        assert draft["status"] == "DRAFT"
        assert draft["payload"] == {"apply_min_score": 80, "review_min_score": 35}
        assert len(draft["payload_fingerprint"]) == 64

        published_response = client.post(f"/api/v1/admin/configs/{draft['id']}/publish")
        assert published_response.status_code == 200, published_response.text
        published = published_response.json()
        assert published["status"] == "PUBLISHED"
        assert published["published_by_user_id"] == str(admin.id)
        assert published["published_at"] is not None

        db.expire_all()
        stored = db.get(GovernanceConfigVersion, draft["id"])
        assert stored is not None
        assert stored.payload == {"apply_min_score": 80, "review_min_score": 35}
        assert stored.payload_fingerprint == draft["payload_fingerprint"]
        actions = db.scalars(select(AdminAuditEvent)).all()
        assert any(event.action == "CONFIG_DRAFT_CREATED" and event.resource_id == stored.id for event in actions)
        assert any(event.action == "CONFIG_VERSION_PUBLISHED" and event.resource_id == stored.id for event in actions)

        evaluations_before = db.scalar(select(func.count(CandidateEvaluation.id))) or 0
        preview = client.post(
            "/api/v1/admin/decision-rules/preview",
            json={
                "rules": {"apply_min_score": 90, "review_min_score": 35},
                "job": {
                    "canonical_title": "Operations Manager",
                    "application_url": "https://example.test/apply",
                    "lifecycle_status": "ACTIVE",
                    "freshness_status": "FRESH",
                    "application_route_verification": {
                        "status": "ACTIVE",
                        "checked_url": "https://example.test/apply",
                    },
                    "metadata": {},
                },
                "job_description": (
                    "Responsibilities include managing delivery and coordinating teams. "
                    "Requirements include relevant experience and analytical skills. "
                ) * 5,
                "description_version_id": "preview-jd-v1",
                "description_completeness": "COMPLETE",
                "candidate_evidence": [{"id": "ev-1", "content": "Approved delivery evidence."}],
                "preferences": {},
                "semantic_matches": [
                    {
                        "requirement_id": f"REQ-{index}",
                        "requirement_text": f"Requirement {index}",
                        "status": "DIRECT" if index < 5 else "MISSING",
                        "rationale": "Grounded preview match.",
                        "evidence_refs": ["ev-1"] if index < 5 else [],
                        "mandatory": index < 5,
                    }
                    for index in range(1, 6)
                ],
            },
        )
        assert preview.status_code == 200, preview.text
        preview_data = preview.json()
        assert preview_data["preview_only"] is True
        assert preview_data["result"]["policy_version"] == "candidate-evaluation-preview"
        assert preview_data["result"]["outcome"] == "REVIEW"
        assert preview_data["result"]["score"] == 80
        assert db.scalar(select(func.count(CandidateEvaluation.id))) == evaluations_before

        second_draft_response = client.post(
            "/api/v1/admin/configs",
            json={
                "config_key": config_key,
                "payload": {"apply_min_score": 85, "review_min_score": 30},
            },
        )
        assert second_draft_response.status_code == 201, second_draft_response.text
        second_draft = second_draft_response.json()
        assert second_draft["version"] == draft["version"] + 1
        second_publish = client.post(f"/api/v1/admin/configs/{second_draft['id']}/publish")
        assert second_publish.status_code == 200, second_publish.text

        rollback_response = client.post(f"/api/v1/admin/configs/{draft['id']}/rollback")
        assert rollback_response.status_code == 201, rollback_response.text
        rollback = rollback_response.json()
        assert rollback["version"] == second_draft["version"] + 1
        assert rollback["status"] == "PUBLISHED"
        assert rollback["supersedes_id"] == draft["id"]
        assert rollback["payload"] == {"apply_min_score": 80, "review_min_score": 35}
        db.expire_all()
        versions = db.scalars(
            select(GovernanceConfigVersion)
            .where(GovernanceConfigVersion.config_key == config_key)
            .order_by(GovernanceConfigVersion.version)
        ).all()
        created_versions = [
            row for row in versions
            if row.version >= draft["version"]
            and row.version <= rollback["version"]
        ]
        assert [(row.version, row.status) for row in created_versions] == [
            (draft["version"], "RETIRED"),
            (second_draft["version"], "RETIRED"),
            (rollback["version"], "PUBLISHED"),
        ]
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()




def test_concurrent_decision_rule_publications_leave_exactly_one_active_version():
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=5, max_overflow=2)
    factory = sessionmaker(engine, expire_on_commit=False)
    user_id = None
    config_ids = []
    config_key = "decision.rules.candidate_evaluation"
    now = datetime.now(timezone.utc)
    try:
        with factory() as setup:
            existing_versions = setup.scalars(
                select(GovernanceConfigVersion)
                .where(GovernanceConfigVersion.config_key == config_key)
                .order_by(GovernanceConfigVersion.version)
            ).all()
            base_version = max((row.version for row in existing_versions), default=0)
            active_before = [row for row in existing_versions if row.status == "PUBLISHED"]
            assert len(active_before) <= 1, "decision-rule config has multiple published versions"
            previous_active_id = active_before[0].id if active_before else None
            admin = User(display_name=f"Concurrent rules admin {uuid4()}")
            setup.add(admin)
            setup.flush()
            user_id = admin.id
            setup.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
            first = GovernanceConfigVersion(
                config_key=config_key,
                version=base_version + 1,
                status="DRAFT",
                payload={"apply_min_score": 80, "review_min_score": 35},
                payload_fingerprint="a" * 64,
                created_by_user_id=admin.id,
                created_at=now,
            )
            second = GovernanceConfigVersion(
                config_key=config_key,
                version=base_version + 2,
                status="DRAFT",
                payload={"apply_min_score": 85, "review_min_score": 35},
                payload_fingerprint="b" * 64,
                created_by_user_id=admin.id,
                created_at=now,
            )
            third = GovernanceConfigVersion(
                config_key=config_key,
                version=base_version + 3,
                status="DRAFT",
                payload={"apply_min_score": 90, "review_min_score": 35},
                payload_fingerprint="c" * 64,
                created_by_user_id=admin.id,
                created_at=now,
            )
            setup.add_all([first, second, third])
            setup.commit()
            config_ids = [first.id, second.id, third.id]
            admin_id = admin.id

        app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
            user_id=admin_id, auth_provider="test"
        )

        def override_db():
            request_session = factory()
            try:
                yield request_session
            finally:
                request_session.close()

        app.dependency_overrides[get_db] = override_db

        def publish(config_id):
            with TestClient(app) as client:
                return client.post(f"/api/v1/admin/configs/{config_id}/publish").status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(publish, config_ids[1:]))

        assert all(code in {200, 409} for code in outcomes), outcomes
        assert 200 in outcomes

        with factory() as verify:
            versions = verify.scalars(
                select(GovernanceConfigVersion)
                .where(GovernanceConfigVersion.config_key == config_key)
                .order_by(GovernanceConfigVersion.version)
            ).all()
            created_versions = [row for row in versions if row.id in config_ids]
            assert len(created_versions) == 3
            assert sum(row.status == "PUBLISHED" for row in versions) == 1
            if previous_active_id is not None:
                previous_active = verify.get(GovernanceConfigVersion, previous_active_id)
                assert previous_active.status == "RETIRED"
            assert all(row.status in {"PUBLISHED", "RETIRED", "DRAFT"} for row in versions)
            published_events = verify.scalars(select(AdminAuditEvent).where(
                AdminAuditEvent.action == "CONFIG_VERSION_PUBLISHED",
                AdminAuditEvent.resource_id.in_(config_ids[1:]),
            )).all()
            assert len(published_events) == outcomes.count(200)
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        # Governance configuration versions are append-only and protected by a
        # database trigger. Keep their referenced identity, but revoke its admin
        # grant so this test cannot affect last-admin protection in later tests.
        if user_id is not None:
            with factory.begin() as cleanup:
                grant = cleanup.scalar(select(AdminGrant).where(AdminGrant.user_id == user_id))
                if grant is not None:
                    grant.active = False
                    grant.revoked_at = datetime.now(timezone.utc)
        engine.dispose()



def test_admin_grant_lifecycle_is_audited_and_prevents_self_elevation_or_lockout(db):
    admin = User(display_name=f"Granting admin {uuid4()}")
    target_admin = User(display_name=f"Target admin {uuid4()}")
    candidate = User(display_name=f"Ordinary candidate {uuid4()}")
    disabled = User(display_name=f"Disabled target {uuid4()}", status="DISABLED")
    db.add_all([admin, target_admin, candidate, disabled])
    db.flush()
    original_grant = AdminGrant(user_id=admin.id, role="ADMIN", active=True)
    db.add(original_grant)
    db.flush()

    principal_holder = {
        "value": AuthenticatedPrincipal(user_id=admin.id, auth_provider="firebase")
    }
    app.dependency_overrides[get_principal] = lambda: principal_holder["value"]
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
        grant_response = client.post(
            "/api/v1/admin/grants",
            headers={"x-request-id": "admin-grant-create"},
            json={"user_id": str(target_admin.id), "role": "ADMIN"},
        )
        assert grant_response.status_code == 201
        grant = grant_response.json()
        assert grant["user_id"] == str(target_admin.id)
        assert grant["active"] is True
        assert grant["granted_by_user_id"] == str(admin.id)

        duplicate_response = client.post(
            "/api/v1/admin/grants",
            json={"user_id": str(target_admin.id), "role": "ADMIN"},
        )
        assert duplicate_response.status_code == 409

        disabled_response = client.post(
            "/api/v1/admin/grants",
            json={"user_id": str(disabled.id), "role": "ADMIN"},
        )
        assert disabled_response.status_code == 409

        list_response = client.get("/api/v1/admin/grants")
        assert list_response.status_code == 200
        assert {row["user_id"] for row in list_response.json()} >= {
            str(admin.id), str(target_admin.id)
        }

        revoke_response = client.post(
            f"/api/v1/admin/grants/{grant['id']}/revoke",
            headers={"x-request-id": "admin-grant-revoke"},
        )
        assert revoke_response.status_code == 200
        assert revoke_response.json()["active"] is False
        assert revoke_response.json()["revoked_at"] is not None

        # A revoked administrator loses authorization immediately.
        principal_holder["value"] = AuthenticatedPrincipal(
            user_id=target_admin.id, auth_provider="firebase"
        )
        denied_response = client.get("/api/v1/admin/configs")
        assert denied_response.status_code == 403

        # An ordinary candidate cannot grant themselves administrative access.
        principal_holder["value"] = AuthenticatedPrincipal(
            user_id=candidate.id, auth_provider="firebase"
        )
        self_grant_response = client.post(
            "/api/v1/admin/grants",
            json={"user_id": str(candidate.id), "role": "ADMIN"},
        )
        assert self_grant_response.status_code == 403
        assert db.scalar(select(AdminGrant).where(
            AdminGrant.user_id == candidate.id,
            AdminGrant.role == "ADMIN",
        )) is None

        # An existing admin can reactivate a revoked grant, with a new audit event.
        principal_holder["value"] = AuthenticatedPrincipal(
            user_id=admin.id, auth_provider="firebase"
        )
        reactivate_response = client.post(
            "/api/v1/admin/grants",
            headers={"x-request-id": "admin-grant-reactivate"},
            json={"user_id": str(target_admin.id), "role": "ADMIN"},
        )
        assert reactivate_response.status_code == 201
        assert reactivate_response.json()["active"] is True
        assert reactivate_response.json()["revoked_at"] is None
        assert reactivate_response.json()["id"] == grant["id"]

        second_revoke = client.post(f"/api/v1/admin/grants/{grant['id']}/revoke")
        assert second_revoke.status_code == 200

        final_admin_revoke = client.post(
            f"/api/v1/admin/grants/{original_grant.id}/revoke"
        )
        assert final_admin_revoke.status_code == 409
        db.refresh(original_grant)
        assert original_grant.active is True

        db.expire_all()
        grant_audit = db.scalars(select(AdminAuditEvent).where(
            AdminAuditEvent.resource_type == "admin_grant",
            AdminAuditEvent.resource_id.in_([str(grant["id"]), str(original_grant.id)]),
        )).all()
        assert [event.action for event in grant_audit].count("ADMIN_GRANT_CREATED") == 1
        assert [event.action for event in grant_audit].count("ADMIN_GRANT_REVOKED") == 2
        assert [event.action for event in grant_audit].count("ADMIN_GRANT_REACTIVATED") == 1
        assert any(event.request_id == "admin-grant-create" for event in grant_audit)
        assert any(event.request_id == "admin-grant-revoke" for event in grant_audit)
        assert any(event.request_id == "admin-grant-reactivate" for event in grant_audit)
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()



def test_concurrent_admin_revocations_cannot_remove_all_active_admins():
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=8)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    admin_a = admin_b = None
    grant_a = grant_b = None
    try:
        with SessionLocal() as seed:
            admin_a = User(display_name=f"Concurrent admin A {uuid4()}")
            admin_b = User(display_name=f"Concurrent admin B {uuid4()}")
            seed.add_all([admin_a, admin_b])
            seed.flush()
            grant_a = AdminGrant(user_id=admin_a.id, role="ADMIN", active=True)
            grant_b = AdminGrant(user_id=admin_b.id, role="ADMIN", active=True)
            seed.add_all([grant_a, grant_b])
            seed.commit()
            admin_a_id, admin_b_id = admin_a.id, admin_b.id
            grant_a_id, grant_b_id = grant_a.id, grant_b.id

        def override_principal(x_test_user_id: str = Header(alias="x-test-user-id")):
            return AuthenticatedPrincipal(
                user_id=UUID(x_test_user_id), auth_provider="test"
            )

        def override_db():
            with SessionLocal() as session:
                yield session

        app.dependency_overrides[get_principal] = override_principal
        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)

        def revoke(actor_id, target_grant_id):
            return client.post(
                f"/api/v1/admin/grants/{target_grant_id}/revoke",
                headers={"x-test-user-id": str(actor_id)},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(revoke, admin_b_id, grant_a_id),
                executor.submit(revoke, admin_a_id, grant_b_id),
            ]
            responses = [future.result(timeout=30) for future in futures]

        assert sorted(response.status_code for response in responses) == [200, 403]
        with SessionLocal() as verify:
            active = verify.scalars(select(AdminGrant).where(
                AdminGrant.role == "ADMIN",
                AdminGrant.active.is_(True),
                AdminGrant.user_id.in_([admin_a_id, admin_b_id]),
            )).all()
            assert len(active) == 1
            audit_events = verify.scalars(select(AdminAuditEvent).where(
                AdminAuditEvent.resource_type == "admin_grant",
                AdminAuditEvent.resource_id.in_([grant_a_id, grant_b_id]),
            )).all()
            assert [row.action for row in audit_events].count("ADMIN_GRANT_REVOKED") == 1
        client.close()
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        with SessionLocal() as cleanup:
            grant_ids = [
                item.id for item in (grant_a, grant_b) if item is not None
            ]
            # Audit events are append-only and reference their actors. Keep the
            # evidence intact and retire the test grants instead of deleting history.
            if grant_ids:
                cleanup.execute(
                    update(AdminGrant)
                    .where(AdminGrant.id.in_(grant_ids))
                    .values(active=False, revoked_at=datetime.now(timezone.utc))
                )
            cleanup.commit()
        engine.dispose()


def test_governance_config_payload_is_database_immutable(db):
    actor = User(display_name=f"Config owner {uuid4()}")
    db.add(actor)
    db.flush()
    config = GovernanceConfigVersion(
        config_key="ai.task.cover_letter",
        version=1,
        status="DRAFT",
        payload={"provider": "google", "temperature": 0.2},
        payload_fingerprint="a" * 64,
        created_by_user_id=actor.id,
    )
    db.add(config)
    db.flush()

    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(
                update(GovernanceConfigVersion)
                .where(GovernanceConfigVersion.id == config.id)
                .values(payload={"provider": "google", "temperature": 0.9})
            )

    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(
                GovernanceConfigVersion.__table__.delete().where(
                    GovernanceConfigVersion.id == config.id
                )
            )

    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(text("TRUNCATE TABLE governance_config_versions"))

    db.expire_all()
    persisted = db.get(GovernanceConfigVersion, config.id)
    assert persisted is not None
    assert persisted.payload == {"provider": "google", "temperature": 0.2}
    assert persisted.payload_fingerprint == "a" * 64



def test_admin_ai_execution_list_returns_complete_governance_trace(db):
    admin = User(display_name=f"AI governance admin {uuid4()}")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    execution = AIExecution(
        user_id=admin.id,
        task="cover_letter",
        task_policy_version="v1",
        system_policy_version="v1",
        input_contract_version="v3",
        input_fingerprint="a" * 64,
        output_contract_version="v1",
        config_fingerprint="b" * 64,
        contract_snapshot={
            "task": "cover_letter",
            "task_policy_version": "v1",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        },
        output_fingerprint="c" * 64,
        provider="google",
        model="gemini-test",
        status="FAILED",
        fallback_attempted=True,
        fallback_used=False,
        error_code="AI_INVALID_OUTPUT",
        validation_result={"status": "REJECTED", "category": "OUTPUT_CONTRACT_INVALID", "contract_version": "v1"},
    )
    db.add(execution)
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
    try:
        response = TestClient(app).get("/api/v1/admin/ai-executions")
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == str(execution.id)
    assert row["input_contract_version"] == "v3"
    assert row["input_fingerprint"] == "a" * 64
    assert row["contract_snapshot"]["task"] == "cover_letter"
    assert row["contract_snapshot"]["input_schema"] == {"type": "object"}
    assert row["output_fingerprint"] == "c" * 64
    assert row["fallback_attempted"] is True
    assert row["fallback_used"] is False
    assert row["error_code"] == "AI_INVALID_OUTPUT"
    assert row["validation_result"] == {
        "status": "REJECTED",
        "category": "OUTPUT_CONTRACT_INVALID",
        "contract_version": "v1",
    }
    assert "prompt" not in row
    assert "raw_text" not in row


def test_ai_execution_contract_snapshot_is_immutable_at_database_boundary(db):
    execution = AIExecution(
        task="cover_letter",
        task_policy_version="v1",
        system_policy_version="v1",
        input_contract_version="v4",
        input_fingerprint="a" * 64,
        output_contract_version="v2",
        config_fingerprint="b" * 64,
        contract_snapshot={"task": "cover_letter", "input_schema": {"type": "object"}},
        provider="google",
        model="gemini-test",
        status="RUNNING",
    )
    db.add(execution)
    db.flush()

    with pytest.raises(DBAPIError):
        db.execute(
            update(AIExecution)
            .where(AIExecution.id == execution.id)
            .values(contract_snapshot={"task": "tampered"})
        )
    db.rollback()


def test_ai_execution_output_fingerprint_is_immutable_once_recorded(db):
    execution = AIExecution(
        task="cover_letter",
        task_policy_version="v1",
        system_policy_version="v1",
        input_contract_version="v4",
        input_fingerprint="a" * 64,
        output_contract_version="v2",
        config_fingerprint="b" * 64,
        contract_snapshot={"task": "cover_letter"},
        provider="google",
        model="gemini-test",
        status="RUNNING",
    )
    db.add(execution)
    db.flush()

    db.execute(
        update(AIExecution)
        .where(AIExecution.id == execution.id)
        .values(output_fingerprint="c" * 64)
    )
    with pytest.raises(DBAPIError):
        db.execute(
            update(AIExecution)
            .where(AIExecution.id == execution.id)
            .values(output_fingerprint="d" * 64)
        )
    db.rollback()

def test_admin_suspension_is_audited_and_blocks_subsequent_candidate_authentication(db):
    """Exercise the persisted account gate across Admin mutation and Firebase identity resolution."""
    admin = User(display_name=f"Lifecycle admin {uuid4()}")
    candidate = User(
        status="ACTIVE",
        display_name=f"Lifecycle candidate {uuid4()}",
        email=f"{uuid4()}@example.test",
        onboarding_status="NOT_STARTED",
    )
    firebase_uid = f"lifecycle-{uuid4()}"
    db.add_all([admin, candidate])
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.add(
        Identity(
            user_id=candidate.id,
            provider="FIREBASE",
            provider_subject=firebase_uid,
        )
    )
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
        response = client.patch(
            f"/api/v1/admin/users/{candidate.id}/status",
            headers={"x-request-id": "account-lifecycle-integration"},
            json={
                "target_status": "SUSPENDED",
                "expected_revision": candidate.revision,
                "reason": "Verify suspended accounts fail closed",
            },
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "SUSPENDED"
    assert response.json()["revision"] == candidate.revision + 1

    db.expire_all()
    persisted = db.get(User, candidate.id)
    assert persisted is not None
    assert persisted.status == "SUSPENDED"

    audit = db.scalar(
        select(AdminAuditEvent).where(
            AdminAuditEvent.action == "USER_STATUS_CHANGED",
            AdminAuditEvent.resource_id == candidate.id,
            AdminAuditEvent.request_id == "account-lifecycle-integration",
        )
    )
    assert audit is not None
    assert audit.actor_user_id == admin.id
    assert audit.outcome == "SUCCESS"
    assert audit.details["previous_status"] == "ACTIVE"
    assert audit.details["new_status"] == "SUSPENDED"
    assert audit.details["reason"] == "Verify suspended accounts fail closed"

    provider = FirebaseIdentityProvider(
        token_verifier=lambda _token: {"uid": firebase_uid}
    )
    with pytest.raises(ValueError, match="not active"):
        provider.authenticate("Bearer verified-token", db)


@pytest.mark.parametrize(
    ("case", "revision_offset", "target_status", "reason", "expected_status", "expected_code"),
    [
        ("no-op", 0, "ACTIVE", "No change requested", 409, "NO_STATUS_CHANGE"),
        ("stale-revision", 1, "SUSPENDED", "Stale request", 409, "STALE_REVISION"),
        ("blank-reason", 0, "SUSPENDED", "   ", 422, None),
    ],
)
def test_admin_user_status_rejections_leave_account_unchanged_and_audit_valid_attempts(
    db, case, revision_offset, target_status, reason, expected_status, expected_code
):
    admin = User(display_name=f"Status admin {uuid4()}")
    candidate = User(status="ACTIVE", display_name=f"Status candidate {uuid4()}")
    db.add_all([admin, candidate])
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()
    initial_revision = candidate.revision

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
        response = client.patch(
            f"/api/v1/admin/users/{candidate.id}/status",
            headers={"x-request-id": f"account-status-rejected-{case}"},
            json={
                "target_status": target_status,
                "expected_revision": initial_revision + revision_offset,
                "reason": reason,
            },
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()

    assert response.status_code == expected_status, response.text
    if expected_code is not None:
        assert response.json()["detail"]["code"] == expected_code

    db.expire_all()
    persisted = db.get(User, candidate.id)
    assert persisted is not None
    assert persisted.status == "ACTIVE"
    assert persisted.revision == initial_revision
    assert db.scalar(
        select(func.count()).select_from(AdminAuditEvent).where(
            AdminAuditEvent.action == "USER_STATUS_CHANGED",
            AdminAuditEvent.resource_id == candidate.id,
        )
    ) == 0

    rejected_audit = db.scalar(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "USER_STATUS_CHANGE_REJECTED",
        AdminAuditEvent.resource_id == candidate.id,
        AdminAuditEvent.request_id == f"account-status-rejected-{case}",
    ))
    if expected_code is None:
        assert rejected_audit is None
    else:
        assert rejected_audit is not None
        assert rejected_audit.outcome in {"CONFLICT", "REJECTED"}
        assert rejected_audit.details["validation_result"] == expected_code
        assert rejected_audit.details["reason"] == reason


def test_non_admin_cannot_change_another_users_account_status(db):
    ordinary_user = User(display_name=f"Non-admin {uuid4()}")
    candidate = User(status="ACTIVE", display_name=f"Target candidate {uuid4()}")
    db.add_all([ordinary_user, candidate])
    db.flush()
    initial_revision = candidate.revision

    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=ordinary_user.id, auth_provider="firebase"
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
        response = client.patch(
            f"/api/v1/admin/users/{candidate.id}/status",
            json={
                "target_status": "SUSPENDED",
                "expected_revision": initial_revision,
                "reason": "Unauthorized attempt",
            },
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()

    assert response.status_code == 403
    db.expire_all()
    persisted = db.get(User, candidate.id)
    assert persisted is not None
    assert persisted.status == "ACTIVE"
    assert persisted.revision == initial_revision
    assert db.scalar(
        select(func.count()).select_from(AdminAuditEvent).where(
            AdminAuditEvent.action == "USER_STATUS_CHANGED",
            AdminAuditEvent.resource_id == candidate.id,
        )
    ) == 0

def _admin_test_client(db, principal_user_id):
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=principal_user_id, auth_provider="firebase"
    )
    connection = db.connection()

    def override_db():
        request_session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def test_admin_user_directory_searches_paginates_and_omits_identity_provider_subjects(db):
    admin = User(display_name="Directory admin")
    first = User(
        display_name="Alpha Candidate",
        email="alpha@example.test",
        email_verified=True,
    )
    second = User(
        display_name="Beta Candidate",
        email="beta@example.test",
    )
    db.add_all([admin, first, second])
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.add_all([
        CandidateProfile(
            user_id=first.id,
            status="ACTIVE",
            profile_data={"headline": "Product manager"},
        ),
        CandidateProfile(
            user_id=second.id,
            status="DRAFT",
            profile_data={},
        ),
        Identity(
            user_id=first.id,
            provider="FIREBASE",
            provider_subject="private-provider-subject",
        ),
    ])
    db.flush()

    client = _admin_test_client(db, admin.id)
    try:
        response = client.get(
            "/api/v1/admin/users",
            params={"query": "alpha", "limit": 1, "offset": 0},
        )
        response_by_id = client.get(
            "/api/v1/admin/users",
            params={"query": str(first.id)},
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["pagination"] == {"limit": 1, "offset": 0, "total": 1}
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["id"] == str(first.id)
    assert item["candidate_profile"]["status"] == "ACTIVE"
    assert "profile_data" not in item["candidate_profile"]
    assert "identities" not in item
    assert "provider_subject" not in response.text
    assert "private-provider-subject" not in response.text
    assert response_by_id.status_code == 200, response_by_id.text
    assert response_by_id.json()["data"]["pagination"]["total"] == 1
    assert response_by_id.json()["data"]["items"][0]["id"] == str(first.id)


def test_admin_user_detail_returns_profile_and_audits_sensitive_read(db):
    admin = User(display_name="Detail admin")
    target = User(
        display_name="Candidate Detail",
        email="detail@example.test",
        email_verified=True,
    )
    db.add_all([admin, target])
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    profile = CandidateProfile(
        user_id=target.id,
        status="ACTIVE",
        profile_data={"headline": "Technical product manager"},
    )
    db.add(profile)
    db.flush()
    job = Job(
        canonical_title="Technical Product Manager",
        company_name="Admin Detail Test",
        lifecycle_status="ACTIVE",
        freshness_status="FRESH",
        application_url="https://example.test/jobs/admin-detail",
        location_country="Nigeria",
        work_mode="REMOTE",
        metadata_json={},
        location_restrictions={},
    )
    db.add(job)
    db.flush()
    application = Application(
        candidate_profile_id=profile.id,
        job_id=job.id,
        status="PLANNED",
    )
    db.add(application)
    db.flush()
    saved_job = SavedJob(candidate_profile_id=profile.id, job_id=job.id)
    db.add(saved_job)
    package = ApplicationPackage(
        application_id=application.id,
        version_number=1,
        status="DRAFT",
        source_snapshot={"status": "TEST_FIXTURE"},
        source_snapshot_hash="b" * 64,
        created_by_user_id=target.id,
    )
    db.add(package)
    db.flush()
    package_item = ApplicationPackageItem(
        package_id=package.id,
        artifact_type="COVER_LETTER",
        content={"body": "private artifact body"},
        evidence_refs=[],
        confidence="high",
        unsupported_claims=[],
        content_hash="c" * 64,
    )
    db.add(package_item)
    db.flush()
    db.add(AuditEvent(
        event_type="PROFILE_UPDATED",
        resource_type="candidate_profile",
        resource_id=profile.id,
        actor_type="CANDIDATE",
        actor_id=target.id,
        occurred_at=datetime(2026, 10, 17, 10, tzinfo=timezone.utc),
        request_id="candidate-profile-update",
    ))
    db.add(AuditEvent(
        event_type="ADMIN_REVIEWED_CANDIDATE",
        resource_type="candidate_profile",
        resource_id=profile.id,
        actor_type="ADMIN",
        actor_id=admin.id,
        occurred_at=datetime(2026, 10, 18, 10, tzinfo=timezone.utc),
        request_id="admin-candidate-review",
    ))
    db.add_all([
        CandidatePreferences(
            candidate_profile_id=profile.id,
            preferences={"work_mode": ["REMOTE"]},
        ),
        Document(
            candidate_profile_id=profile.id,
            application_package_id=package.id,
            application_package_item_id=package_item.id,
            document_type="CV",
            filename="private-cv.pdf",
            mime_type="application/pdf",
            storage_key="private/object/key",
            checksum_sha256="a" * 64,
            byte_size=128,
        ),
        SupportTicket(
            candidate_user_id=target.id,
            idempotency_key=f"detail-support-{uuid4()}",
            title="Private support title",
            issue_details="Private support body must not be returned.",
            status="OPEN",
        ),
        Notification(
            user_id=target.id,
            event_key=f"detail-notification-{uuid4()}",
            event_type="PUNT_QUALIFIED",
            title="New Punt",
            message="Private notification body must not be returned.",
            resource_type="punt",
            resource_id=uuid4(),
        ),
        NotificationEmailPreference(user_id=target.id, enabled=True),
    ])
    db.flush()

    client = _admin_test_client(db, admin.id)
    try:
        response = client.get(
            f"/api/v1/admin/users/{target.id}",
            headers={"x-request-id": "admin-user-detail-test"},
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["id"] == str(target.id)
    assert data["candidate_profile"]["id"] == str(profile.id)
    assert data["candidate_profile"]["profile_data"] == {
        "headline": "Technical product manager"
    }
    assert data["preferences"] == {"work_mode": ["REMOTE"]}
    assert data["activity_overview"]["recent_event_count"] == 2
    assert {event["event_type"] for event in data["activity_overview"]["recent_events"]} == {
        "PROFILE_UPDATED", "ADMIN_REVIEWED_CANDIDATE",
    }
    assert all("details" not in event for event in data["activity_overview"]["recent_events"])
    assert data["document_overview"]["total"] == 1
    assert data["document_overview"]["by_type"] == {"CV": 1}
    recent_document = data["document_overview"]["recent_documents"][0]
    assert recent_document["document_type"] == "CV"
    assert recent_document["mime_type"] == "application/pdf"
    assert recent_document["byte_size"] == 128
    assert recent_document["application_package_id"] == str(package.id)
    assert recent_document["application_package_item_id"] == str(package_item.id)
    assert "filename" not in recent_document
    assert "storage_key" not in recent_document
    assert data["application_overview"]["total"] == 1
    assert data["application_overview"]["by_status"] == {"PLANNED": 1}
    assert data["application_overview"]["recent_applications"][0]["application_id"] == str(application.id)
    assert data["application_overview"]["recent_applications"][0]["job_title"] == "Technical Product Manager"
    assert data["job_search_overview"]["saved_job_count"] == 1
    assert data["job_search_overview"]["recent_saved_jobs"][0]["saved_job_id"] == str(saved_job.id)
    assert data["job_search_overview"]["recent_saved_jobs"][0]["job_id"] == str(job.id)
    assert data["application_package_overview"]["total"] == 1
    assert data["application_package_overview"]["by_status"] == {"DRAFT": 1}
    assert data["application_package_overview"]["recent_packages"] == [{
        "id": str(package.id),
        "application_id": str(application.id),
        "version_number": 1,
        "status": "DRAFT",
        "artifact_count": 1,
        "created_at": data["application_package_overview"]["recent_packages"][0]["created_at"],
        "review_decided_at": None,
    }]
    assert "private artifact body" not in response.text
    assert data["support_overview"]["total"] == 1
    assert data["support_overview"]["by_status"] == {"OPEN": 1}
    assert data["support_overview"]["recent_tickets"][0]["status"] == "OPEN"
    assert data["notification_overview"] == {
        "total": 1,
        "unread": 1,
        "email_enabled": True,
    }
    assert "provider_subject" not in response.text
    assert "private/object/key" not in response.text
    assert "private-cv.pdf" not in response.text
    assert "Private support body" not in response.text
    assert "Private notification body" not in response.text

    audit = db.scalar(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "ADMIN_USER_DETAIL_VIEWED",
        AdminAuditEvent.resource_id == target.id,
    ))
    assert audit is not None
    assert audit.actor_user_id == admin.id
    assert audit.request_id == "admin-user-detail-test"
    assert audit.details == {
        "candidate_profile_present": True,
        "includes_preferences": True,
        "document_count": 1,
        "application_count": 0,
    }


def test_admin_user_directory_rejects_non_admin(db):
    ordinary_user = User(display_name="Not an admin")
    db.add(ordinary_user)
    db.flush()

    client = _admin_test_client(db, ordinary_user.id)
    try:
        response = client.get(
            "/api/v1/admin/users",
            headers={"x-request-id": "admin-denial-test"},
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 403
    denial = db.scalar(select(AdminAuditEvent).where(
        AdminAuditEvent.actor_user_id == ordinary_user.id,
        AdminAuditEvent.action == "ADMIN_ACCESS_DENIED",
        AdminAuditEvent.resource_type == "admin_route",
    ))
    assert denial is not None
    assert denial.outcome == "DENIED"
    assert denial.request_id == "admin-denial-test"
    assert denial.details == {
        "method": "GET",
        "path": "/api/v1/admin/users",
        "reason": "ADMIN_GRANT_REQUIRED",
    }

def test_admin_audit_filters_by_actor_action_resource_outcome_and_time_range(db):
    admin = User(display_name="Audit filter admin")
    other = User(display_name="Audit filter other")
    db.add_all([admin, other])
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    first = AdminAuditEvent(
        actor_user_id=admin.id,
        action="USER_STATUS_CHANGED",
        resource_type="user",
        outcome="SUCCESS",
        details={"reason": "support request"},
        request_id="audit-filter-1",
        created_at=datetime(2026, 10, 1, 12, tzinfo=timezone.utc),
    )
    second = AdminAuditEvent(
        actor_user_id=admin.id,
        action="CONFIG_PUBLISHED",
        resource_type="governance_config_version",
        outcome="SUCCESS",
        details={},
        request_id="audit-filter-2",
        created_at=datetime(2026, 10, 2, 12, tzinfo=timezone.utc),
    )
    third = AdminAuditEvent(
        actor_user_id=other.id,
        action="USER_STATUS_CHANGED",
        resource_type="user",
        outcome="DENIED",
        details={},
        request_id="audit-filter-3",
        created_at=datetime(2026, 10, 3, 12, tzinfo=timezone.utc),
    )
    db.add_all([first, second, third])
    db.flush()

    client = _admin_test_client(db, admin.id)
    try:
        response = client.get(
            "/api/v1/admin/audit",
            params={
                "actor_user_id": str(admin.id),
                "action": "USER_STATUS_CHANGED",
                "resource_type": "user",
                "outcome": "success",
                "created_after": "2026-10-01T00:00:00Z",
                "created_before": "2026-10-02T00:00:00Z",
            },
        )
        invalid_range = client.get(
            "/api/v1/admin/audit",
            params={
                "created_after": "2026-10-03T00:00:00Z",
                "created_before": "2026-10-01T00:00:00Z",
            },
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["id"] == str(first.id)
    assert rows[0]["request_id"] == "audit-filter-1"
    assert invalid_range.status_code == 422

def test_admin_user_directory_applies_verification_support_date_and_sort_filters(db):
    admin = User(display_name="Directory filter admin")
    first = User(
        display_name="Alpha Filter Candidate",
        email="alpha-filter@example.test",
        email_verified=True,
        created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        last_login_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
    )
    second = User(
        display_name="Beta Filter Candidate",
        email="beta-filter@example.test",
        email_verified=False,
        created_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
        last_login_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
    )
    db.add_all([admin, first, second])
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.add(SupportTicket(
        candidate_user_id=first.id,
        idempotency_key=f"directory-filter-{uuid4()}",
        title="Account help",
        issue_details="Candidate requested account assistance.",
        status="OPEN",
    ))
    db.add_all([
        AuditEvent(
            event_type="CANDIDATE_PROFILE_UPDATED",
            resource_type="candidate_profile",
            resource_id=first.id,
            actor_type="CANDIDATE",
            actor_id=first.id,
            occurred_at=datetime(2026, 10, 12, tzinfo=timezone.utc),
        ),
        AuditEvent(
            event_type="CANDIDATE_SEARCH_COMPLETED",
            resource_type="candidate",
            resource_id=second.id,
            actor_type="CANDIDATE",
            actor_id=second.id,
            occurred_at=datetime(2026, 10, 13, tzinfo=timezone.utc),
        ),
    ])
    db.flush()

    client = _admin_test_client(db, admin.id)
    try:
        response = client.get(
            "/api/v1/admin/users",
            params={
                "email_verified": "true",
                "support_status": "OPEN",
                "created_after": "2026-10-01T00:00:00Z",
                "created_before": "2026-10-01T23:59:59Z",
                "last_login_after": "2026-10-09T00:00:00Z",
                "last_login_before": "2026-10-10T23:59:59Z",
                "sort_by": "display_name",
                "sort_order": "asc",
            },
        )
        activity_response = client.get(
            "/api/v1/admin/users",
            params={
                "last_activity_after": "2026-10-11T00:00:00Z",
                "last_activity_before": "2026-10-14T00:00:00Z",
                "sort_by": "last_activity_at",
                "sort_order": "desc",
                "limit": 10,
            },
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["pagination"]["total"] == 1
    assert [item["id"] for item in data["items"]] == [str(first.id)]

    assert activity_response.status_code == 200, activity_response.text
    activity_items = activity_response.json()["data"]["items"]
    assert [item["id"] for item in activity_items[:2]] == [str(second.id), str(first.id)]
    assert activity_items[0]["last_activity_at"].startswith("2026-10-13")
    assert activity_items[1]["last_activity_at"].startswith("2026-10-12")

def test_admin_email_delivery_listing_reports_persisted_status_without_message_content(db):
    admin = User(display_name="Delivery admin")
    candidate = User(
        display_name="Delivery candidate",
        email="delivery-candidate@example.test",
    )
    db.add_all([admin, candidate])
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    notification = Notification(
        user_id=candidate.id,
        event_key=f"admin-email-delivery-{uuid4()}",
        event_type="PUNT_QUALIFIED",
        title="New Punt",
        message="Sensitive notification body must not be included.",
        resource_type="punt",
        resource_id=uuid4(),
    )
    db.add(notification)
    db.flush()
    delivery = NotificationEmailDelivery(
        notification_id=notification.id,
        status="FAILED",
        attempt_count=3,
        last_error="TimeoutError",
        updated_at=datetime(2026, 10, 18, 10, tzinfo=timezone.utc),
    )
    db.add(delivery)
    db.flush()

    client = _admin_test_client(db, admin.id)
    try:
        response = client.get(
            "/api/v1/admin/notifications/email-deliveries",
            params={"status": "FAILED", "user_id": str(candidate.id)},
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["pagination"]["total"] == 1
    item = payload["data"][0]
    assert item["notification_id"] == str(notification.id)
    assert item["user_id"] == str(candidate.id)
    assert item["status"] == "FAILED"
    assert item["attempt_count"] == 3
    assert item["last_error"] == "TimeoutError"
    assert "message" not in item
    assert "email" not in item
    assert "Sensitive notification body" not in response.text


def test_admin_email_delivery_listing_rejects_non_admin(db):
    ordinary_user = User(display_name="Delivery non-admin")
    db.add(ordinary_user)
    db.flush()

    client = _admin_test_client(db, ordinary_user.id)
    try:
        response = client.get("/api/v1/admin/notifications/email-deliveries")
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 403

def test_admin_integration_status_separates_configuration_from_observed_health(db, monkeypatch):
    admin = User(display_name="Integration status admin")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()
    settings = Settings(
        document_storage_provider="r2",
        r2_bucket="private-bucket",
        r2_endpoint_url="https://example.r2.cloudflarestorage.com",
        r2_access_key_id="private-access-key",
        r2_secret_access_key="private-storage-secret",
        smtp_host="smtp.example.test",
        smtp_from_email="noreply@example.test",
        smtp_username="private-smtp-user",
        smtp_password="private-smtp-password",
        document_ocr_enabled=True,
        google_drive_backup_enabled=True,
        google_drive_client_id="private-drive-client",
        google_drive_client_secret="private-drive-secret",
        google_drive_refresh_token="private-drive-refresh",
    )
    monkeypatch.setattr("app.config.settings.get_settings", lambda: settings)

    client = _admin_test_client(db, admin.id)
    try:
        response = client.get("/api/v1/admin/integrations/status")
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    integrations = {item["key"]: item for item in payload["integrations"]}
    assert integrations["document_storage"]["configuration_state"] == "CONFIGURED"
    assert integrations["document_storage"]["observed_status"] == "UNKNOWN"
    assert integrations["smtp_email"]["configured"] is True
    assert integrations["smtp_email"]["observed_status"] == "UNKNOWN"
    assert integrations["document_ocr"]["configuration_state"] == "ENABLED"
    assert integrations["document_ocr"]["configured"] is None
    assert integrations["google_drive_backup"]["enabled"] is True
    assert integrations["google_drive_backup"]["configuration_state"] == "CONFIGURED"
    assert integrations["firebase_identity"]["observed_status"] == "UNKNOWN"
    assert "private-access-key" not in response.text
    assert "private-storage-secret" not in response.text
    assert "private-smtp-password" not in response.text
    assert "private-drive-secret" not in response.text
    assert "private-drive-refresh" not in response.text
    assert "Persisted delivery outcomes are not a provider-wide health guarantee." in response.text

def test_admin_configuration_history_is_bounded_filtered_and_stably_ordered(db):
    admin = User(display_name="Config history admin")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.add_all([
        GovernanceConfigVersion(
            config_key="ai.task.cv_generation",
            version=version,
            status="DRAFT",
            payload={"temperature": version / 10},
            payload_fingerprint=("%064x" % version),
            created_by_user_id=admin.id,
        )
        for version in (1, 2, 3)
    ])
    db.add(GovernanceConfigVersion(
        config_key="ai.task.cover_letter",
        version=1,
        status="DRAFT",
        payload={"temperature": 0.2},
        payload_fingerprint="f" * 64,
        created_by_user_id=admin.id,
    ))
    db.flush()

    client = _admin_test_client(db, admin.id)
    try:
        response = client.get(
            "/api/v1/admin/configs",
            params={
                "config_key": "ai.task.cv_generation",
                "status": "DRAFT",
                "limit": 1,
                "offset": 1,
            },
        )
        invalid_limit = client.get("/api/v1/admin/configs", params={"limit": 0})
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["config_key"] == "ai.task.cv_generation"
    assert rows[0]["version"] == 2
    assert invalid_limit.status_code == 422

def test_admin_source_registry_filters_and_paginates_without_conflating_health(db):
    admin = User(display_name="Source registry admin")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    reviewed_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    first = Source(
        name="Alpha Board",
        source_type="JOB_BOARD",
        base_url="https://alpha.example/jobs",
        status="ACTIVE",
        permission_review_status="APPROVED",
        permission_review_reference="review-alpha-1",
        permission_reviewed_at=reviewed_at,
        operational_state="AVAILABLE",
        configuration={},
    )
    second = Source(
        name="Alpha Archive",
        source_type="JOB_BOARD",
        base_url="https://archive.alpha.example/jobs",
        status="ACTIVE",
        permission_review_status="APPROVED",
        permission_review_reference="review-alpha-2",
        permission_reviewed_at=reviewed_at,
        operational_state="AVAILABLE",
        configuration={},
    )
    excluded = Source(
        name="Beta Board",
        source_type="JOB_BOARD",
        base_url="https://beta.example/jobs",
        status="PAUSED",
        permission_review_status="PENDING",
        operational_state="UNAVAILABLE",
        configuration={},
    )
    db.add_all([first, second, excluded])
    db.flush()
    db.add_all([
        SourceConnector(
            source_id=source.id,
            connector_type="GREENHOUSE_JOB_BOARD",
            connector_version="1.0.0",
            status="ACTIVE",
            configuration={},
            capabilities={"published_jobs": True},
            limits={"timeout_seconds": 10},
        )
        for source in (first, second)
    ])
    db.flush()

    client = _admin_test_client(db, admin.id)
    try:
        response = client.get(
            "/api/v1/admin/sources",
            params={
                "query": "alpha",
                "status": "ACTIVE",
                "operational_state": "AVAILABLE",
                "permission_review_status": "APPROVED",
                "connector_type": "greenhouse_job_board",
                "limit": 1,
                "offset": 1,
            },
        )
        invalid_limit = client.get("/api/v1/admin/sources", params={"limit": 0})
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["pagination"] == {"limit": 1, "offset": 1, "total": 2}
    assert len(payload["data"]) == 1
    assert payload["data"][0]["id"] in {str(first.id), str(second.id)}
    assert payload["data"][0]["permission_review_status"] == "APPROVED"
    assert payload["data"][0]["operational_state"] == "AVAILABLE"
    assert invalid_limit.status_code == 422

def test_admin_source_creation_persists_normalized_start_urls(db):
    admin = User(display_name="Source URL admin")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()

    client = _admin_test_client(db, admin.id)
    try:
        response = client.post(
            "/api/v1/admin/sources",
            json={
                "name": "Normalized public source",
                "connector_type": "SCRAPY",
                "configuration": {
                    "start_urls": ["HTTPS://8.8.8.8:443/jobs?market=CA#section"],
                },
            },
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 201, response.text
    source_id = UUID(response.json()["data"]["id"])
    source = db.get(Source, source_id)
    assert source is not None
    assert source.base_url == "https://8.8.8.8/jobs?market=CA"
    connector = db.scalar(select(SourceConnector).where(SourceConnector.source_id == source_id))
    assert connector is not None
    version = db.scalar(select(SourceConnectorConfigurationVersion).where(
        SourceConnectorConfigurationVersion.source_connector_id == connector.id,
        SourceConnectorConfigurationVersion.version == 1,
    ))
    assert version is not None
    assert version.configuration["start_urls"] == ["https://8.8.8.8/jobs?market=CA"]

def test_admin_impersonation_session_is_audited_bounded_and_read_only(db):
    admin = User(display_name="Inspection admin", email="inspection-admin@example.test")
    target = User(
        display_name="Inspection target",
        email="inspection-target@example.test",
        email_verified=True,
        status="ACTIVE",
    )
    db.add_all([admin, target])
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()

    client = _admin_test_client(db, admin.id)
    try:
        started = client.post(
            "/api/v1/admin/impersonation-sessions",
            headers={"x-request-id": "impersonation-start-test"},
            json={"target_user_id": str(target.id), "reason": "Investigate reported profile issue."},
        )
        assert started.status_code == 201, started.text
        session_data = started.json()["data"]
        session_id = UUID(session_data["id"])
        assert session_data["status"] == "ACTIVE"
        assert session_data["read_only"] is True
        assert session_data["target_user_id"] == str(target.id)
        assert session_data["expires_at"] > session_data["started_at"]

        workspace_response = client.get(
            f"/api/v1/admin/impersonation-sessions/{session_id}/workspace",
            headers={"x-request-id": "impersonation-workspace-test"},
        )
        assert workspace_response.status_code == 200, workspace_response.text
        workspace = workspace_response.json()
        assert workspace["session"]["id"] == str(session_id)
        assert workspace["session"]["read_only"] is True
        assert workspace["workspace"]["id"] == target.id.__str__()
        assert workspace["workspace"]["email"] == "inspection-target@example.test"
        assert "document_bytes" not in workspace["workspace"]
        assert "storage_key" not in workspace["workspace"]

        ended = client.post(
            f"/api/v1/admin/impersonation-sessions/{session_id}/end",
            headers={"x-request-id": "impersonation-end-test"},
        )
        assert ended.status_code == 200, ended.text
        assert ended.json()["changed"] is True
        assert ended.json()["data"]["status"] == "ENDED"
        repeated_end = client.post(f"/api/v1/admin/impersonation-sessions/{session_id}/end")
        assert repeated_end.status_code == 200
        assert repeated_end.json()["changed"] is False
        unavailable = client.get(f"/api/v1/admin/impersonation-sessions/{session_id}/workspace")
        assert unavailable.status_code == 410
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    db.expire_all()
    session = db.get(AdminImpersonationSession, session_id)
    assert session is not None
    assert session.status == "ENDED"
    assert session.ended_at is not None
    events = db.scalars(select(AdminAuditEvent).where(
        AdminAuditEvent.resource_type == "admin_impersonation_session",
        AdminAuditEvent.resource_id == session_id,
    )).all()
    assert {event.action for event in events} >= {
        "IMPERSONATION_SESSION_STARTED",
        "IMPERSONATION_WORKSPACE_VIEWED",
        "IMPERSONATION_SESSION_ENDED",
    }
    assert all(event.actor_user_id == admin.id for event in events)


def test_admin_impersonation_rejects_self_and_expires_sessions_before_workspace_access(db):
    admin = User(display_name="Expiry inspection admin")
    target = User(display_name="Expiry inspection target")
    db.add_all([admin, target])
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()

    client = _admin_test_client(db, admin.id)
    try:
        self_session = client.post(
            "/api/v1/admin/impersonation-sessions",
            json={"target_user_id": str(admin.id), "reason": "Self inspection is not allowed."},
        )
        assert self_session.status_code == 422

        started = client.post(
            "/api/v1/admin/impersonation-sessions",
            json={"target_user_id": str(target.id), "reason": "Review account issue."},
        )
        assert started.status_code == 201, started.text
        session_id = UUID(started.json()["data"]["id"])
        persisted = db.get(AdminImpersonationSession, session_id)
        assert persisted is not None
        persisted.started_at = datetime(1999, 12, 31, tzinfo=timezone.utc)
        persisted.expires_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        db.flush()

        expired = client.get(f"/api/v1/admin/impersonation-sessions/{session_id}/workspace")
        assert expired.status_code == 410
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    db.expire_all()
    persisted = db.get(AdminImpersonationSession, session_id)
    assert persisted is not None
    assert persisted.status == "EXPIRED"
    assert persisted.ended_at is not None

def test_active_inspection_session_stops_working_when_admin_grant_is_revoked(db):
    admin = User(display_name="Revocation inspection admin")
    target = User(display_name="Revocation inspection target")
    db.add_all([admin, target])
    db.flush()
    grant = AdminGrant(user_id=admin.id, role="ADMIN", active=True)
    db.add(grant)
    db.flush()

    client = _admin_test_client(db, admin.id)
    try:
        started = client.post(
            "/api/v1/admin/impersonation-sessions",
            json={"target_user_id": str(target.id), "reason": "Investigate a user-reported issue."},
        )
        assert started.status_code == 201, started.text
        session_id = UUID(started.json()["data"]["id"])

        grant.active = False
        grant.revoked_at = datetime.now(timezone.utc)
        db.flush()
        denied = client.get(f"/api/v1/admin/impersonation-sessions/{session_id}/workspace")
        assert denied.status_code == 403
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    db.expire_all()
    persisted = db.get(AdminImpersonationSession, session_id)
    assert persisted is not None
    assert persisted.status == "ACTIVE"
    denial = db.scalar(select(AdminAuditEvent).where(
        AdminAuditEvent.actor_user_id == admin.id,
        AdminAuditEvent.action == "ADMIN_ACCESS_DENIED",
        AdminAuditEvent.resource_type == "admin_route",
    ).order_by(AdminAuditEvent.created_at.desc()))
    assert denial is not None
    assert denial.outcome == "DENIED"

def test_admin_forbidden_target_rule_lifecycle_is_normalized_audited_and_filterable(db):
    admin = User(display_name="Target policy admin")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()
    client = _admin_test_client(db, admin.id)

    try:
        created = client.post(
            "/api/v1/admin/target-policy/forbidden-rules",
            headers={"x-request-id": "target-rule-create"},
            json={
                "rule_type": "HOST_SUFFIX",
                "pattern": "*.EXAMPLE.COM.",
                "connector_scope": "SCRAPY",
                "reason": "Source is outside the approved acquisition scope.",
            },
        )
        assert created.status_code == 201, created.text
        rule_data = created.json()["data"]
        rule_id = UUID(rule_data["id"])
        assert rule_data["pattern"] == "example.com"
        assert rule_data["rule_type"] == "HOST_SUFFIX"
        assert rule_data["connector_scope"] == "SCRAPY"
        assert rule_data["active"] is True

        blocked_source = client.post(
            "/api/v1/admin/sources",
            json={
                "name": "Blocked by forbidden policy",
                "connector_type": "SCRAPY",
                "configuration": {"start_urls": ["https://jobs.example.com/list"]},
            },
        )
        assert blocked_source.status_code == 409, blocked_source.text
        assert blocked_source.json()["detail"]["code"] == "FORBIDDEN_TARGET_RULE_MATCHED"

        duplicate = client.post(
            "/api/v1/admin/target-policy/forbidden-rules",
            json={
                "rule_type": "HOST_SUFFIX",
                "pattern": "example.com",
                "connector_scope": "SCRAPY",
                "reason": "Duplicate normalized rule.",
            },
        )
        assert duplicate.status_code == 409

        listed = client.get(
            "/api/v1/admin/target-policy/forbidden-rules",
            params={"active": True, "connector_scope": "SCRAPY", "limit": 10},
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()["pagination"]["total"] == 1
        assert listed.json()["data"][0]["id"] == str(rule_id)

        deactivated = client.patch(
            f"/api/v1/admin/target-policy/forbidden-rules/{rule_id}/status",
            headers={"x-request-id": "target-rule-disable"},
            json={"active": False, "reason": "Permission review superseded this restriction."},
        )
        assert deactivated.status_code == 200, deactivated.text
        assert deactivated.json()["changed"] is True
        assert deactivated.json()["data"]["active"] is False

        allowed_source = client.post(
            "/api/v1/admin/sources",
            json={
                "name": "Source configured before later restriction",
                "connector_type": "SCRAPY",
                "configuration": {"start_urls": ["https://jobs.example.com/list"]},
            },
        )
        assert allowed_source.status_code == 201, allowed_source.text
        source_data = allowed_source.json()["data"]
        source_id = UUID(source_data["id"])
        connector_id = UUID(source_data["connectors"][0]["id"])
        draft_response = client.patch(
            f"/api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration",
            json={
                "expected_revision": 1,
                "configuration": {"start_urls": ["https://jobs.example.com/list?page=2"]},
                "reason": "Create a new page configuration draft.",
            },
        )
        assert draft_response.status_code == 200, draft_response.text
        draft_data = draft_response.json()["data"]
        draft_version = draft_data["draft_version"]

        reactivated = client.patch(
            f"/api/v1/admin/target-policy/forbidden-rules/{rule_id}/status",
            json={"active": True, "reason": "The source is no longer approved."},
        )
        assert reactivated.status_code == 200, reactivated.text

        blocked_publish = client.post(
            f"/api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration-versions/{draft_version}/publish",
            json={
                "expected_revision": draft_data["revision"],
                "reason": "Publish should recheck the live policy.",
            },
        )
        assert blocked_publish.status_code == 409, blocked_publish.text
        assert blocked_publish.json()["detail"]["code"] == "FORBIDDEN_TARGET_RULE_MATCHED"
        db.expire_all()
        connector = db.get(SourceConnector, connector_id)
        assert connector is not None
        assert connector.active_configuration_version == 1
        draft = db.scalar(select(SourceConnectorConfigurationVersion).where(
            SourceConnectorConfigurationVersion.source_connector_id == connector_id,
            SourceConnectorConfigurationVersion.version == draft_version,
        ))
        assert draft is not None
        assert draft.status == "DRAFT"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    db.expire_all()
    persisted = db.get(ForbiddenTargetRule, rule_id)
    assert persisted is not None
    assert persisted.active is True
    audit = db.scalars(select(AdminAuditEvent).where(
        AdminAuditEvent.resource_id == rule_id,
        AdminAuditEvent.resource_type == "forbidden_target_rule",
    )).all()
    assert {event.action for event in audit} == {
        "FORBIDDEN_TARGET_RULE_CREATED",
        "FORBIDDEN_TARGET_RULE_DEACTIVATED",
        "FORBIDDEN_TARGET_RULE_ACTIVATED",
    }
    assert all(event.actor_user_id == admin.id for event in audit)

def test_postgres_audit_histories_reject_update_delete_and_truncate(db):
    admin = User(display_name="Immutable audit admin")
    db.add(admin)
    db.flush()
    admin_event = AdminAuditEvent(
        actor_user_id=admin.id,
        action="IMMUTABILITY_TEST",
        resource_type="user",
        resource_id=admin.id,
        outcome="SUCCESS",
        details={"test": True},
        created_at=datetime.now(timezone.utc),
    )
    domain_event = AuditEvent(
        event_type="IMMUTABILITY_TEST",
        resource_type="user",
        resource_id=admin.id,
        actor_type="ADMIN",
        actor_id=admin.id,
        details={"test": True},
        occurred_at=datetime.now(timezone.utc),
    )
    db.add_all([admin_event, domain_event])
    db.commit()
    admin_event_id = admin_event.id
    domain_event_id = domain_event.id

    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(update(AdminAuditEvent).where(
            AdminAuditEvent.id == admin_event_id
        ).values(action="MUTATED"))
    db.rollback()

    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(delete(AdminAuditEvent).where(AdminAuditEvent.id == admin_event_id))
    db.rollback()

    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(update(AuditEvent).where(
            AuditEvent.id == domain_event_id
        ).values(event_type="MUTATED"))
    db.rollback()

    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("TRUNCATE TABLE admin_audit_events"))
    db.rollback()

    assert db.get(AdminAuditEvent, admin_event_id).action == "IMMUTABILITY_TEST"
    assert db.get(AuditEvent, domain_event_id).event_type == "IMMUTABILITY_TEST"

def test_admin_smtp_health_check_is_bounded_non_destructive_and_persisted(db, monkeypatch):
    from types import SimpleNamespace

    admin = User(display_name="SMTP health admin")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()
    settings = SimpleNamespace(
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_timeout_seconds=3,
        smtp_use_tls=True,
        smtp_username="smtp-user",
        smtp_password="smtp-secret",
        smtp_from_email="noreply@example.test",
        document_storage_provider="r2",
        r2_bucket=None,
        r2_endpoint_url=None,
        r2_access_key_id=None,
        r2_secret_access_key=None,
        google_cloud_storage_bucket=None,
        google_drive_backup_enabled=False,
        google_drive_client_id=None,
        google_drive_client_secret=None,
        google_drive_refresh_token=None,
        document_ocr_enabled=False,
    )
    monkeypatch.setattr("app.config.settings.get_settings", lambda: settings)
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append(("close",))
            return False

        def ehlo(self):
            calls.append(("ehlo",))
            return 250, b"ok"

        def starttls(self, context):
            calls.append(("starttls",))
            return 220, b"ready"

        def login(self, username, password):
            calls.append(("login", username, password))
            return 235, b"authenticated"

        def send_message(self, *_args, **_kwargs):
            raise AssertionError("health checks must never send email")

    monkeypatch.setattr("app.api.v1.admin.smtplib.SMTP", FakeSMTP)
    client = _admin_test_client(db, admin.id)
    try:
        response = client.post(
            "/api/v1/admin/integrations/smtp_email/health-check",
            headers={"x-request-id": "smtp-health-check"},
        )
        status_response = client.get("/api/v1/admin/integrations/status")
        history_response = client.get(
            "/api/v1/admin/integrations/observations",
            params={"integration_key": "smtp_email", "status": "HEALTHY"},
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["status"] == "HEALTHY"
    assert result["check_type"] == "CONNECTION_AND_AUTHENTICATION_NO_SEND"
    assert result["details"]["email_sent"] is False
    assert result["details"]["tls_negotiated"] is True
    assert ("connect", "smtp.example.test", 587, 3) in calls
    assert ("login", "smtp-user", "smtp-secret") in calls
    assert not any(call[0] == "send_message" for call in calls)
    assert "smtp-secret" not in response.text

    assert status_response.status_code == 200, status_response.text
    smtp_status = next(
        item for item in status_response.json()["data"]["integrations"]
        if item["key"] == "smtp_email"
    )
    assert smtp_status["observed_status"] == "HEALTHY"
    assert smtp_status["last_tested_at"] is not None
    assert history_response.status_code == 200, history_response.text
    assert history_response.json()["pagination"]["total"] == 1
    assert history_response.json()["data"][0]["id"] == result["id"]

    db.expire_all()
    persisted = db.get(IntegrationHealthObservation, UUID(result["id"]))
    assert persisted is not None
    assert persisted.checked_by_user_id == admin.id
    assert persisted.status == "HEALTHY"
    assert persisted.details["email_sent"] is False
    audit = db.scalar(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "INTEGRATION_HEALTH_CHECKED",
        AdminAuditEvent.resource_id == persisted.id,
    ))
    assert audit is not None
    assert audit.actor_user_id == admin.id
    assert audit.outcome == "HEALTHY"


def test_admin_smtp_health_check_records_safe_failure_without_provider_text(db, monkeypatch):
    import smtplib
    from types import SimpleNamespace

    admin = User(display_name="SMTP failure admin")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()
    monkeypatch.setattr(
        "app.config.settings.get_settings",
        lambda: SimpleNamespace(
            smtp_host="smtp.example.test",
            smtp_port=587,
            smtp_timeout_seconds=2,
            smtp_use_tls=True,
            smtp_username="smtp-user",
            smtp_password="smtp-secret",
            smtp_from_email="noreply@example.test",
        ),
    )

    class FailedSMTP:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            return 250, b"ok"

        def starttls(self, context):
            return 220, b"ready"

        def login(self, *_args):
            raise smtplib.SMTPAuthenticationError(535, b"private provider response")

    monkeypatch.setattr("app.api.v1.admin.smtplib.SMTP", FailedSMTP)
    client = _admin_test_client(db, admin.id)
    try:
        response = client.post("/api/v1/admin/integrations/smtp_email/health-check")
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["status"] == "FAILED"
    assert result["error_category"] == "SMTPAuthenticationError"
    assert "private provider response" not in response.text
    assert "smtp-secret" not in response.text

def test_admin_smtp_health_check_records_unconfigured_without_network(db, monkeypatch):
    from types import SimpleNamespace

    admin = User(display_name="Unconfigured SMTP admin")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()
    monkeypatch.setattr(
        "app.config.settings.get_settings",
        lambda: SimpleNamespace(
            smtp_host=None,
            smtp_port=587,
            smtp_timeout_seconds=2,
            smtp_use_tls=True,
            smtp_username=None,
            smtp_password=None,
            smtp_from_email=None,
        ),
    )
    monkeypatch.setattr(
        "app.api.v1.admin.smtplib.SMTP",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not connect")),
    )
    client = _admin_test_client(db, admin.id)
    try:
        response = client.post("/api/v1/admin/integrations/smtp_email/health-check")
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "UNCONFIGURED"
    assert response.json()["data"]["error_category"] == "SMTP_CONFIGURATION_INCOMPLETE"
    assert response.json()["data"]["details"]["email_sent"] is False

def test_admin_email_delivery_retry_requeues_existing_outbox_without_claiming_delivery(db, monkeypatch):
    from types import SimpleNamespace

    admin = User(display_name="Email retry admin")
    recipient = User(
        display_name="Email retry recipient",
        email="recipient@example.test",
        email_verified=True,
        status="ACTIVE",
    )
    db.add_all([admin, recipient])
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.add(NotificationEmailPreference(
        user_id=recipient.id, enabled=True, updated_at=datetime.now(timezone.utc)
    ))
    notification = Notification(
        user_id=recipient.id,
        event_key=f"email-retry-{uuid4()}",
        event_type="SUPPORT_TICKET_CREATED",
        title="Support ticket received",
        message="Your support ticket was received.",
        resource_type="support_ticket",
        resource_id=uuid4(),
        created_at=datetime.now(timezone.utc),
    )
    db.add(notification)
    db.flush()
    delivery = NotificationEmailDelivery(
        notification_id=notification.id,
        status="FAILED",
        attempt_count=2,
        last_error="SMTPAuthenticationError",
        updated_at=datetime.now(timezone.utc),
    )
    event = OutboxEvent(
        event_type="NOTIFICATION_EMAIL_REQUESTED",
        aggregate_type="Notification",
        aggregate_id=notification.id,
        payload={"notification_id": str(notification.id)},
        status="DEAD",
        attempt_count=5,
        last_error="notification_email_delivery_failed",
        created_at=datetime.now(timezone.utc),
        available_at=datetime.now(timezone.utc),
    )
    db.add_all([delivery, event])
    db.flush()
    delivery_id = delivery.id
    event_id = event.id
    notification_id = notification.id
    monkeypatch.setattr(
        "app.config.settings.get_settings",
        lambda: SimpleNamespace(smtp_host="smtp.example.test", smtp_from_email="noreply@example.test"),
    )

    client = _admin_test_client(db, admin.id)
    try:
        response = client.post(
            f"/api/v1/admin/notifications/email-deliveries/{delivery_id}/retry",
            headers={"x-request-id": "email-delivery-retry"},
            json={"reason": "SMTP credentials were corrected."},
        )
        duplicate = client.post(
            f"/api/v1/admin/notifications/email-deliveries/{delivery_id}/retry",
            json={"reason": "Duplicate retry request."},
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["delivery_id"] == str(delivery_id)
    assert result["notification_id"] == str(notification_id)
    assert result["delivery_status"] == "PENDING"
    assert result["outbox_status"] == "PENDING"
    assert result["delivery_confirmed"] is False
    assert duplicate.status_code == 409

    db.expire_all()
    persisted_delivery = db.get(NotificationEmailDelivery, delivery_id)
    persisted_event = db.get(OutboxEvent, event_id)
    assert persisted_delivery is not None
    assert persisted_delivery.status == "PENDING"
    assert persisted_delivery.attempt_count == 2
    assert persisted_delivery.last_error is None
    assert persisted_event is not None
    assert persisted_event.status == "PENDING"
    assert persisted_event.attempt_count == 0
    assert persisted_event.locked_at is None
    assert persisted_event.processed_at is None
    audit = db.scalar(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "NOTIFICATION_EMAIL_RETRY_REQUESTED",
        AdminAuditEvent.resource_id == delivery_id,
    ))
    assert audit is not None
    assert audit.actor_user_id == admin.id
    assert audit.details["reason"] == "SMTP credentials were corrected."
    rejected_audit = db.scalar(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "NOTIFICATION_EMAIL_RETRY_REJECTED",
        AdminAuditEvent.resource_id == delivery_id,
    ))
    assert rejected_audit is not None
    assert rejected_audit.outcome == "REJECTED"
    assert rejected_audit.details["validation_result"] == "DELIVERY_STATUS_NOT_RETRYABLE"

def test_admin_operations_reports_worker_heartbeats_and_derived_staleness(db):
    admin = User(display_name="Worker operations admin")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    now = datetime.now(timezone.utc)
    fresh = WorkerHeartbeat(
        worker_name="outbox",
        instance_id=uuid4(),
        status="IDLE",
        started_at=now - timedelta(minutes=5),
        last_seen_at=now,
        last_success_at=now,
        last_error_category=None,
        metrics={"claimed": 0, "processed": 3, "email": "must-not-persist"},
    )
    stale = WorkerHeartbeat(
        worker_name="discovery_scheduler",
        instance_id=uuid4(),
        status="RUNNING",
        started_at=now - timedelta(hours=1),
        last_seen_at=now - timedelta(minutes=10),
        last_success_at=now - timedelta(minutes=10),
        last_error_category="TimeoutError",
        metrics={"cycles": 10, "enqueued": 2},
    )
    db.add_all([fresh, stale])
    db.flush()

    client = _admin_test_client(db, admin.id)
    try:
        stale_response = client.get(
            "/api/v1/admin/operations/workers",
            params={"status": "STALE", "stale_after_seconds": 60},
        )
        active_response = client.get(
            "/api/v1/admin/operations/workers",
            params={"status": "IDLE", "stale_after_seconds": 60},
        )
        summary_response = client.get("/api/v1/admin/operations/summary")
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert stale_response.status_code == 200, stale_response.text
    stale_data = stale_response.json()["data"]
    assert len(stale_data) == 1
    assert stale_data[0]["worker_name"] == "discovery_scheduler"
    assert stale_data[0]["status"] == "STALE"
    assert stale_data[0]["last_error_category"] == "TimeoutError"
    assert "email" not in stale_data[0]["metrics"]

    assert active_response.status_code == 200, active_response.text
    assert [row["worker_name"] for row in active_response.json()["data"]] == ["outbox"]
    assert active_response.json()["data"][0]["status"] == "IDLE"

    assert summary_response.status_code == 200, summary_response.text
    assert summary_response.json()["data"]["workers"]["stale_count"] == 1


def test_suspended_admin_account_is_denied_and_access_attempt_is_audited(db):
    user = User(status="SUSPENDED", display_name=f"Suspended admin {uuid4()}")
    db.add(user)
    db.flush()
    db.add(AdminGrant(user_id=user.id, role="ADMIN", active=True))
    db.flush()

    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=user.id, auth_provider="firebase"
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
    request_id = f"suspended-admin-{uuid4()}"
    try:
        response = client.get(
            "/api/v1/admin/integrations/status",
            headers={"x-request-id": request_id},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Administrator permission required."

        db.expire_all()
        event = db.scalar(select(AdminAuditEvent).where(
            AdminAuditEvent.actor_user_id == user.id,
            AdminAuditEvent.action == "ADMIN_ACCESS_DENIED",
            AdminAuditEvent.request_id == request_id,
        ))
        assert event is not None
        assert event.outcome == "DENIED"
        assert event.details["reason"] == "ADMIN_GRANT_REQUIRED"
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()


def test_admin_smtp_config_requires_test_before_publish_and_never_returns_secret(db, monkeypatch):
    from app.api.v1 import admin as admin_api

    admin = User(display_name="SMTP config admin")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.commit()
    monkeypatch.setenv(
        "JOBPUNTER_SMTP_SECRET_ENCRYPTION_KEY",
        Fernet.generate_key().decode("ascii"),
    )

    class FakeSMTP:
        def __init__(self, host, port, timeout=10, **kwargs):
            assert host == "smtp.example.test"
            assert port == 587
            self.started_tls = False
            self.logged_in = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self, context=None):
            self.started_tls = True

        def login(self, username, password):
            assert username == "smtp-user"
            assert password == "smtp-private-password"
            self.logged_in = True

        def noop(self):
            return (250, b"ok")

    monkeypatch.setattr(admin_api.smtplib, "SMTP", FakeSMTP)
    client = _admin_test_client(db, admin.id)
    try:
        created = client.post("/api/v1/admin/smtp/configurations", json={
            "provider_name": "Example SMTP",
            "host": "smtp.example.test",
            "port": 587,
            "use_tls": True,
            "username": "smtp-user",
            "password": "smtp-private-password",
            "from_email": "noreply@example.test",
            "event_policy": {"PUNT_CREATED": True},
            "retry_limit": 4,
            "retry_backoff_seconds": 30,
        })
        assert created.status_code == 201, created.text
        draft = created.json()
        assert draft["status"] == "DRAFT"
        assert draft["has_password"] is True
        assert "smtp-private-password" not in created.text
        assert "encrypted_password" not in draft

        rejected_publish = client.post(
            f"/api/v1/admin/smtp/configurations/{draft['id']}/publish"
        )
        assert rejected_publish.status_code == 409
        denial_audit = db.scalar(select(AdminAuditEvent).where(
            AdminAuditEvent.action == "SMTP_CONFIG_PUBLISH_DENIED",
            AdminAuditEvent.resource_id == UUID(draft["id"]),
        ))
        assert denial_audit is not None
        assert denial_audit.outcome == "DENIED"
        assert denial_audit.details["reason"] == "CONNECTIVITY_TEST_REQUIRED"

        tested = client.post(f"/api/v1/admin/smtp/configurations/{draft['id']}/test")
        assert tested.status_code == 200, tested.text
        assert tested.json()["tested"] is True

        published = client.post(
            f"/api/v1/admin/smtp/configurations/{draft['id']}/publish"
        )
        assert published.status_code == 200, published.text
        assert published.json()["status"] == "PUBLISHED"
        stored = db.get(SMTPProviderConfiguration, UUID(draft["id"]))
        assert stored is not None
        assert stored.encrypted_password != "smtp-private-password"
        assert stored.tested_at is not None
        audit = db.scalar(select(AdminAuditEvent).where(
            AdminAuditEvent.action == "SMTP_CONFIG_PUBLISHED",
            AdminAuditEvent.resource_id == stored.id,
        ))
        assert audit is not None
        assert audit.resource_type == "smtp_provider_configuration"
        assert "smtp-private-password" not in str(audit.details)
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)


def test_candidate_saved_job_routes_are_owned_idempotent_and_removable(db):
    user = User(display_name="Saved-job candidate")
    db.add(user)
    db.flush()
    profile = CandidateProfile(
        user_id=user.id, status="ACTIVE", profile_data={"headline": "Candidate"}
    )
    job = Job(
        canonical_title="Product Manager",
        company_name="Saved Job Test",
        lifecycle_status="ACTIVE",
        freshness_status="FRESH",
        application_url="https://example.test/jobs/saved",
        location_country="Nigeria",
        work_mode="REMOTE",
        metadata_json={},
        location_restrictions={},
    )
    db.add_all([profile, job])
    db.commit()
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=user.id, auth_provider="test"
    )
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    try:
        first = client.put(f"/api/v1/candidate/saved-jobs/{job.id}")
        assert first.status_code == 200, first.text
        assert first.json()["created"] is True
        assert first.json()["data"]["job_id"] == str(job.id)

        repeated = client.put(f"/api/v1/candidate/saved-jobs/{job.id}")
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["created"] is False
        assert db.query(SavedJob).filter_by(candidate_profile_id=profile.id).count() == 1

        listed = client.get("/api/v1/candidate/saved-jobs")
        assert listed.status_code == 200, listed.text
        assert len(listed.json()["data"]["items"]) == 1

        removed = client.delete(f"/api/v1/candidate/saved-jobs/{job.id}")
        assert removed.status_code == 204
        assert db.query(SavedJob).filter_by(candidate_profile_id=profile.id).count() == 0
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
