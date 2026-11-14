"""PostgreSQL acceptance for global discovery schedules and durable run enqueueing."""
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.application.jobs.discovery_orchestration import strategy_configuration_hash
from app.application.jobs.discovery_run_execution import (
    ConnectorAttemptResult,
    execute_discovery_run,
)
from app.application.jobs.discovery_schedules import schedule_due_discovery_runs
from app.infrastructure.db.models import (
    AcquisitionRun,
    AdminAuditEvent,
    AdminGrant,
    DiscoveryRoleExecution,
    DiscoveryRun,
    DiscoverySchedule,
    DiscoveryStrategy,
    DiscoveryStrategyVersion,
    DiscoveryTarget,
    OutboxEvent,
    RawSourceRecord,
    Source,
    SourceConnector,
    User,
    WorkItem,
)
from app.infrastructure.db.session import get_db
from app.main import app
from app.ports.identity import AuthenticatedPrincipal
from app.workers.discovery_run_worker import DiscoveryRunWorker

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
    admin = User(display_name=f"Schedule admin {uuid4()}")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    payload = {
        "schema_version": "v1",
        "roles": [{"role_key": "STRUCTURED_ACQUISITION", "order": 1,
                   "tool_keys": ["GREENHOUSE_JOB_BOARD"],
                   "failure_behavior": "FALLBACK",
                   "fallbacks": [{"tool_key": "JSON_API",
                                  "on_outcomes": ["UNAVAILABLE"]}],
                   "limits": {"max_requests": 10, "max_records": 100}},
                  {"role_key": "RECORD_VALIDATION", "order": 2,
                   "tool_keys": ["INTERNAL_RECORD_VALIDATOR"],
                   "limits": {"max_records": 100, "max_duration_seconds": 60}}],
        "global_limits": {"max_requests": 10, "max_records": 100,
                          "max_duration_seconds": 300},
    }
    strategy = DiscoveryStrategy(
        strategy_key=f"schedule.{uuid4().hex}", name="Schedule strategy", purpose="Test",
        status="ENABLED", created_by_user_id=admin.id,
    )
    db.add(strategy)
    db.flush()
    version = DiscoveryStrategyVersion(
        strategy_id=strategy.id, version=1, payload=payload,
        configuration_hash=strategy_configuration_hash(payload), schema_version="v1",
        status="PUBLISHED", validation_status="VALID", validation_result={"valid": True},
        created_by_user_id=admin.id, published_by_user_id=admin.id,
        published_at=datetime.now(UTC),
    )
    db.add(version)
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
        yield client, admin, version
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()


def _schedule_payload(version_id, **changes):
    value = {
        "name": "Independent global sweep", "schedule_type": "GLOBAL_SWEEP",
        "timezone": "UTC", "frequency_type": "INTERVAL",
        "frequency_definition": {"interval_minutes": 30},
        "strategy_version_id": str(version_id), "priority": 10,
        "overlap_policy": "SKIP", "maximum_concurrent_runs": 1,
        "limits": {"max_requests": 50, "max_records": 500,
                   "max_duration_seconds": 900},
        "retry_policy": {"max_attempts": 2, "initial_backoff_seconds": 5,
                         "maximum_backoff_seconds": 60},
        "reason": "Create bounded global discovery schedule", "activate": True,
    }
    value.update(changes)
    return value


def _create_published_schedule(client, payload):
    created = client.post("/api/v1/admin/discovery/schedules", json={**payload, "activate": False})
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    schedule_id, config_id = data["id"], data["draft_config_version_id"]
    published = client.post(f"/api/v1/admin/discovery/schedules/{schedule_id}/versions/{config_id}/publish")
    assert published.status_code == 200, published.text
    if payload.get("activate"):
        row = next(row for row in client.get("/api/v1/admin/discovery/schedules").json()["data"] if row["id"] == schedule_id)
        response = client.patch(f"/api/v1/admin/discovery/schedules/{schedule_id}/status", json={
            "status": "ACTIVE", "expected_revision": row["revision"], "reason": "Enable published schedule",
        })
        assert response.status_code == 200, response.text
    return created


def _approved_source_fields():
    return {
        "permission_review_status": "APPROVED",
        "permission_review_reference": "test-policy-review",
        "permission_reviewed_at": datetime.now(UTC),
        "configuration": {"permission_review": {
            "access_method": "public_api", "terms_reference": "test-terms",
            "rate_limit_policy": "bounded", "rate_limit_reference": "test-limit",
        }},
    }


def test_admin_schedule_and_manual_run_are_audited_snapshot_and_replay_safe(db, admin_client):
    client, admin, version = admin_client
    created = _create_published_schedule(client, _schedule_payload(version.id))
    assert created.status_code == 201, created.text
    schedule_id = created.json()["data"]["id"]
    first = client.post(f"/api/v1/admin/discovery/schedules/{schedule_id}/runs", json={
        "idempotency_key": "operator-ticket-123", "reason": "Verify manual execution path",
    })
    assert first.status_code == 201, first.text
    replay = client.post(f"/api/v1/admin/discovery/schedules/{schedule_id}/runs", json={
        "idempotency_key": "operator-ticket-123", "reason": "Verify manual execution path",
    })
    assert replay.status_code == 201, replay.text
    assert replay.json()["data"]["id"] == first.json()["data"]["id"]
    run = db.get(DiscoveryRun, first.json()["data"]["id"])
    assert run.configuration_snapshot["strategy"] == version.payload
    assert run.configuration_snapshot["schedule_revision"] == db.get(DiscoverySchedule, UUID(schedule_id)).revision
    assert run.configuration_snapshot["schedule_config_version_id"] is not None
    assert db.scalar(select(func.count(WorkItem.id)).where(
        WorkItem.work_type == "DISCOVERY_RUN", WorkItem.resource_id == run.id,
    )) == 1
    assert db.scalar(select(func.count(AdminAuditEvent.id)).where(
        AdminAuditEvent.action == "DISCOVERY_RUN_REQUESTED",
        AdminAuditEvent.resource_id == run.id,
    )) == 2
    assert run.requested_by_user_id == admin.id
    with pytest.raises(DBAPIError), db.begin_nested():
        db.execute(update(DiscoveryRun).where(DiscoveryRun.id == run.id).values(
            configuration_snapshot={"tampered": True}
        ))


def test_due_scheduler_records_overlap_block_and_advances_slot(db, admin_client):
    client, _, version = admin_client
    created = _create_published_schedule(client, _schedule_payload(version.id))
    assert created.status_code == 201, created.text
    schedule = db.get(DiscoverySchedule, created.json()["data"]["id"])
    now = datetime.now(UTC)
    schedule.next_run_at = now - timedelta(minutes=1)
    active = DiscoveryRun(
        run_key=f"existing:{uuid4()}", run_type=schedule.schedule_type,
        trigger_type="MANUAL", schedule_id=schedule.id, strategy_version_id=version.id,
        configuration_snapshot={}, configuration_hash="0" * 64, scope={}, status="RUNNING",
        operation_counts={}, record_counts={}, budget_outcome={}, retry_information={},
        error_details={}, correlation_id=str(uuid4()), created_at=now,
    )
    db.add(active)
    db.commit()
    run_ids = schedule_due_discovery_runs(db, now=now)
    assert len(run_ids) == 1
    blocked = db.get(DiscoveryRun, run_ids[0])
    assert blocked.status == "BLOCKED"
    assert blocked.error_details["code"] == "OVERLAP_LIMIT"
    assert db.scalar(select(WorkItem).where(
        WorkItem.work_type == "DISCOVERY_RUN", WorkItem.resource_id == blocked.id,
    )) is None
    db.refresh(schedule)
    assert schedule.next_run_at > now


def test_run_executor_persists_role_evidence_and_fallback_decision(db, admin_client):
    client, _, version = admin_client
    source = Source(
        name=f"Executor source {uuid4()}", source_type="TEST", status="ACTIVE",
        operational_state="AVAILABLE", **_approved_source_fields(),
    )
    db.add(source)
    db.flush()
    primary_connector = SourceConnector(
        source_id=source.id, connector_type="GREENHOUSE_JOB_BOARD", connector_version="1",
        status="ACTIVE", configuration={}, capabilities={}, limits={},
    )
    fallback_connector = SourceConnector(
        source_id=source.id, connector_type="JSON_API", connector_version="1",
        status="ACTIVE", configuration={}, capabilities={}, limits={},
    )
    db.add_all([primary_connector, fallback_connector])
    db.flush()
    acquisition = AcquisitionRun(
        source_id=source.id, source_connector_id=fallback_connector.id,
        run_key=f"validation-fixture-{uuid4()}", status="SUCCEEDED",
        metrics={"acquired_count": 1, "persisted_count": 1},
        started_at=datetime.now(UTC), completed_at=datetime.now(UTC),
    )
    db.add(acquisition)
    db.flush()
    raw = RawSourceRecord(
        acquisition_run_id=acquisition.id, source_id=source.id,
        external_record_id="validation-job-1", deduplication_key="validation-job-1",
        source_url="https://example.test/jobs/1",
        raw_content={"title": "Backend Engineer", "company": "Example"},
        content_hash="a" * 64, status="ACQUIRED",
        acquired_at=datetime.now(UTC), created_at=datetime.now(UTC),
    )
    db.add(raw)
    db.flush()
    db.add(OutboxEvent(
        event_type="RAW_SOURCE_RECORD_ACQUIRED", aggregate_type="RawSourceRecord",
        aggregate_id=raw.id, payload={"raw_source_record_id": str(raw.id)},
        status="PENDING", attempt_count=0, available_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    ))
    db.commit()
    created = _create_published_schedule(client, _schedule_payload(
        version.id, source_scope=[str(source.id)], activate=False,
    ))
    schedule_id = created.json()["data"]["id"]
    requested = client.post(f"/api/v1/admin/discovery/schedules/{schedule_id}/runs", json={
        "idempotency_key": "fallback-evidence", "reason": "Exercise durable fallback evidence",
    })
    run_id = requested.json()["data"]["id"]

    def runner(_db, _run, _role, connector, _target):
        if connector.connector_type == "GREENHOUSE_JOB_BOARD":
            return ConnectorAttemptResult(
                tool_key=connector.connector_type, source_id=connector.source_id,
                status="UNAVAILABLE", error_category="TEST_OUTAGE",
            )
        return ConnectorAttemptResult(
            tool_key=connector.connector_type, source_id=connector.source_id,
            status="SUCCEEDED", acquired_count=1, persisted_count=1,
            acquisition_run_id=acquisition.id,
        )

    assert execute_discovery_run(
        db, UUID(run_id), attempt_number=1, connector_runner=runner
    ) == "SUCCEEDED"
    executions = db.scalars(select(DiscoveryRoleExecution).where(
        DiscoveryRoleExecution.run_id == run_id
    ).order_by(DiscoveryRoleExecution.role_order)).all()
    execution = executions[0]
    assert execution.outcome == "SUCCEEDED"
    assert execution.record_counts == {"acquired": 1, "persisted": 1}
    assert execution.fallback_decision["events"][0]["to"] == "JSON_API"
    assert execution.fallback_decision["attempts"][1]["acquisition_run_id"] == str(
        acquisition.id
    )
    run = db.get(DiscoveryRun, run_id)
    assert run.operation_counts == {"connector_attempts": 2}
    assert run.record_counts == {"acquired": 1, "persisted": 1}
    assert executions[1].outcome == "SUCCEEDED"
    assert executions[1].selected_tool_key == "INTERNAL_RECORD_VALIDATOR"
    assert executions[1].record_counts == {
        "examined": 1, "valid": 1, "invalid": 0,
        "canonicalization_handoff_missing": 0,
    }


def test_record_validation_rejects_unresolved_titleless_external_id(db, admin_client):
    client, _, version = admin_client
    source = Source(
        name=f"Titleless source {uuid4()}", source_type="TEST", status="ACTIVE",
        operational_state="AVAILABLE", **_approved_source_fields(),
    )
    db.add(source)
    db.flush()
    primary = SourceConnector(
        source_id=source.id, connector_type="GREENHOUSE_JOB_BOARD", connector_version="1",
        status="ACTIVE", configuration={}, capabilities={}, limits={},
    )
    fallback = SourceConnector(
        source_id=source.id, connector_type="JSON_API", connector_version="1",
        status="ACTIVE", configuration={}, capabilities={}, limits={},
    )
    db.add_all([primary, fallback])
    db.flush()
    acquisition = AcquisitionRun(
        source_id=source.id, source_connector_id=fallback.id,
        run_key=f"titleless-validation-{uuid4()}", status="SUCCEEDED",
        metrics={"acquired_count": 1, "persisted_count": 1},
        started_at=datetime.now(UTC), completed_at=datetime.now(UTC),
    )
    db.add(acquisition)
    db.flush()
    raw = RawSourceRecord(
        acquisition_run_id=acquisition.id, source_id=source.id,
        external_record_id="new-titleless-id", deduplication_key="new-titleless-id",
        source_url="https://example.test/jobs/titleless",
        raw_content={"company": "Example"}, content_hash="b" * 64,
        status="ACQUIRED", acquired_at=datetime.now(UTC), created_at=datetime.now(UTC),
    )
    db.add(raw)
    db.flush()
    db.add(OutboxEvent(
        event_type="RAW_SOURCE_RECORD_ACQUIRED", aggregate_type="RawSourceRecord",
        aggregate_id=raw.id, payload={"raw_source_record_id": str(raw.id)},
        status="PENDING", attempt_count=0, available_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    ))
    db.commit()
    created = _create_published_schedule(client, _schedule_payload(
        version.id, source_scope=[str(source.id)], activate=False,
    ))
    requested = client.post(
        f"/api/v1/admin/discovery/schedules/{created.json()['data']['id']}/runs",
        json={"idempotency_key": "titleless-record", "reason": "Reject false validation"},
    )
    run_id = UUID(requested.json()["data"]["id"])

    def runner(_db, _run, _role, connector, _target):
        if connector.connector_type == "GREENHOUSE_JOB_BOARD":
            return ConnectorAttemptResult(
                tool_key=connector.connector_type, source_id=source.id,
                status="UNAVAILABLE", error_category="TEST_OUTAGE",
            )
        return ConnectorAttemptResult(
            tool_key=connector.connector_type, source_id=source.id,
            status="SUCCEEDED", acquired_count=1, persisted_count=1,
            acquisition_run_id=acquisition.id,
        )

    assert execute_discovery_run(
        db, run_id, attempt_number=1, connector_runner=runner
    ) == "PARTIAL"
    validation = db.scalar(select(DiscoveryRoleExecution).where(
        DiscoveryRoleExecution.run_id == run_id,
        DiscoveryRoleExecution.role_key == "RECORD_VALIDATION",
    ))
    assert validation.outcome == "FAILED"
    assert validation.reason_code == "NO_VALID_ACQUIRED_RECORDS"
    assert validation.record_counts == {
        "examined": 1, "valid": 0, "invalid": 1,
        "canonicalization_handoff_missing": 0,
    }


def test_scoped_run_blocks_inactive_target_before_connector_execution(db, admin_client):
    client, admin, version = admin_client
    source = Source(
        name=f"Blocked target source {uuid4()}", source_type="TEST", status="ACTIVE",
        operational_state="AVAILABLE", **_approved_source_fields(),
    )
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id, connector_type="JSON_API", connector_version="1",
        status="ACTIVE", configuration={}, capabilities={}, limits={},
    )
    db.add(connector)
    db.flush()
    target = DiscoveryTarget(
        source_id=source.id, source_connector_id=connector.id,
        url="https://example.test/jobs", target_kind="CRAWL", purpose="DISCOVERY",
        status="PAUSED", validation_status="VALIDATED", validated_at=datetime.now(UTC),
        created_by_user_id=admin.id,
    )
    db.add(target)
    db.commit()
    created = _create_published_schedule(client, _schedule_payload(
        version.id, schedule_type="TARGET_REFRESH", source_scope=[str(source.id)],
        target_scope=[str(target.id)], activate=False,
    ))
    requested = client.post(
        f"/api/v1/admin/discovery/schedules/{created.json()['data']['id']}/runs",
        json={"idempotency_key": "blocked-target", "reason": "Prove pre-network target block"},
    )

    def must_not_run(*_args):
        raise AssertionError("connector must not execute for an inactive target")

    run_id = UUID(requested.json()["data"]["id"])
    assert execute_discovery_run(
        db, run_id, attempt_number=1, connector_runner=must_not_run
    ) == "BLOCKED"
    execution = db.scalar(select(DiscoveryRoleExecution).where(
        DiscoveryRoleExecution.run_id == run_id,
        DiscoveryRoleExecution.role_key == "STRUCTURED_ACQUISITION",
    ))
    assert execution.outcome == "BLOCKED"
    assert execution.reason_code == "NO_ELIGIBLE_TARGET"


def test_worker_reclaims_expired_lease_and_exhausts_bounded_retries(db, admin_client):
    client, _, version = admin_client
    created = _create_published_schedule(client, _schedule_payload(
        version.id, activate=False,
    ))
    requested = client.post(
        f"/api/v1/admin/discovery/schedules/{created.json()['data']['id']}/runs",
        json={"idempotency_key": "lease-recovery", "reason": "Exercise crash recovery"},
    )
    run_id = UUID(requested.json()["data"]["id"])
    run = db.get(DiscoveryRun, run_id)
    item = db.scalar(select(WorkItem).where(WorkItem.resource_id == run_id))
    run.status = "RUNNING"
    run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    item.status = "PROCESSING"
    item.started_at = datetime.now(UTC) - timedelta(minutes=10)
    db.commit()

    connection = db.connection()

    def session_factory():
        return Session(bind=connection, join_transaction_mode="create_savepoint")

    worker = DiscoveryRunWorker(session_factory=session_factory, lease_seconds=30)
    assert worker.claim_batch(1) == [(run_id, 1)]
    assert worker._record_failure(run_id, 1, RuntimeError("redacted")) == "RETRIED"
    db.expire_all()
    run = db.get(DiscoveryRun, run_id)
    item = db.scalar(select(WorkItem).where(WorkItem.resource_id == run_id))
    assert run.status == "PENDING"
    assert run.error_details == {"code": "RuntimeError", "retryable": True}
    assert item.status == "PENDING"
    run.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert worker.claim_batch(1) == [(run_id, 2)]
    assert worker._record_failure(run_id, 2, RuntimeError("redacted")) == "FAILED"
    db.expire_all()
    assert db.get(DiscoveryRun, run_id).status == "FAILED"
    assert db.scalar(select(WorkItem).where(WorkItem.resource_id == run_id)).status == "FAILED"


def _new_draft_schedule(client, version_id, **changes):
    return client.post("/api/v1/admin/discovery/schedules", json=_schedule_payload(version_id, activate=False, **changes))


def test_general_draft_is_inert_and_activation_requires_publication(db, admin_client):
    client, _, strategy = admin_client
    assert client.post("/api/v1/admin/discovery/schedules", json=_schedule_payload(strategy.id)).status_code == 422
    created = _new_draft_schedule(client, strategy.id)
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    assert data["status"] == "PAUSED" and data["publication_required"] and data["next_run_at"] is None
    path = f"/api/v1/admin/discovery/schedules/{data['id']}"
    assert client.post(path + "/runs", json={"idempotency_key": "inert-draft", "reason": "Must not execute draft"}).status_code == 409
    assert client.patch(path + "/status", json={"status": "ACTIVE", "expected_revision": 1, "reason": "Cannot resume draft"}).status_code == 409
    assert client.post(path + f"/versions/{data['draft_config_version_id']}/publish").status_code == 200
    row = db.get(DiscoverySchedule, UUID(data["id"]))
    assert row.config_version_id is not None and row.status == "PAUSED" and row.next_run_at is None
    assert db.scalar(select(func.count()).select_from(DiscoveryRun).where(DiscoveryRun.schedule_id == row.id)) == 0


def test_general_publication_rollback_and_manual_retry_keep_historical_context(db, admin_client):
    client, _, strategy = admin_client
    created = _create_published_schedule(client, _schedule_payload(strategy.id))
    data = created.json()["data"]
    schedule_id, first_id = data["id"], data["draft_config_version_id"]
    path = f"/api/v1/admin/discovery/schedules/{schedule_id}"
    original = client.post(path + "/runs", json={"idempotency_key": "old-published-version", "reason": "Original admitted run"})
    assert original.status_code == 201, original.text
    snapshot = original.json()["data"]["configuration_snapshot"]
    payload = _schedule_payload(strategy.id, frequency_definition={"interval_minutes": 60})
    payload.pop("activate")
    second = client.post(path + "/versions", json=payload).json()["data"]
    stale = client.post(path + "/versions", json={**payload, "frequency_definition": {"interval_minutes": 90}}).json()["data"]
    assert client.post(path + f"/versions/{second['id']}/publish").status_code == 200
    assert client.post(path + f"/versions/{stale['id']}/publish").status_code == 409
    current = next(r for r in client.get("/api/v1/admin/discovery/schedules").json()["data"] if r["id"] == schedule_id)
    assert client.patch(path + "/status", json={"status": "PAUSED", "expected_revision": current["revision"], "reason": "Pause for rollback review"}).status_code == 200
    restored = client.post(path + f"/versions/{first_id}/rollback")
    assert restored.status_code == 200, restored.text
    replacement = restored.json()["data"]
    assert replacement["id"] not in {first_id, second["id"]} and replacement["version"] == 4
    db.expire_all()
    row = db.get(DiscoverySchedule, UUID(schedule_id))
    assert row.status == "PAUSED" and row.frequency_definition["interval_minutes"] == 30
    assert str(row.config_version_id) == replacement["id"]
    run = db.get(DiscoveryRun, UUID(original.json()["data"]["id"]))
    assert run.configuration_snapshot == snapshot and snapshot["schedule_config_version_id"] == first_id
    replay = client.post(path + "/runs", json={"idempotency_key": "old-published-version", "reason": "Retry accepted request"})
    assert replay.json()["data"]["id"] == original.json()["data"]["id"]
    assert client.get(path + "/versions?limit=2&offset=1").json()["pagination"]["total"] == 4


def test_global_schedule_wrappers_cannot_publish_another_domain(db, admin_client):
    client, actor, strategy = admin_client
    first = _new_draft_schedule(client, strategy.id).json()["data"]
    second = _new_draft_schedule(client, strategy.id).json()["data"]
    ai = client.post("/api/v1/admin/configs", json={"config_key": "ai.task.cover_letter", "payload": {"temperature": 0.2}}).json()
    grant = db.scalar(select(AdminGrant).where(AdminGrant.user_id == actor.id))
    grant.role = "OPERATIONS"
    db.commit()
    path = f"/api/v1/admin/discovery/schedules/{first['id']}/versions/"
    for config_id in (second["draft_config_version_id"], ai["id"]):
        assert client.post(path + config_id + "/publish").status_code == 404
    assert client.post(f"/api/v1/admin/configs/{first['draft_config_version_id']}/publish").status_code == 403
    assert client.post(path + first["draft_config_version_id"] + "/publish").status_code == 200


def test_general_scheduler_enforces_published_window(db, admin_client):
    from app.application.jobs.discovery_schedules import create_discovery_run

    client, _, strategy = admin_client
    created = _create_published_schedule(client, _schedule_payload(strategy.id,
        window_definition={"start": "22:00", "end": "02:00"}))
    row = db.get(DiscoverySchedule, UUID(created.json()["data"]["id"]))
    now = datetime(2026, 11, 14, 12, tzinfo=UTC)
    row.next_run_at = now - timedelta(minutes=1)
    db.commit()
    assert schedule_due_discovery_runs(db, now=now) == []
    db.refresh(row)
    assert row.next_run_at == datetime(2026, 11, 14, 22, tzinfo=UTC)
    with pytest.raises(ValueError, match="window"):
        create_discovery_run(db, schedule=row, trigger_type="MANUAL", run_key=f"window-{uuid4()}", now=now)
    assert len(schedule_due_discovery_runs(db, now=now.replace(hour=23))) == 1


def test_changed_strategy_blocks_publication_and_isolates_scheduler_failure(db, admin_client):
    from app.infrastructure.db.models import AuditEvent

    client, _, strategy = admin_client
    draft = _new_draft_schedule(client, strategy.id).json()["data"]
    strategy.status = "RETIRED"
    db.commit()
    path = f"/api/v1/admin/discovery/schedules/{draft['id']}/versions/{draft['draft_config_version_id']}/publish"
    assert client.post(path).status_code == 409
    db.expire_all()
    schedule = db.get(DiscoverySchedule, UUID(draft["id"]))
    assert schedule.config_version_id is None
    # An unavailable strategy on an already-published schedule must pause that
    # schedule durably instead of poisoning the whole scheduling transaction.
    strategy.status = "PUBLISHED"
    db.commit()
    assert client.post(path).status_code == 200
    current = next(r for r in client.get("/api/v1/admin/discovery/schedules").json()["data"] if r["id"] == draft["id"])
    assert client.patch(f"/api/v1/admin/discovery/schedules/{draft['id']}/status", json={
        "status": "ACTIVE", "expected_revision": current["revision"], "reason": "Enable published schedule",
    }).status_code == 200
    db.refresh(schedule)
    schedule.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    strategy.status = "RETIRED"
    db.commit()
    assert schedule_due_discovery_runs(db) == []
    db.refresh(schedule)
    assert schedule.status == "PAUSED"
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.resource_id == schedule.id,
        AuditEvent.event_type == "DISCOVERY_SCHEDULE_CONFIG_BLOCKED")) is not None


def test_general_publication_is_atomic_when_required_audit_fails(db, admin_client, monkeypatch):
    from fastapi import HTTPException

    from app.api.v1 import admin as api
    from app.infrastructure.db.models import GovernanceConfigVersion

    client, _, strategy = admin_client
    initial = _create_published_schedule(client, _schedule_payload(strategy.id)).json()["data"]
    path = f"/api/v1/admin/discovery/schedules/{initial['id']}/versions"
    payload = _schedule_payload(strategy.id, frequency_definition={"interval_minutes": 90})
    payload.pop("activate")
    draft = client.post(path, json=payload).json()["data"]
    real_audit = api._audit

    def unavailable_audit(*args, **kwargs):
        if args[2] == "CONFIG_VERSION_PUBLISHED":
            raise HTTPException(status_code=503, detail="Required audit unavailable")
        return real_audit(*args, **kwargs)

    monkeypatch.setattr(api, "_audit", unavailable_audit)
    assert client.post(path + f"/{draft['id']}/publish").status_code == 503
    db.expire_all()
    row = db.get(DiscoverySchedule, UUID(initial["id"]))
    assert str(row.config_version_id) == initial["draft_config_version_id"]
    assert row.frequency_definition["interval_minutes"] == 30
    assert db.get(GovernanceConfigVersion, UUID(draft["id"])).status == "DRAFT"
    assert db.get(GovernanceConfigVersion, row.config_version_id).status == "PUBLISHED"


def test_general_schedule_dates_and_manual_permission_are_effective(db, admin_client):
    from app.application.jobs.discovery_schedules import create_discovery_run

    client, _, strategy = admin_client
    future = datetime.now(UTC) + timedelta(days=2)
    initial = _create_published_schedule(client, _schedule_payload(strategy.id,
        start_at=future.isoformat(), end_at=(future + timedelta(hours=1)).isoformat(),
        manual_execution_enabled=False)).json()["data"]
    row = db.get(DiscoverySchedule, UUID(initial["id"]))
    row.next_run_at = future - timedelta(minutes=1)
    db.commit()
    assert schedule_due_discovery_runs(db, now=future - timedelta(minutes=1)) == []
    db.refresh(row)
    assert row.next_run_at == future
    assert client.post(f"/api/v1/admin/discovery/schedules/{row.id}/runs", json={
        "idempotency_key": "disabled-manual", "reason": "Must respect published permission"}).status_code == 409
    for now in (future - timedelta(minutes=1), future + timedelta(hours=2)):
        with pytest.raises(ValueError, match="start/end"):
            create_discovery_run(db, schedule=row, trigger_type="MANUAL", run_key=str(uuid4()), now=now)
    assert schedule_due_discovery_runs(db, now=future + timedelta(hours=2)) == []
    db.refresh(row)
    assert row.status == "RETIRED" and row.next_run_at is None
