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
        assert result["duplicate_count"] == 0
        assert result["downstream_processing"] == {
            "tracked_count": 1,
            "pending_count": 1,
            "processing_count": 0,
            "processed_count": 0,
            "failed_count": 0,
            "other_count": 0,
        }

        db.expire_all()
        observed_source = db.get(Source, source.id)
        assert observed_source.last_observed_at is not None
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

        detail_url = (
            f"/api/v1/admin/sources/{source.id}/acquisition-runs/{result['run_id']}"
        )
        detail = client.get(detail_url)
        assert detail.status_code == 200, detail.text
        detail_data = detail.json()["data"]
        assert detail_data["status"] == "SUCCEEDED"
        assert detail_data["acquired_count"] == 1
        assert detail_data["persisted_count"] == 1
        assert detail_data["duplicate_count"] == 0
        assert detail_data["downstream_processing"]["pending_count"] == 1

        wrong_source = client.get(
            f"/api/v1/admin/sources/{uuid4()}/acquisition-runs/{result['run_id']}"
        )
        assert wrong_source.status_code == 404

        outbox_events[0].status = "PROCESSED"
        db.commit()
        refreshed = client.get(detail_url)
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["data"]["downstream_processing"]["processed_count"] == 1
        assert refreshed.json()["data"]["downstream_processing"]["pending_count"] == 0

        outbox_events[0].status = "DEAD"
        db.commit()
        failed_downstream = client.get(detail_url)
        assert failed_downstream.status_code == 200, failed_downstream.text
        assert failed_downstream.json()["data"]["downstream_processing"]["failed_count"] == 1
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



def test_new_run_reports_duplicate_records_separately_from_persisted_records(db, monkeypatch):
    user, source, connector = seed_source(db)
    client = make_client(db, user)
    job = {
        "id": 8770,
        "title": "Product Manager",
        "absolute_url": "https://boards.greenhouse.io/example/jobs/8770",
        "location": {"name": "Remote"},
        "content": "<p>Manage product delivery.</p>",
    }
    fake_http = FakeGreenhouseHTTPClient([
        greenhouse_response({"name": "Example Company"}),
        greenhouse_response({"jobs": [job]}),
        greenhouse_response({"name": "Example Company"}),
        greenhouse_response({"jobs": [job]}),
    ])
    original_connector = admin_api.GreenhouseJobBoardConnector

    class InjectedGreenhouseConnector(original_connector):
        def __init__(self):
            super().__init__(http_client=fake_http)

    monkeypatch.setattr(admin_api, "GreenhouseJobBoardConnector", InjectedGreenhouseConnector)
    try:
        url = f"/api/v1/admin/sources/{source.id}/acquisition-runs"
        first = client.post(url, json={
            "connector_id": str(connector.id),
            "run_key": "greenhouse-dedupe-first",
        })
        second = client.post(url, json={
            "connector_id": str(connector.id),
            "run_key": "greenhouse-dedupe-second",
        })

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        first_result = first.json()["data"]
        second_result = second.json()["data"]
        assert (first_result["acquired_count"], first_result["persisted_count"], first_result["duplicate_count"]) == (1, 1, 0)
        assert (second_result["acquired_count"], second_result["persisted_count"], second_result["duplicate_count"]) == (1, 0, 1)
        assert second_result["downstream_processing"]["tracked_count"] == 0

        db.expire_all()
        records = db.scalars(select(RawSourceRecord).where(
            RawSourceRecord.source_id == source.id,
        )).all()
        assert len(records) == 1
        events = db.scalars(select(OutboxEvent).where(
            OutboxEvent.event_type == "RAW_SOURCE_RECORD_ACQUIRED",
            OutboxEvent.aggregate_id == records[0].id,
        )).all()
        assert len(events) == 1
        observed_source = db.get(Source, source.id)
        assert observed_source.last_observed_at is not None
    finally:
        client.close()


def test_repeated_greenhouse_run_key_replays_without_refetching_or_duplicate_records(db, monkeypatch):
    user, source, connector = seed_source(db)
    client = make_client(db, user)
    fake_http = FakeGreenhouseHTTPClient([
        greenhouse_response({"name": "Example Company"}),
        greenhouse_response({
            "jobs": [{
                "id": 8766,
                "title": "Product Manager",
                "absolute_url": "https://boards.greenhouse.io/example/jobs/8766",
                "location": {"name": "Remote"},
                "content": "<p>Manage product delivery.</p>",
            }]
        }),
    ])
    original_connector = admin_api.GreenhouseJobBoardConnector

    class InjectedGreenhouseConnector(original_connector):
        def __init__(self):
            super().__init__(http_client=fake_http)

    monkeypatch.setattr(admin_api, "GreenhouseJobBoardConnector", InjectedGreenhouseConnector)
    try:
        url = f"/api/v1/admin/sources/{source.id}/acquisition-runs"
        body = {"connector_id": str(connector.id), "run_key": "greenhouse-idempotent-run"}
        first = client.post(url, json=body)
        second = client.post(url, json=body)

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        first_result = first.json()["data"]
        second_result = second.json()["data"]
        assert first_result["run_id"] == second_result["run_id"]
        assert first_result["status"] == second_result["status"] == "SUCCEEDED"
        assert len(fake_http.calls) == 2, "a replay must not call the upstream board again"

        db.expire_all()
        records = db.scalars(select(RawSourceRecord).where(
            RawSourceRecord.source_id == source.id,
        )).all()
        assert len(records) == 1
        events = db.scalars(select(OutboxEvent).where(
            OutboxEvent.event_type == "RAW_SOURCE_RECORD_ACQUIRED",
            OutboxEvent.aggregate_id == records[0].id,
        )).all()
        assert len(events) == 1
        audits = db.scalars(select(AdminAuditEvent).where(
            AdminAuditEvent.resource_id == first_result["run_id"],
        )).all()
        assert [row.action for row in audits].count("SOURCE_ACQUISITION_COMPLETED") == 1
        assert [row.action for row in audits].count("SOURCE_ACQUISITION_REPLAYED") == 1
    finally:
        client.close()


def test_greenhouse_upstream_failure_persists_failed_run_and_sanitized_audit(db, monkeypatch):
    user, source, connector = seed_source(db)
    client = make_client(db, user)
    fake_http = FakeGreenhouseHTTPClient([
        greenhouse_response({"error": "sensitive upstream body"}, status_code=503),
    ])
    original_connector = admin_api.GreenhouseJobBoardConnector

    class InjectedGreenhouseConnector(original_connector):
        def __init__(self):
            super().__init__(http_client=fake_http)

    monkeypatch.setattr(admin_api, "GreenhouseJobBoardConnector", InjectedGreenhouseConnector)
    try:
        response = client.post(
            f"/api/v1/admin/sources/{source.id}/acquisition-runs",
            headers={"x-request-id": "greenhouse-failure-1"},
            json={"connector_id": str(connector.id), "run_key": "greenhouse-failure-run"},
        )
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "SOURCE_ACQUISITION_FAILED"
        assert "sensitive upstream body" not in response.text

        db.expire_all()
        run = db.scalar(select(AcquisitionRun).where(
            AcquisitionRun.source_id == source.id,
            AcquisitionRun.run_key == "greenhouse-failure-run",
        ))
        assert run is not None
        assert run.status == "FAILED"
        assert run.error_summary == "connector_error:GreenhouseConnectorError"
        observed_source = db.get(Source, source.id)
        assert observed_source.last_observed_at is not None
        assert db.scalar(select(RawSourceRecord).where(
            RawSourceRecord.source_id == source.id,
        )) is None
        audit = db.scalar(select(AdminAuditEvent).where(
            AdminAuditEvent.action == "SOURCE_ACQUISITION_FAILED",
            AdminAuditEvent.resource_id == run.id,
        ))
        assert audit is not None
        assert audit.outcome == "FAILED"
        assert audit.details["error_code"] == "greenhouse_request_failed"
        assert audit.request_id == "greenhouse-failure-1"
    finally:
        client.close()

def test_acquisition_processing_summary_counts_pending_processing_processed_and_dead_events(db):
    from datetime import datetime, timezone

    from app.api.v1.admin import _acquisition_processing_summary

    _user, source, connector = seed_source(db)
    now = datetime.now(timezone.utc)
    run = AcquisitionRun(
        source_id=source.id,
        source_connector_id=connector.id,
        run_key="processing-summary",
        status="SUCCEEDED",
        started_at=now,
        completed_at=now,
        metrics={"acquired_count": 5, "persisted_count": 5},
    )
    db.add(run)
    db.flush()

    for index, event_status in enumerate(("PENDING", "PROCESSING", "PROCESSED", "DEAD", "UNEXPECTED")):
        raw = RawSourceRecord(
            acquisition_run_id=run.id,
            source_id=source.id,
            external_record_id=f"processing-{index}",
            deduplication_key=f"id:processing-{index}",
            source_url=f"https://example.test/jobs/{index}",
            raw_content={"title": f"Job {index}"},
            content_hash=f"hash-{index}",
            status="ACQUIRED",
            acquired_at=now,
            created_at=now,
        )
        db.add(raw)
        db.flush()
        db.add(OutboxEvent(
            event_type="RAW_SOURCE_RECORD_ACQUIRED",
            aggregate_type="RawSourceRecord",
            aggregate_id=raw.id,
            payload={
                "raw_source_record_id": str(raw.id),
                "source_id": str(source.id),
                "acquisition_run_id": str(run.id),
            },
            status=event_status,
            attempt_count=1,
            available_at=now,
            created_at=now,
        ))
    db.flush()

    assert _acquisition_processing_summary(db, run.id) == {
        "tracked_count": 5,
        "pending_count": 1,
        "processing_count": 1,
        "processed_count": 1,
        "failed_count": 1,
        "other_count": 1,
    }

def test_non_admin_cannot_read_acquisition_run_status(db):
    user, source, _connector = seed_source(db, admin=False)
    client = make_client(db, user)
    try:
        response = client.get(
            f"/api/v1/admin/sources/{source.id}/acquisition-runs/{uuid4()}"
        )
        assert response.status_code == 403
        history = client.get(
            f"/api/v1/admin/sources/{source.id}/acquisition-runs"
        )
        assert history.status_code == 403
    finally:
        client.close()


def test_admin_acquisition_history_is_source_scoped_and_paginated(db):
    from datetime import datetime, timedelta, timezone

    admin_user, source, connector = seed_source(db)
    _other_user, other_source, _other_connector = seed_source(db, admin=False)
    now = datetime.now(timezone.utc)
    older = AcquisitionRun(
        source_id=source.id,
        source_connector_id=connector.id,
        run_key="history-older",
        status="SUCCEEDED",
        started_at=now - timedelta(days=1),
        completed_at=now - timedelta(days=1),
        metrics={"acquired_count": 3, "persisted_count": 2},
    )
    newer = AcquisitionRun(
        source_id=source.id,
        source_connector_id=connector.id,
        run_key="history-newer",
        status="FAILED",
        started_at=now,
        completed_at=now,
        metrics={"acquired_count": 1, "persisted_count": 0},
        error_summary="connector_error:TimeoutError",
    )
    db.add_all([older, newer])
    db.flush()
    client = make_client(db, admin_user)
    try:
        url = f"/api/v1/admin/sources/{source.id}/acquisition-runs"
        response = client.get(url)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["pagination"] == {"limit": 25, "offset": 0, "total": 2}
        assert [item["run_key"] for item in payload["data"]] == [
            "history-newer",
            "history-older",
        ]
        assert payload["data"][1]["duplicate_count"] == 1
        assert payload["data"][0]["error_summary"] == "connector_error:TimeoutError"

        page = client.get(f"{url}?limit=1&offset=1")
        assert page.status_code == 200, page.text
        assert page.json()["pagination"] == {"limit": 1, "offset": 1, "total": 2}
        assert [item["run_key"] for item in page.json()["data"]] == ["history-older"]

        other = client.get(
            f"/api/v1/admin/sources/{other_source.id}/acquisition-runs"
        )
        assert other.status_code == 200
        assert other.json()["data"] == []
        assert other.json()["pagination"]["total"] == 0

        missing = client.get(f"/api/v1/admin/sources/{uuid4()}/acquisition-runs")
        assert missing.status_code == 404
    finally:
        client.close()

