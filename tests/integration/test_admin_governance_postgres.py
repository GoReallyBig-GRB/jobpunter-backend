import os
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
