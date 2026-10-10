import os
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.api.v1 import admin as admin_api
from app.infrastructure.db.models import (
    AcquisitionRun,
    AdminAuditEvent,
    AdminGrant,
    OutboxEvent,
    RawSourceRecord,
    Source,
    SourceConnector,
    User,
)
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


class FakeGreenhouseHTTPClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def get(self, url, *, params=None):
        self.calls.append((url, params))
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def greenhouse_response(payload, status_code=200):
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("GET", "https://boards-api.greenhouse.io/test"),
    )


def seed_source(db, *, admin=True, board_token="example"):
    user = User(display_name=f"Acquisition admin {uuid4()}")
    db.add(user)
    db.flush()
    if admin:
        db.add(AdminGrant(user_id=user.id, role="ADMIN", active=True))
    source = Source(
        name=f"Greenhouse board {uuid4()}",
        source_type="GREENHOUSE_JOB_BOARD",
        base_url="https://boards-api.greenhouse.io",
        status="ACTIVE",
        configuration={},
    )
    db.add(source)
    db.flush()
    connector = SourceConnector(
        source_id=source.id,
        connector_type="GREENHOUSE_JOB_BOARD",
        connector_version="1",
        status="ACTIVE",
        configuration={"board_token": board_token},
        capabilities={"published_jobs": True},
        limits={"timeout_seconds": 15},
    )
    db.add(connector)
    db.flush()
    return user, source, connector


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


def test_admin_greenhouse_acquisition_persists_run_raw_record_outbox_and_audit(db, monkeypatch):
    user, source, connector = seed_source(db)
    client = make_client(db, user)
    fake_http = FakeGreenhouseHTTPClient([
        greenhouse_response({"name": "Example Company"}),
        greenhouse_response({
            "jobs": [{
                "id": 8765,
                "title": "Technical Product Manager",
                "absolute_url": "https://boards.greenhouse.io/example/jobs/8765",
                "updated_at": "2026-10-01T12:00:00Z",
                "location": {"name": "Remote - Nigeria"},
                "content": "<p>Lead technical product delivery.</p>",
                "departments": [{"id": 2, "name": "Product"}],
                "offices": [],
                "metadata": None,
            }]
        }),
    ])

    original_connector = admin_api.GreenhouseJobBoardConnector

    class InjectedGreenhouseConnector(original_connector):
        def __init__(self):
            super().__init__(http_client=fake_http)

    monkeypatch.setattr(admin_api, "GreenhouseJobBoardConnector", InjectedGreenhouseConnector)
    try:
        response = client.post(
            f"/api/v1/admin/sources/{source.id}/acquisition-runs",
            headers={"x-request-id": "greenhouse-acquire-1"},
            json={"connector_id": str(connector.id), "run_key": "greenhouse-run-1"},
        )

        assert response.status_code == 200, response.text
        result = response.json()["data"]
        assert result["status"] == "SUCCEEDED"
        assert result["acquired_count"] == 1
        assert result["persisted_count"] == 1

        db.expire_all()
        run = db.scalar(select(AcquisitionRun).where(
            AcquisitionRun.source_id == source.id,
            AcquisitionRun.run_key == "greenhouse-run-1",
        ))
        assert run is not None
        assert run.status == "SUCCEEDED"
        raw_records = db.scalars(select(RawSourceRecord).where(
            RawSourceRecord.source_id == source.id,
        )).all()
        assert len(raw_records) == 1
        assert raw_records[0].external_record_id == "8765"
        assert raw_records[0].raw_content["company"] == "Example Company"
        outbox_events = db.scalars(select(OutboxEvent).where(
            OutboxEvent.event_type == "RAW_SOURCE_RECORD_ACQUIRED",
            OutboxEvent.aggregate_id == raw_records[0].id,
        )).all()
        assert len(outbox_events) == 1
        audit = db.scalar(select(AdminAuditEvent).where(
            AdminAuditEvent.action == "SOURCE_ACQUISITION_COMPLETED",
            AdminAuditEvent.resource_id == run.id,
        ))
        assert audit is not None
        assert audit.actor_user_id == user.id
        assert audit.request_id == "greenhouse-acquire-1"
        assert fake_http.closed is False, "injected HTTP clients remain caller-owned"
    finally:
        client.close()


def test_non_admin_cannot_start_greenhouse_acquisition(db, monkeypatch):
    user, source, connector = seed_source(db, admin=False)
    client = make_client(db, user)

    class MustNotBeConstructed:
        connector_type = "GREENHOUSE_JOB_BOARD"

        def __init__(self):
            pytest.fail("connector must not run before administrator authorization")

    monkeypatch.setattr(admin_api, "GreenhouseJobBoardConnector", MustNotBeConstructed)
    try:
        response = client.post(
            f"/api/v1/admin/sources/{source.id}/acquisition-runs",
            json={"connector_id": str(connector.id), "run_key": "unauthorized-run"},
        )
        assert response.status_code == 403
        assert db.scalar(select(AcquisitionRun).where(
            AcquisitionRun.source_id == source.id,
            AcquisitionRun.run_key == "unauthorized-run",
        )) is None
    finally:
        client.close()


def test_invalid_greenhouse_board_token_is_rejected_before_run_creation(db):
    user, source, connector = seed_source(db, board_token="../private")
    client = make_client(db, user)
    try:
        response = client.post(
            f"/api/v1/admin/sources/{source.id}/acquisition-runs",
            json={"connector_id": str(connector.id), "run_key": "invalid-board-token"},
        )
        assert response.status_code == 422
        assert db.scalar(select(AcquisitionRun).where(
            AcquisitionRun.source_id == source.id,
            AcquisitionRun.run_key == "invalid-board-token",
        )) is None
    finally:
        client.close()
