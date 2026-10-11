import os
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.api.v1 import admin as admin_api
from app.infrastructure.db.models import AdminAuditEvent, AdminGrant, Source, SourceConnector, User
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

    def get(self, url, *, params=None):
        self.calls.append((url, params))
        return self.responses.pop(0)


def greenhouse_response(payload, status_code=200):
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("GET", "https://boards-api.greenhouse.io/test"),
    )


def test_admin_can_create_list_and_pause_source_without_exposing_board_token(db):
    user = make_user(db, admin=True)
    client = make_client(db, user)
    try:
        response = client.post(
            "/api/v1/admin/sources",
            headers={"x-request-id": "source-registry-create"},
            json={"name": "Example board", "board_token": "example-board"},
        )
        assert response.status_code == 201, response.text
        data = response.json()["data"]
        assert data["name"] == "Example board"
        assert data["source_type"] == "GREENHOUSE_JOB_BOARD"
        assert data["status"] == "ACTIVE"
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
        assert db.scalar(select(Source).where(Source.name == "Forbidden board")) is None
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
            super().__init__(http_client=fake_http)

    monkeypatch.setattr(admin_api, "GreenhouseJobBoardConnector", InjectedGreenhouseConnector)
    try:
        created = client.post(
            "/api/v1/admin/sources",
            json={"name": "Health board", "board_token": "health-board"},
        )
        assert created.status_code == 201, created.text
        source = created.json()["data"]
        connector_id = source["connectors"][0]["id"]
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
            super().__init__(http_client=fake_http)

    monkeypatch.setattr(admin_api, "GreenhouseJobBoardConnector", InjectedGreenhouseConnector)
    try:
        created = client.post(
            "/api/v1/admin/sources",
            json={"name": "Broken health board", "board_token": "broken-board"},
        )
        assert created.status_code == 201, created.text
        source = created.json()["data"]
        connector_id = source["connectors"][0]["id"]
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
