import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_principal
from app.infrastructure.db.models import CandidateProfile, DiscoveryMission, Notification, OutboxEvent, WorkItem
from app.infrastructure.db.models.governance import AdminAuditEvent, AdminGrant
from app.infrastructure.db.models.identity import User
from app.application.notifications.service import discovery_completion_notification_handler
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


@pytest.fixture
def admin_client(db):
    admin = User(display_name=f"Work operations admin {uuid4()}")
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
        yield client, admin
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()


def seed_discovery_work_item(
    db,
    *,
    item_status: str,
    mission_status: str,
    next_attempt_at: datetime | None = None,
    error_code: str | None = None,
):
    now = datetime.now(timezone.utc)
    candidate = User(display_name=f"Work operations candidate {uuid4()}")
    db.add(candidate)
    db.flush()
    profile = CandidateProfile(user_id=candidate.id, status="ACTIVE", revision=1)
    db.add(profile)
    db.flush()

    mission = DiscoveryMission(
        candidate_profile_id=profile.id,
        idempotency_key=f"admin-mission-{uuid4()}",
        trigger_type="MANUAL",
        status=mission_status,
        policy_snapshot={"waves": [{"key": "official_feeds", "priority": 1}]},
        wave_index=0,
        cumulative_usable_opportunities=0,
        next_attempt_at=next_attempt_at,
        error_code=error_code,
        completed_at=now if mission_status == "FAILED" else None,
        created_at=now,
        updated_at=now,
    )
    db.add(mission)
    db.flush()

    item = WorkItem(
        work_type="DISCOVERY_MISSION",
        idempotency_key=f"discovery-mission:{mission.id}",
        resource_type="DISCOVERY_MISSION",
        resource_id=mission.id,
        execution_id=uuid4(),
        status=item_status,
        attempt_count=4,
        started_at=now if item_status == "PROCESSING" else None,
        completed_at=now if item_status == "FAILED" else None,
        error_code=error_code,
        error_details={"category": error_code} if error_code else {},
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    db.flush()
    return mission, item


def test_admin_can_expedite_only_a_future_retry_and_audits_it(db, admin_client):
    client, admin = admin_client
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    mission, item = seed_discovery_work_item(
        db, item_status="PENDING", mission_status="RETRY_WAIT", next_attempt_at=future,
    )

    response = client.post(
        f"/api/v1/admin/operations/work-items/{item.id}/retry",
        headers={"x-request-id": "work-item-retry-test"},
        json={"reason": "Expedite a verified recoverable discovery failure"},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["action"] == "RETRY_REQUESTED"
    assert data["status"] == "PENDING"
    assert data["mission_status"] == "RETRY_WAIT"
    assert datetime.fromisoformat(data["next_attempt_at"]) <= datetime.now(timezone.utc)

    db.expire_all()
    saved_mission = db.get(DiscoveryMission, mission.id)
    saved_item = db.get(WorkItem, item.id)
    audit = db.scalar(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "WORK_ITEM_RETRY_REQUESTED",
        AdminAuditEvent.resource_id == item.id,
    ))
    assert saved_mission.next_attempt_at <= datetime.now(timezone.utc)
    assert saved_item.status == "PENDING"
    assert saved_item.attempt_count == 4
    assert audit.actor_user_id == admin.id
    assert audit.request_id == "work-item-retry-test"
    assert audit.details["reason"] == "Expedite a verified recoverable discovery failure"

    repeated = client.post(
        f"/api/v1/admin/operations/work-items/{item.id}/retry",
        json={"reason": "Repeat retry must not create a second action"},
    )
    assert repeated.status_code == 409
    assert db.scalar(select(func.count(AdminAuditEvent.id)).where(
        AdminAuditEvent.action == "WORK_ITEM_RETRY_REQUESTED",
        AdminAuditEvent.resource_id == item.id,
    )) == 1


def test_admin_replay_reuses_failed_work_item_and_is_bounded_and_idempotent(db, admin_client):
    client, admin = admin_client
    mission, item = seed_discovery_work_item(
        db,
        item_status="FAILED",
        mission_status="FAILED",
        error_code="ALL_SOURCES_FAILED",
    )
    old_execution_id = item.execution_id

    response = client.post(
        f"/api/v1/admin/operations/work-items/{item.id}/replay",
        headers={"x-request-id": "work-item-replay-test"},
        json={"reason": "Source configuration was corrected; replay this mission"},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["action"] == "REPLAY_REQUESTED"
    assert data["status"] == "PENDING"
    assert data["mission_status"] == "PENDING"

    db.expire_all()
    saved_item = db.get(WorkItem, item.id)
    saved_mission = db.get(DiscoveryMission, mission.id)
    assert saved_item.id == item.id
    assert saved_item.status == "PENDING"
    assert saved_item.attempt_count == 4
    assert saved_item.execution_id != old_execution_id
    assert saved_item.error_code is None
    assert saved_mission.status == "PENDING"
    assert saved_mission.wave_index == 0
    assert saved_mission.current_wave_key is None
    assert saved_mission.completed_at is None
    assert saved_mission.next_attempt_at is not None

    audit = db.scalar(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "WORK_ITEM_REPLAY_REQUESTED",
        AdminAuditEvent.resource_id == item.id,
    ))
    assert audit.actor_user_id == admin.id
    assert audit.request_id == "work-item-replay-test"
    assert audit.details["replay_number"] == 1

    repeated = client.post(
        f"/api/v1/admin/operations/work-items/{item.id}/replay",
        json={"reason": "Repeated replay must not enqueue another work item"},
    )
    assert repeated.status_code == 409
    assert db.scalar(select(func.count(WorkItem.id)).where(
        WorkItem.work_type == "DISCOVERY_MISSION",
        WorkItem.idempotency_key == item.idempotency_key,
    )) == 1
    assert db.scalar(select(func.count(AdminAuditEvent.id)).where(
        AdminAuditEvent.action == "WORK_ITEM_REPLAY_REQUESTED",
        AdminAuditEvent.resource_id == item.id,
    )) == 1


def test_admin_replay_enforces_per_work_item_limit(db, admin_client):
    client, admin = admin_client
    mission, item = seed_discovery_work_item(
        db,
        item_status="FAILED",
        mission_status="FAILED",
        error_code="ALL_SOURCES_FAILED",
    )
    for replay_number in range(3):
        db.add(AdminAuditEvent(
            actor_user_id=admin.id,
            action="WORK_ITEM_REPLAY_REQUESTED",
            resource_type="work_item",
            resource_id=item.id,
            outcome="SUCCESS",
            details={"replay_number": replay_number + 1},
            created_at=datetime.now(timezone.utc),
        ))
    db.flush()

    response = client.post(
        f"/api/v1/admin/operations/work-items/{item.id}/replay",
        json={"reason": "Replay cap must be enforced"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "WORK_ITEM_REPLAY_LIMIT_REACHED"
    db.expire_all()
    assert db.get(WorkItem, item.id).status == "FAILED"
    assert db.get(DiscoveryMission, mission.id).status == "FAILED"


def test_admin_can_cancel_only_unclaimed_mission_and_emits_one_completion_signal(db, admin_client):
    client, admin = admin_client
    mission, item = seed_discovery_work_item(
        db,
        item_status="PENDING",
        mission_status="RETRY_WAIT",
        next_attempt_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    response = client.post(
        f"/api/v1/admin/operations/work-items/{item.id}/cancel",
        headers={"x-request-id": "work-item-cancel-test"},
        json={"reason": "Candidate requested that this pending mission stop"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "CANCELLED"
    assert response.json()["data"]["mission_status"] == "CANCELLED"

    db.expire_all()
    saved_item = db.get(WorkItem, item.id)
    saved_mission = db.get(DiscoveryMission, mission.id)
    completion_events = db.scalars(select(OutboxEvent).where(
        OutboxEvent.event_type == "DISCOVERY_MISSION_COMPLETED",
        OutboxEvent.aggregate_id == mission.id,
    )).all()
    audit = db.scalar(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "WORK_ITEM_CANCELLED",
        AdminAuditEvent.resource_id == item.id,
    ))
    assert saved_item.status == "CANCELLED"
    assert saved_item.error_code == "CANCELLED_BY_ADMIN"
    assert saved_mission.status == "CANCELLED"
    assert saved_mission.error_code == "CANCELLED_BY_ADMIN"
    assert len(completion_events) == 1
    assert completion_events[0].status == "PENDING"

    notification_factory = sessionmaker(
        bind=db.connection(),
        class_=Session,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    discovery_completion_notification_handler(notification_factory)({"mission_id": str(mission.id)})
    db.expire_all()
    notification = db.scalar(select(Notification).where(
        Notification.event_key == f"discovery-completed:{mission.id}",
    ))
    assert notification is not None
    assert notification.message == "Job discovery was cancelled by an administrator."

    assert audit.actor_user_id == admin.id
    assert audit.request_id == "work-item-cancel-test"
    assert audit.details["reason"] == "Candidate requested that this pending mission stop"

    repeated = client.post(
        f"/api/v1/admin/operations/work-items/{item.id}/cancel",
        json={"reason": "Duplicate cancellation must not create a second signal"},
    )
    assert repeated.status_code == 409
    assert db.scalar(select(func.count(OutboxEvent.id)).where(
        OutboxEvent.event_type == "DISCOVERY_MISSION_COMPLETED",
        OutboxEvent.aggregate_id == mission.id,
    )) == 1


def test_admin_cannot_cancel_a_claimed_work_item_and_non_admin_is_denied(db, admin_client):
    client, _admin = admin_client
    mission, item = seed_discovery_work_item(
        db, item_status="PROCESSING", mission_status="RUNNING",
    )
    response = client.post(
        f"/api/v1/admin/operations/work-items/{item.id}/cancel",
        json={"reason": "Attempted cancellation during active execution"},
    )
    assert response.status_code == 409

    db.expire_all()
    assert db.get(WorkItem, item.id).status == "PROCESSING"
    assert db.get(DiscoveryMission, mission.id).status == "RUNNING"
    assert db.scalar(select(func.count(AdminAuditEvent.id)).where(
        AdminAuditEvent.action == "WORK_ITEM_CANCELLED",
        AdminAuditEvent.resource_id == item.id,
    )) == 0

    non_admin = User(display_name=f"Unprivileged work operations user {uuid4()}")
    db.add(non_admin)
    db.flush()
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=non_admin.id, auth_provider="firebase"
    )
    denied = client.post(
        f"/api/v1/admin/operations/work-items/{item.id}/cancel",
        json={"reason": "Unauthorized cancellation attempt"},
    )
    assert denied.status_code == 403
