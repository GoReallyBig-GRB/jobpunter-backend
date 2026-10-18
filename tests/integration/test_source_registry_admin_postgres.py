import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.api.v1 import admin as admin_api
from app.infrastructure.db.models import AcquisitionRun, AdminAuditEvent, AdminGrant, OutboxEvent, RawSourceRecord, Source, SourceConnector, SourceConnectorConfigurationVersion, User, WorkItem
from app.infrastructure.db.session import get_db
from app.main import app
from app.ports.identity import AuthenticatedPrincipal


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


@pytest.fixture
def db():
    from sqlalchemy import create_engine

    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def make_client(db, user):
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=user.id,
        auth_provider="test",
    )
    connection = db.connection()

    def override_db():
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def make_user(db, *, admin):
    user = User(display_name=f"Source registry test {uuid4()}")
    db.add(user)
    db.flush()
    if admin:
        db.add(AdminGrant(user_id=user.id, role="ADMIN", active=True))
        db.flush()
    return user


class FakeHealthHTTPClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    @contextmanager
    def stream(self, method, url, *, params=None, headers=None):
        assert method == "GET"
        self.calls.append((url, params))
        yield self.responses.pop(0)


def greenhouse_response(payload, status_code=200):
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("GET", "https://boards-api.greenhouse.io/test"),
    )



def approve_source(client, source_id):
    response = client.patch(
        f"/api/v1/admin/sources/{source_id}/permission-review",
        json={
            "status": "APPROVED",
            "reference": "test-policy-review://approved",
            "access_method": "PUBLIC_JOB_BOARD_API",
            "terms_reference": "https://example.test/source-terms",
            "rate_limit_policy": "Configured connector limit: 10 requests per minute",
            "rate_limit_reference": "https://example.test/rate-limits",
        },
    )
    assert response.status_code == 200, response.text

def test_admin_can_create_list_and_pause_source_without_exposing_board_token(db):
    user = make_user(db, admin=True)
    client = make_client(db, user)
    try:
        response = client.post(
            "/api/v1/admin/sources",
            headers={"x-request-id": "source-registry-create"},
            json={"name": "Example board", "board_token": "example-board", "application_route_type": "free ats"},
        )
        assert response.status_code == 201, response.text
        data = response.json()["data"]
        assert data["name"] == "Example board"
        assert data["source_type"] == "GREENHOUSE_JOB_BOARD"
        assert data["status"] == "ACTIVE"
        assert data["application_route_type"] == "FREE_ATS"
        assert len(data["connectors"]) == 1
        connector = data["connectors"][0]
        assert connector["connector_type"] == "GREENHOUSE_JOB_BOARD"
        assert connector["status"] == "ACTIVE"
        assert "board_token" not in response.text

        source_id = data["id"]
        connector_id = connector["id"]
        listing = client.get("/api/v1/admin/sources")
        assert listing.status_code == 200
        listed = next(item for item in listing.json()["data"] if item["id"] == source_id)
        assert listed["connectors"][0]["id"] == connector_id
        assert "board_token" not in listing.text

        paused = client.patch(
            f"/api/v1/admin/sources/{source_id}/status",
            headers={"x-request-id": "source-registry-pause"},
            json={"status": "PAUSED"},
        )
        assert paused.status_code == 200, paused.text
        assert paused.json()["data"]["status"] == "PAUSED"

        run = client.post(
            f"/api/v1/admin/sources/{source_id}/acquisition-runs",
            json={"connector_id": connector_id, "run_key": "must-not-run-while-paused"},
        )
        assert run.status_code == 409
        assert "must both be active" in run.text

        db.expire_all()
        source = db.get(Source, UUID(source_id))
        connector_row = db.get(SourceConnector, UUID(connector_id))
        assert source.status == "PAUSED"
        assert source.configuration == {"application_route_type": "FREE_ATS"}
        assert connector_row.configuration == {"board_token": "example-board"}
        events = db.scalars(select(AdminAuditEvent).where(
            AdminAuditEvent.resource_id == source.id,
        ).order_by(AdminAuditEvent.created_at, AdminAuditEvent.id)).all()
        assert [event.action for event in events] == ["SOURCE_CREATED", "SOURCE_STATUS_CHANGED"]
        assert all(event.actor_user_id == user.id for event in events)
        assert all("example-board" not in str(event.details) for event in events)
        assert [event.request_id for event in events] == [
            "source-registry-create", "source-registry-pause",
        ]
    finally:
        client.close()


def test_non_admin_cannot_read_or_create_source_registry_entries(db):
    user = make_user(db, admin=False)
    client = make_client(db, user)
    try:
        listed = client.get("/api/v1/admin/sources")
        created = client.post(
            "/api/v1/admin/sources",
            json={"name": "Forbidden board", "board_token": "forbidden"},
        )
        assert listed.status_code == 403
        assert created.status_code == 403
        assert client.get("/api/v1/admin/operations/summary").status_code == 403
        assert client.get("/api/v1/admin/operations/work-items").status_code == 403
        assert db.scalar(select(Source).where(Source.name == "Forbidden board")) is None
    finally:
        client.close()


@pytest.mark.parametrize(
    "route_type",
    ["looks-free-from-url", "FREE_JOB_SITE", "", "unknown-route"],
)
def test_source_registry_rejects_unrecognized_application_route_type(db, route_type):
    user = make_user(db, admin=True)
    client = make_client(db, user)
    try:
        response = client.post(
            "/api/v1/admin/sources",
            json={
                "name": "Invalid route classification",
                "board_token": "valid-board",
                "application_route_type": route_type,
            },
        )
        assert response.status_code == 422
        assert db.scalar(
            select(Source).where(Source.name == "Invalid route classification")
        ) is None
    finally:
        client.close()


@pytest.mark.parametrize("board_token", ["../secret", "", "contains space", "x" * 129])
def test_source_registry_rejects_invalid_board_token(db, board_token):
    user = make_user(db, admin=True)
    client = make_client(db, user)
    try:
        response = client.post(
            "/api/v1/admin/sources",
            json={"name": "Invalid board", "board_token": board_token},
        )
        assert response.status_code == 422
        assert db.scalar(select(Source).where(Source.name == "Invalid board")) is None
    finally:
        client.close()


def test_source_status_update_rejects_unknown_source_and_duplicate_status(db):
    user = make_user(db, admin=True)
    client = make_client(db, user)
    try:
        missing = client.patch(
            f"/api/v1/admin/sources/{uuid4()}/status",
            json={"status": "PAUSED"},
        )
        assert missing.status_code == 404

        created = client.post(
            "/api/v1/admin/sources",
            json={"name": "Status board", "board_token": "status-board"},
        )
        assert created.status_code == 201
        source_id = created.json()["data"]["id"]
        duplicate = client.patch(
            f"/api/v1/admin/sources/{source_id}/status",
            json={"status": "ACTIVE"},
        )
        assert duplicate.status_code == 409
    finally:
        client.close()


def test_source_connector_health_check_records_success_without_exposing_board_token(db, monkeypatch):
    user = make_user(db, admin=True)
    client = make_client(db, user)
    fake_http = FakeHealthHTTPClient([
        greenhouse_response({"name": "Example Company"}),
        greenhouse_response({"jobs": [{"id": 1}, {"id": 2}]}),
    ])
    original_connector = admin_api.GreenhouseJobBoardConnector

    class InjectedGreenhouseConnector(original_connector):
        def __init__(self):
            super().__init__(http_client=fake_http, allow_unsafe_http_client=True)

    monkeypatch.setattr(admin_api, "GreenhouseJobBoardConnector", InjectedGreenhouseConnector)
    try:
        created = client.post(
            "/api/v1/admin/sources",
            json={"name": "Health board", "board_token": "health-board"},
        )
        assert created.status_code == 201, created.text
        source = created.json()["data"]
        connector_id = source["connectors"][0]["id"]
        approve_source(client, source["id"])
        response = client.post(
            f"/api/v1/admin/sources/{source['id']}/connectors/{connector_id}/health-check",
            headers={"x-request-id": "health-check-success"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["status"] == "ACTIVE"
        assert response.json()["data"]["board_name"] == "Example Company"
        assert response.json()["data"]["job_count"] == 2
        assert response.json()["data"]["last_verified_at"] is not None
        assert response.json()["data"]["source_last_observed_at"] is not None
        assert "health-board" not in response.text

        db.expire_all()
        source_row = db.get(Source, UUID(source["id"]))
        assert source_row.last_observed_at is not None
        connector = db.get(SourceConnector, UUID(connector_id))
        assert connector.status == "ACTIVE"
        assert connector.last_verified_at is not None
        event = db.scalar(select(AdminAuditEvent).where(
            AdminAuditEvent.action == "SOURCE_CONNECTOR_HEALTH_CHECKED",
            AdminAuditEvent.resource_id == connector.id,
        ))
        assert event is not None
        assert event.actor_user_id == user.id
        assert event.request_id == "health-check-success"
        assert event.details["job_count"] == 2
        assert "health-board" not in str(event.details)
    finally:
        client.close()


def test_failed_source_connector_health_check_degrades_connector_and_blocks_acquisition(db, monkeypatch):
    user = make_user(db, admin=True)
    client = make_client(db, user)
    fake_http = FakeHealthHTTPClient([
        greenhouse_response({"error": "private upstream body"}, status_code=503),
    ])
    original_connector = admin_api.GreenhouseJobBoardConnector

    class InjectedGreenhouseConnector(original_connector):
        def __init__(self):
            super().__init__(http_client=fake_http, allow_unsafe_http_client=True)

    monkeypatch.setattr(admin_api, "GreenhouseJobBoardConnector", InjectedGreenhouseConnector)
    try:
        created = client.post(
            "/api/v1/admin/sources",
            json={"name": "Broken health board", "board_token": "broken-board"},
        )
        assert created.status_code == 201, created.text
        source = created.json()["data"]
        connector_id = source["connectors"][0]["id"]
        approve_source(client, source["id"])
        response = client.post(
            f"/api/v1/admin/sources/{source['id']}/connectors/{connector_id}/health-check",
            headers={"x-request-id": "health-check-failure"},
        )
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "SOURCE_HEALTH_CHECK_FAILED"
        assert "private upstream body" not in response.text

        db.expire_all()
        connector = db.get(SourceConnector, UUID(connector_id))
        assert connector.status == "DEGRADED"
        event = db.scalar(select(AdminAuditEvent).where(
            AdminAuditEvent.action == "SOURCE_CONNECTOR_HEALTH_CHECK_FAILED",
            AdminAuditEvent.resource_id == connector.id,
        ))
        assert event is not None
        assert event.outcome == "FAILED"
        assert event.details["error_code"] == "greenhouse_request_failed"
        assert event.request_id == "health-check-failure"
        assert "broken-board" not in str(event.details)

        run = client.post(
            f"/api/v1/admin/sources/{source['id']}/acquisition-runs",
            json={"connector_id": connector_id, "run_key": "degraded-connector-run"},
        )
        assert run.status_code == 409
    finally:
        client.close()

def test_manual_acquisition_enforces_current_forbidden_target_rules_before_network(db, monkeypatch):
    from app.infrastructure.connectors.greenhouse import GreenhouseJobBoardConnector

    class NoNetworkClient:
        def __init__(self):
            self.calls = 0

        def stream(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("forbidden target must be rejected before network access")

    client = make_client(db, make_user(db, admin=True))
    network = NoNetworkClient()
    original_init = GreenhouseJobBoardConnector.__init__

    def controlled_init(self, http_client=None, *, allow_unsafe_http_client=False):
        original_init(self, http_client=network, allow_unsafe_http_client=True)

    monkeypatch.setattr(GreenhouseJobBoardConnector, "__init__", controlled_init)
    try:
        created = client.post(
            "/api/v1/admin/sources",
            json={"name": "Forbidden target regression", "board_token": "blocked-board"},
        )
        assert created.status_code == 201, created.text
        source = created.json()["data"]
        source_id = source["id"]
        connector_id = source["connectors"][0]["id"]
        assert approve_source(client, source_id).status_code == 200

        rule_response = client.post(
            "/api/v1/admin/target-policy/forbidden-rules",
            json={
                "rule_type": "HOST_EXACT",
                "pattern": "boards-api.greenhouse.io",
                "connector_scope": "GREENHOUSE_JOB_BOARD",
                "reason": "Block the board endpoint in this regression test",
            },
        )
        assert rule_response.status_code == 201, rule_response.text

        response = client.post(
            f"/api/v1/admin/sources/{source_id}/acquisition-runs",
            json={"connector_id": connector_id, "run_key": "forbidden-target-regression"},
        )
        assert response.status_code == 502, response.text
        assert response.json()["detail"]["code"] == "SOURCE_RESTRICTED"
        assert network.calls == 0

        run = db.scalar(select(AcquisitionRun).where(
            AcquisitionRun.source_id == UUID(source_id),
            AcquisitionRun.run_key == "forbidden-target-regression",
        ))
        assert run is not None
        assert run.status == "RESTRICTED"
    finally:
        client.close()


def test_connector_configuration_updates_publish_immutable_versions_and_reject_stale_revision(db):
    user = make_user(db, admin=True)
    client = make_client(db, user)
    try:
        created = client.post(
            "/api/v1/admin/sources",
            json={"name": "Versioned board", "board_token": "original-board"},
        )
        assert created.status_code == 201, created.text
        source_data = created.json()["data"]
        connector_data = source_data["connectors"][0]
        source_id = source_data["id"]
        connector_id = connector_data["id"]
        initial_revision = connector_data["revision"]
        assert connector_data["active_configuration_version"] == 1

        published = client.patch(
            f"/api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration",
            headers={"x-request-id": "connector-config-v2"},
            json={
                "expected_revision": initial_revision,
                "configuration": {"board_token": "replacement-board"},
                "reason": "Rotate the source board identifier after the employer changed it",
            },
        )
        assert published.status_code == 200, published.text
        published_data = published.json()["data"]
        assert published_data["status"] == "DRAFT"
        assert published_data["draft_version"] == 2
        assert published_data["active_configuration_version"] == 1
        assert published_data["revision"] == initial_revision + 1
        assert "replacement-board" not in published.text

        history = client.get(
            f"/api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration-versions"
        )
        assert history.status_code == 200, history.text
        versions = history.json()["data"]
        assert [version["version"] for version in versions] == [2, 1]
        assert versions[0]["status"] == "DRAFT"
        assert versions[0]["active"] is False
        assert versions[1]["status"] == "PUBLISHED"
        assert versions[1]["active"] is True
        assert versions[0]["configuration"]["board_token"] == "[REDACTED]"
        assert versions[1]["configuration"]["board_token"] == "[REDACTED]"
        assert versions[0]["configuration_fingerprint"] != versions[1]["configuration_fingerprint"]

        stale = client.patch(
            f"/api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration",
            json={
                "expected_revision": initial_revision,
                "configuration": {"board_token": "stale-board"},
                "reason": "Attempt a stale concurrent edit",
            },
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "SOURCE_CONNECTOR_REVISION_CONFLICT"

        activated = client.post(
            f"/api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration-versions/2/publish",
            headers={"x-request-id": "connector-config-publish"},
            json={
                "expected_revision": initial_revision + 1,
                "reason": "Publish the reviewed source configuration draft",
            },
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["data"]["status"] == "PUBLISHED"
        assert activated.json()["data"]["active_configuration_version"] == 2
        assert activated.json()["data"]["revision"] == initial_revision + 2

        rolled_back = client.post(
            f"/api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration-versions/1/activate",
            headers={"x-request-id": "connector-config-rollback"},
            json={
                "expected_revision": initial_revision + 2,
                "reason": "Restore the last known-good source board configuration",
            },
        )
        assert rolled_back.status_code == 200, rolled_back.text
        assert rolled_back.json()["data"]["active_configuration_version"] == 1
        assert rolled_back.json()["data"]["revision"] == initial_revision + 3
        assert "original-board" not in rolled_back.text

        history_after_rollback = client.get(
            f"/api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration-versions"
        )
        assert history_after_rollback.status_code == 200
        active_versions = [item["version"] for item in history_after_rollback.json()["data"] if item["active"]]
        assert active_versions == [1]

        db.expire_all()
        connector = db.get(SourceConnector, UUID(connector_id))
        assert connector.active_configuration_version == 1
        assert connector.configuration == {"board_token": "original-board"}
        version_rows = db.scalars(
            select(SourceConnectorConfigurationVersion)
            .where(SourceConnectorConfigurationVersion.source_connector_id == connector.id)
            .order_by(SourceConnectorConfigurationVersion.version)
        ).all()
        assert [row.version for row in version_rows] == [1, 2]
        assert version_rows[0].configuration == {"board_token": "original-board"}
        assert version_rows[1].configuration == {"board_token": "replacement-board"}
        audit = db.scalars(
            select(AdminAuditEvent)
            .where(AdminAuditEvent.action == "SOURCE_CONNECTOR_CONFIGURATION_PUBLISHED")
        ).all()
        assert len(audit) == 1
        assert audit[0].request_id == "connector-config-publish"
        assert "replacement-board" not in str(audit[0].details)
        rollback_audit = db.scalar(select(AdminAuditEvent).where(
            AdminAuditEvent.action == "SOURCE_CONNECTOR_CONFIGURATION_ROLLED_BACK",
        ))
        assert rollback_audit is not None
        assert rollback_audit.request_id == "connector-config-rollback"
        assert rollback_audit.details["previous_version"] == 2
        assert rollback_audit.details["activated_version"] == 1
    finally:
        client.close()


def test_connector_configuration_update_validates_required_fields(db):
    user = make_user(db, admin=True)
    client = make_client(db, user)
    try:
        created = client.post(
            "/api/v1/admin/sources",
            json={"name": "Validated board", "board_token": "valid-board"},
        )
        assert created.status_code == 201, created.text
        source = created.json()["data"]
        connector = source["connectors"][0]
        response = client.patch(
            f"/api/v1/admin/sources/{source['id']}/connectors/{connector['id']}/configuration",
            json={
                "expected_revision": connector["revision"],
                "configuration": {},
                "reason": "Invalid empty connector configuration",
            },
        )
        assert response.status_code == 422
        assert db.scalar(select(SourceConnectorConfigurationVersion.id).where(
            SourceConnectorConfigurationVersion.source_connector_id == UUID(connector["id"]),
        )) is not None
        assert db.scalar(select(SourceConnectorConfigurationVersion.version).where(
            SourceConnectorConfigurationVersion.source_connector_id == UUID(connector["id"]),
            SourceConnectorConfigurationVersion.version == 2,
        )) is None
    finally:
        client.close()

def test_acquisition_run_records_the_exact_published_connector_configuration_version(db, monkeypatch):
    user = make_user(db, admin=True)
    client = make_client(db, user)
    original_connector = admin_api.GreenhouseJobBoardConnector

    class EmptyInjectedConnector(original_connector):
        def acquire(self, *, source_id, context):
            return []

    monkeypatch.setattr(admin_api, "GreenhouseJobBoardConnector", EmptyInjectedConnector)
    try:
        created = client.post(
            "/api/v1/admin/sources",
            json={"name": "Version trace board", "board_token": "version-trace-board"},
        )
        assert created.status_code == 201, created.text
        source = created.json()["data"]
        connector = source["connectors"][0]
        approve_source(client, source["id"])

        run_response = client.post(
            f"/api/v1/admin/sources/{source['id']}/acquisition-runs",
            json={"connector_id": connector["id"], "run_key": "config-version-trace-run"},
        )
        assert run_response.status_code == 200, run_response.text
        response_data = run_response.json()["data"]
        assert response_data["status"] == "SUCCEEDED"
        assert response_data["configuration_version"] == 1
        assert response_data["configuration_version_id"]

        changed = client.patch(
            f"/api/v1/admin/sources/{source['id']}/connectors/{connector['id']}/configuration",
            json={
                "expected_revision": connector["revision"],
                "configuration": {"board_token": "version-trace-board-v2"},
                "reason": "Update the board configuration after the first run",
            },
        )
        assert changed.status_code == 200, changed.text
        draft_data = changed.json()["data"]
        publish_response = client.post(
            f"/api/v1/admin/sources/{source['id']}/connectors/{connector['id']}/configuration-versions/{draft_data['draft_version']}/publish",
            json={
                "expected_revision": draft_data["revision"],
                "reason": "Publish the configuration used for future runs",
            },
        )
        assert publish_response.status_code == 200, publish_response.text
        replay = client.post(
            f"/api/v1/admin/sources/{source['id']}/acquisition-runs",
            json={"connector_id": connector["id"], "run_key": "config-version-trace-run"},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["data"]["run_id"] == response_data["run_id"]
        assert replay.json()["data"]["configuration_version"] == 1
        assert replay.json()["data"]["configuration_version_id"] == response_data["configuration_version_id"]

        db.expire_all()
        run = db.scalar(select(AcquisitionRun).where(
            AcquisitionRun.source_id == UUID(source["id"]),
            AcquisitionRun.run_key == "config-version-trace-run",
        ))
        version = db.get(
            SourceConnectorConfigurationVersion,
            UUID(response_data["configuration_version_id"]),
        )
        assert run is not None
        assert version is not None
        assert run.configuration_version_id == version.id
        assert version.source_connector_id == UUID(connector["id"])
        assert version.version == 1
        assert version.configuration_fingerprint
        assert "version-trace-board" not in run_response.text
    finally:
        client.close()

def test_admin_operational_apis_recover_expired_runs_and_retry_only_supported_outbox_events(db):
    user = make_user(db, admin=True)
    client = make_client(db, user)
    try:
        created = client.post(
            "/api/v1/admin/sources",
            json={"name": "Operational source", "board_token": "operational-source"},
        )
        assert created.status_code == 201, created.text
        source_data = created.json()["data"]
        source_id = UUID(source_data["id"])
        connector_id = UUID(source_data["connectors"][0]["id"])
        approve_source(client, str(source_id))

        now = datetime.now(timezone.utc)
        stale_run = AcquisitionRun(
            source_id=source_id,
            source_connector_id=connector_id,
            run_key=f"stale-operation-{uuid4()}",
            status="RUNNING",
            started_at=now - timedelta(hours=2),
            lease_expires_at=now - timedelta(minutes=1),
            metrics={
                "acquired_count": 5,
                "persisted_count": 2,
                "observed_count": 5,
                "new_count": 2,
                "changed_count": 1,
                "unchanged_count": 2,
                "error_category": "WORKER_LOST",
                "failed_item_count": 3,
                "failure_categories": ["WORKER_LOST"],
                "retry_after_seconds": 120,
            },
            created_at=now - timedelta(hours=2),
        )
        fresh_run = AcquisitionRun(
            source_id=source_id,
            source_connector_id=connector_id,
            run_key=f"fresh-operation-{uuid4()}",
            status="RUNNING",
            started_at=now,
            lease_expires_at=now + timedelta(minutes=10),
            metrics={},
            created_at=now,
        )
        db.add_all([stale_run, fresh_run])
        db.flush()
        stale_run_id, fresh_run_id = stale_run.id, fresh_run.id

        item = WorkItem(
            work_type="UNREGISTERED_TEST_WORK",
            idempotency_key=f"work-item-{uuid4()}",
            status="FAILED",
            attempt_count=2,
            error_code="RuntimeError",
            error_details={"category": "RuntimeError"},
            created_at=now,
            updated_at=now,
            completed_at=now,
        )
        db.add(item)
        db.flush()
        item_id = item.id

        recovery = client.post(
            "/api/v1/admin/operations/acquisition-runs/recover-expired",
            headers={"x-request-id": "ops-recovery"},
            json={"limit": 10, "reason": "Recover expired acquisition leases after worker interruption"},
        )
        assert recovery.status_code == 200, recovery.text
        assert recovery.json()["data"]["recovered_count"] == 1

        db.expire_all()
        assert db.get(AcquisitionRun, stale_run_id).status == "FAILED"
        assert db.get(AcquisitionRun, fresh_run_id).status == "RUNNING"

        stale_record = RawSourceRecord(
            acquisition_run_id=stale_run_id,
            source_id=source_id,
            external_record_id=f"ops-raw-{uuid4()}",
            deduplication_key=f"ops-dedup-{uuid4()}",
            source_url="https://jobs.example.test/role",
            raw_content={"title": "Example role"},
            content_hash=f"ops-hash-{uuid4()}",
            status="ACQUIRED",
            acquired_at=now,
            created_at=now,
        )
        db.add(stale_record)
        db.flush()
        retryable_event = OutboxEvent(
            event_type="RAW_SOURCE_RECORD_ACQUIRED",
            aggregate_type="RawSourceRecord",
            aggregate_id=stale_record.id,
            payload={"raw_source_record_id": str(stale_record.id), "private_marker": "must-not-be-returned"},
            status="DEAD",
            attempt_count=8,
            available_at=now,
            last_error="handler_error:RuntimeError",
            created_at=now,
        )
        unsupported_event = OutboxEvent(
            event_type="WORK_REQUESTED",
            aggregate_type="WorkItem",
            aggregate_id=item_id,
            payload={"work_type": "UNREGISTERED_TEST_WORK", "private_marker": "secret"},
            status="DEAD",
            attempt_count=8,
            available_at=now,
            last_error="handler_error:LookupError",
            created_at=now,
        )
        mismatched_event = OutboxEvent(
            event_type="RAW_SOURCE_RECORD_ACQUIRED",
            aggregate_type="RawSourceRecord",
            aggregate_id=uuid4(),
            payload={"raw_source_record_id": str(stale_record.id)},
            status="DEAD",
            attempt_count=8,
            available_at=now,
            last_error="handler_error:RuntimeError",
            created_at=now,
        )
        db.add_all([retryable_event, unsupported_event, mismatched_event])
        db.commit()

        history = client.get(
            f"/api/v1/admin/sources/{source_id}/acquisition-runs?limit=10&offset=0"
        )
        assert history.status_code == 200, history.text
        history_data = history.json()["data"]
        recovered_row = next(row for row in history_data if row["run_id"] == str(stale_run_id))
        assert recovered_row["acquired_count"] == 5
        assert recovered_row["persisted_count"] == 2
        assert recovered_row["duplicate_count"] == 3
        assert recovered_row["error_category"] == "WORKER_LOST"
        assert recovered_row["failed_item_count"] == 3
        assert recovered_row["failure_categories"] == ["WORKER_LOST"]
        assert recovered_row["retry_after_seconds"] == 120
        assert recovered_row["downstream_processing"]["tracked_count"] == 1
        assert recovered_row["downstream_processing"]["failed_count"] == 1

        detail = client.get(
            f"/api/v1/admin/sources/{source_id}/acquisition-runs/{stale_run_id}"
        )
        assert detail.status_code == 200, detail.text
        detail_data = detail.json()["data"]
        assert detail_data["error_category"] == "WORKER_LOST"
        assert detail_data["failed_item_count"] == 3
        assert detail_data["failure_categories"] == ["WORKER_LOST"]
        assert detail_data["retry_after_seconds"] == 120
        assert detail_data["downstream_processing"]["failed_count"] == 1

        retryable_id, unsupported_id, mismatched_id = (
            retryable_event.id, unsupported_event.id, mismatched_event.id
        )

        retry = client.post(
            f"/api/v1/admin/operations/outbox-events/{retryable_id}/retry",
            headers={"x-request-id": "ops-outbox-retry"},
            json={"reason": "Retry canonicalization after correcting the transient worker failure"},
        )
        assert retry.status_code == 200, retry.text
        assert retry.json()["data"]["status"] == "PENDING"
        assert retry.json()["data"]["attempt_count"] == 0

        unsupported = client.post(
            f"/api/v1/admin/operations/outbox-events/{unsupported_id}/retry",
            json={"reason": "This event has no approved production retry handler"},
        )
        assert unsupported.status_code == 409
        assert unsupported.json()["detail"]["code"] == "OUTBOX_EVENT_TYPE_NOT_RETRYABLE"

        mismatched = client.post(
            f"/api/v1/admin/operations/outbox-events/{mismatched_id}/retry",
            json={"reason": "Reject event whose aggregate does not match the raw record"},
        )
        assert mismatched.status_code == 409
        assert mismatched.json()["detail"]["code"] == "OUTBOX_EVENT_AGGREGATE_MISMATCH"

        work_items = client.get("/api/v1/admin/operations/work-items?status=FAILED&limit=10")
        assert work_items.status_code == 200, work_items.text
        assert work_items.json()["pagination"]["total"] == 1
        assert work_items.json()["data"][0]["id"] == str(item_id)

        outbox = client.get("/api/v1/admin/operations/outbox-events?status=PENDING&limit=10")
        assert outbox.status_code == 200, outbox.text
        assert any(row["id"] == str(retryable_id) for row in outbox.json()["data"])
        assert "must-not-be-returned" not in outbox.text

        summary = client.get("/api/v1/admin/operations/summary")
        assert summary.status_code == 200, summary.text
        assert summary.json()["data"]["stale_running_acquisition_runs"] == 0
        assert summary.json()["data"]["work_items"]["FAILED"] == 1

        db.expire_all()
        saved_event = db.get(OutboxEvent, retryable_id)
        assert saved_event.status == "PENDING"
        assert saved_event.attempt_count == 0
        recovery_audit = db.scalar(select(AdminAuditEvent).where(
            AdminAuditEvent.action == "EXPIRED_ACQUISITION_RUNS_RECOVERED",
        ))
        retry_audit = db.scalar(select(AdminAuditEvent).where(
            AdminAuditEvent.action == "OUTBOX_EVENT_RETRY_REQUESTED",
            AdminAuditEvent.resource_id == retryable_id,
        ))
        assert recovery_audit is not None
        assert recovery_audit.request_id == "ops-recovery"
        assert recovery_audit.details["recovered_count"] == 1
        assert retry_audit is not None
        assert retry_audit.request_id == "ops-outbox-retry"
        assert retry_audit.details["previous_status"] == "DEAD"
        assert retry_audit.details["previous_attempt_count"] == 8
    finally:
        client.close()



@pytest.mark.parametrize(
    "policy_change,expected_category",
    [
        ("source_configuration", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("source_base_url", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("source_type", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("permission_review_reference", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("permission_reviewed_at", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("source_deactivated", "SOURCE_POLICY_CHANGED"),
        ("permission_revoked", "PERMISSION_REVIEW_REQUIRED"),
        ("connector_configuration", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("connector_capabilities", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("connector_limits", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("active_configuration_version", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("connector_type", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("connector_version", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("connector_deactivated", "CONNECTOR_POLICY_CHANGED"),
    ],
)
def test_manual_acquisition_blocks_second_request_after_persisted_policy_change(
    db, monkeypatch, policy_change, expected_category
):
    """Exercise the real Admin acquisition route against persisted mid-run changes."""
    from app.infrastructure.connectors.greenhouse import GreenhouseJobBoardConnector

    client = make_client(db, make_user(db, admin=True))
    ids = {}
    network_calls = {"count": 0}

    def fake_get_json(_connector, _client, _url, *, params=None):
        network_calls["count"] += 1
        if network_calls["count"] > 1:
            raise AssertionError("changed policy must block the second HTTP request")
        source = db.get(Source, UUID(ids["source_id"]))
        connector = db.get(SourceConnector, UUID(ids["connector_id"]))
        if policy_change == "source_configuration":
            source.configuration = {"changed_during_run": True}
        elif policy_change == "source_base_url":
            source.base_url = "https://changed-during-run.example.test"
        elif policy_change == "source_type":
            source.source_type = "CHANGED_DURING_RUN"
        elif policy_change == "permission_review_reference":
            source.permission_review_reference = "terms review changed during run"
        elif policy_change == "permission_reviewed_at":
            source.permission_reviewed_at = datetime.now(timezone.utc) + timedelta(seconds=1)
        elif policy_change == "source_deactivated":
            source.status = "INACTIVE"
        elif policy_change == "permission_revoked":
            source.permission_review_status = "REJECTED"
        elif policy_change == "connector_configuration":
            connector.configuration = {"board_token": "changed-during-run"}
        elif policy_change == "connector_capabilities":
            connector.capabilities = {"supports_pagination": False}
        elif policy_change == "connector_limits":
            connector.limits = {"max_pages": 1}
        elif policy_change == "active_configuration_version":
            connector.active_configuration_version = 2
        elif policy_change == "connector_type":
            connector.connector_type = "JSON_API"
        elif policy_change == "connector_version":
            connector.connector_version = "2.0.0"
        elif policy_change == "connector_deactivated":
            connector.status = "INACTIVE"
        db.flush()
        return {"name": "Example Board"}

    monkeypatch.setattr(GreenhouseJobBoardConnector, "_get_json", fake_get_json)

    try:
        created = client.post(
            "/api/v1/admin/sources",
            json={"name": f"Mid-run policy change {policy_change}", "board_token": "policy-change-board"},
        )
        assert created.status_code == 201, created.text
        source = created.json()["data"]
        ids["source_id"] = source["id"]
        ids["connector_id"] = source["connectors"][0]["id"]
        assert approve_source(client, ids["source_id"]).status_code == 200

        response = client.post(
            f"/api/v1/admin/sources/{ids['source_id']}/acquisition-runs",
            json={"connector_id": ids["connector_id"], "run_key": f"policy-change-{policy_change}"},
        )
        assert response.status_code == 502, response.text
        assert response.json()["detail"]["code"] == "SOURCE_RESTRICTED"
        assert network_calls["count"] == 1

        run = db.scalar(select(AcquisitionRun).where(
            AcquisitionRun.source_id == UUID(ids["source_id"]),
            AcquisitionRun.run_key == f"policy-change-{policy_change}",
        ))
        assert run is not None
        assert run.status == "RESTRICTED"
        assert run.error_category == expected_category
    finally:
        client.close()


def test_manual_acquisition_revalidates_new_rule_before_second_network_request(db, monkeypatch):
    from app.infrastructure.connectors.greenhouse import GreenhouseJobBoardConnector
    from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository

    client = make_client(db, make_user(db, admin=True))
    network_calls = {"count": 0}
    live_rule = {
        "id": "live-rule-after-first-request",
        "rule_type": "HOST_EXACT",
        "pattern": "boards-api.greenhouse.io",
        "connector_scope": "GREENHOUSE_JOB_BOARD",
        "reason": "Rule activated during this test",
        "active": True,
    }

    def current_rules(_repository):
        return [live_rule] if network_calls["count"] >= 1 else []

    def fake_get_json(_connector, _client, _url, *, params=None):
        network_calls["count"] += 1
        if network_calls["count"] == 1:
            return {"name": "Example Board"}
        raise AssertionError("the second request must be blocked before network execution")

    monkeypatch.setattr(
        SqlAlchemyAcquisitionRepository, "list_active_forbidden_target_rules", current_rules
    )
    monkeypatch.setattr(GreenhouseJobBoardConnector, "_get_json", fake_get_json)

    try:
        created = client.post(
            "/api/v1/admin/sources",
            json={"name": "Live policy regression", "board_token": "live-policy-board"},
        )
        assert created.status_code == 201, created.text
        source = created.json()["data"]
        source_id = source["id"]
        connector_id = source["connectors"][0]["id"]
        assert approve_source(client, source_id).status_code == 200

        response = client.post(
            f"/api/v1/admin/sources/{source_id}/acquisition-runs",
            json={"connector_id": connector_id, "run_key": "live-rule-mid-run"},
        )
        assert response.status_code == 502, response.text
        assert response.json()["detail"]["code"] == "SOURCE_RESTRICTED"
        assert network_calls["count"] == 1

        run = db.scalar(select(AcquisitionRun).where(
            AcquisitionRun.source_id == UUID(source_id),
            AcquisitionRun.run_key == "live-rule-mid-run",
        ))
        assert run is not None
        assert run.status == "RESTRICTED"
    finally:
        client.close()


@pytest.mark.parametrize(
    "policy_change,next_url,expected_category",
    [
        ("forbidden_rule", "https://blocked.example.test/jobs/2", "FORBIDDEN_TARGET_RULE_MATCHED"),
        ("permission_revoked", "https://public.example.test/jobs/2", "PERMISSION_REVIEW_REQUIRED"),
        ("source_configuration", "https://public.example.test/jobs/2", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("source_base_url", "https://public.example.test/jobs/2", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("source_type", "https://public.example.test/jobs/2", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("permission_review_reference", "https://public.example.test/jobs/2", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("permission_reviewed_at", "https://public.example.test/jobs/2", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("connector_configuration", "https://public.example.test/jobs/2", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("connector_capabilities", "https://public.example.test/jobs/2", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("connector_limits", "https://public.example.test/jobs/2", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("active_configuration_version", "https://public.example.test/jobs/2", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("connector_type", "https://public.example.test/jobs/2", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("connector_version", "https://public.example.test/jobs/2", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("source_deactivated", "https://public.example.test/jobs/2", "SOURCE_POLICY_CHANGED"),
        ("connector_deactivated", "https://public.example.test/jobs/2", "CONNECTOR_POLICY_CHANGED"),
    ],
)
def test_sql_policy_guard_reloads_changes_before_next_request(
    db, policy_change, next_url, expected_category
):
    from app.application.jobs.registered_acquisition import build_acquisition_policy_revalidator
    from app.infrastructure.db.models.target_policy import ForbiddenTargetRule
    from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
    from app.ports.acquisition import AcquisitionAdapterError

    user = make_user(db, admin=True)
    source = Source(
        name=f"Live policy test {uuid4()}",
        source_type="JOB_BOARD",
        base_url="https://public.example.test",
        status="ACTIVE",
        permission_review_status="APPROVED",
        permission_review_reference="terms review accepted",
        permission_reviewed_at=datetime.now(timezone.utc),
        operational_state="AVAILABLE",
        configuration={},
    )
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id,
        connector_type="SCRAPY",
        connector_version="1.0.0",
        status="ACTIVE",
        configuration={},
        active_configuration_version=1,
    )
    db.add(connector)
    db.flush()

    repository = SqlAlchemyAcquisitionRepository(db)
    guard = build_acquisition_policy_revalidator(
        repository, source.id, connector.id, source, connector
    )
    requests_sent = 0
    guard(url="https://public.example.test/jobs/1", connector_type="SCRAPY", method="GET")
    requests_sent += 1

    if policy_change == "forbidden_rule":
        db.add(ForbiddenTargetRule(
            rule_type="HOST_EXACT",
            pattern="blocked.example.test",
            connector_scope="SCRAPY",
            reason="Added while acquisition was running",
            active=True,
            created_by_user_id=user.id,
        ))
    elif policy_change == "permission_revoked":
        source.permission_review_status = "REJECTED"
    elif policy_change == "source_configuration":
        source.configuration = {"changed": True}
    elif policy_change == "source_base_url":
        source.base_url = "https://changed.example.test"
    elif policy_change == "source_type":
        source.source_type = "CHANGED_SOURCE_TYPE"
    elif policy_change == "permission_review_reference":
        source.permission_review_reference = "terms review changed"
    elif policy_change == "permission_reviewed_at":
        source.permission_reviewed_at = datetime.now(timezone.utc) + timedelta(seconds=1)
    elif policy_change == "connector_configuration":
        connector.configuration = {"start_urls": ["https://other.example.test/jobs"]}
    elif policy_change == "connector_capabilities":
        connector.capabilities = {"supports_pagination": False}
    elif policy_change == "connector_limits":
        connector.limits = {"max_pages": 1}
    elif policy_change == "active_configuration_version":
        connector.active_configuration_version = 2
    elif policy_change == "connector_type":
        connector.connector_type = "JSON_API"
    elif policy_change == "connector_version":
        connector.connector_version = "2.0.0"
    elif policy_change == "source_deactivated":
        source.status = "INACTIVE"
    elif policy_change == "connector_deactivated":
        connector.status = "INACTIVE"
    db.flush()

    with pytest.raises(AcquisitionAdapterError) as exc:
        guard(url=next_url, connector_type="SCRAPY", method="GET")
    assert exc.value.error_category == expected_category
    assert requests_sent == 1
