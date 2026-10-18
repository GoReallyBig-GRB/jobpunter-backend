import os
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi import Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.exc import DBAPIError

from app.api.deps import get_principal
from app.infrastructure.db.models.governance import AIExecution, AdminAuditEvent, AdminGrant, GovernanceConfigVersion
from app.infrastructure.db.models.opportunities import CandidateEvaluation
from app.infrastructure.db.models.identity import Identity, User
from app.infrastructure.db.models.candidate import CandidateProfile
from app.infrastructure.db.models.support import SupportTicket
from app.infrastructure.db.models.notifications import Notification, NotificationEmailDelivery
from app.infrastructure.identity.firebase import FirebaseIdentityProvider
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
        audit_events = db.scalars(
            select(AdminAuditEvent).order_by(AdminAuditEvent.created_at, AdminAuditEvent.id)
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
                "config_key": "decision.rules.candidate_evaluation",
                "payload": {"apply_min_score": 35, "review_min_score": 35},
            },
        )
        assert invalid_thresholds.status_code == 422

        attempted_hard_rule_override = client.post(
            "/api/v1/admin/configs",
            json={
                "config_key": "decision.rules.candidate_evaluation",
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
                "config_key": "decision.rules.candidate_evaluation",
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
        stored = db.scalar(select(GovernanceConfigVersion).where(
            GovernanceConfigVersion.config_key == "decision.rules.candidate_evaluation",
            GovernanceConfigVersion.status == "PUBLISHED",
        ))
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
                "config_key": "decision.rules.candidate_evaluation",
                "payload": {"apply_min_score": 85, "review_min_score": 30},
            },
        )
        assert second_draft_response.status_code == 201, second_draft_response.text
        second_draft = second_draft_response.json()
        assert second_draft["version"] == 2
        second_publish = client.post(f"/api/v1/admin/configs/{second_draft['id']}/publish")
        assert second_publish.status_code == 200, second_publish.text

        rollback_response = client.post(f"/api/v1/admin/configs/{draft['id']}/rollback")
        assert rollback_response.status_code == 201, rollback_response.text
        rollback = rollback_response.json()
        assert rollback["version"] == 3
        assert rollback["status"] == "PUBLISHED"
        assert rollback["supersedes_id"] == draft["id"]
        assert rollback["payload"] == {"apply_min_score": 80, "review_min_score": 35}
        db.expire_all()
        versions = db.scalars(
            select(GovernanceConfigVersion)
            .where(GovernanceConfigVersion.config_key == "decision.rules.candidate_evaluation")
            .order_by(GovernanceConfigVersion.version)
        ).all()
        assert [(row.version, row.status) for row in versions] == [
            (1, "RETIRED"),
            (2, "RETIRED"),
            (3, "PUBLISHED"),
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
            existing = setup.scalar(select(func.count(GovernanceConfigVersion.id)).where(
                GovernanceConfigVersion.config_key == config_key
            ))
            assert existing == 0, "test requires a clean decision-rule config namespace"
            admin = User(display_name=f"Concurrent rules admin {uuid4()}")
            setup.add(admin)
            setup.flush()
            user_id = admin.id
            setup.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
            first = GovernanceConfigVersion(
                config_key=config_key,
                version=1,
                status="PUBLISHED",
                payload={"apply_min_score": 80, "review_min_score": 35},
                payload_fingerprint="a" * 64,
                created_by_user_id=admin.id,
                created_at=now,
                published_by_user_id=admin.id,
                published_at=now,
            )
            second = GovernanceConfigVersion(
                config_key=config_key,
                version=2,
                status="DRAFT",
                payload={"apply_min_score": 85, "review_min_score": 35},
                payload_fingerprint="b" * 64,
                created_by_user_id=admin.id,
                created_at=now,
            )
            third = GovernanceConfigVersion(
                config_key=config_key,
                version=3,
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
            assert len(versions) == 3
            assert sum(row.status == "PUBLISHED" for row in versions) == 1
            assert versions[0].status == "RETIRED"
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
            user_ids = [
                item.id for item in (admin_a, admin_b) if item is not None
            ]
            if grant_ids:
                cleanup.execute(delete(AdminAuditEvent).where(
                    AdminAuditEvent.resource_type == "admin_grant",
                    AdminAuditEvent.resource_id.in_(grant_ids),
                ))
                cleanup.execute(delete(AdminGrant).where(AdminGrant.id.in_(grant_ids)))
            if user_ids:
                cleanup.execute(delete(User).where(User.id.in_(user_ids)))
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
def test_admin_user_status_rejections_leave_account_and_audit_unchanged(
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
    assert "provider_subject" not in response.text

    audit = db.scalar(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "ADMIN_USER_DETAIL_VIEWED",
        AdminAuditEvent.resource_id == target.id,
    ))
    assert audit is not None
    assert audit.actor_user_id == admin.id
    assert audit.request_id == "admin-user-detail-test"
    assert audit.details == {"candidate_profile_present": True}


def test_admin_user_directory_rejects_non_admin(db):
    ordinary_user = User(display_name="Not an admin")
    db.add(ordinary_user)
    db.flush()

    client = _admin_test_client(db, ordinary_user.id)
    try:
        response = client.get("/api/v1/admin/users")
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 403

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
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["pagination"]["total"] == 1
    assert [item["id"] for item in data["items"]] == [str(first.id)]

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

