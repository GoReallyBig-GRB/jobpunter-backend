"""Authenticated API integration tests for candidate discovery settings and missions."""
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.infrastructure.db.session import get_db
from app.infrastructure.db.models import (
    AcquisitionRun,
    CandidateDiscoverySettings,
    DiscoveryMission,
    Source,
    SourceConnector,
    User,
    WorkItem,
)
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
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def _user(db, label):
    user = User(display_name=f"{label} {uuid4()}")
    db.add(user)
    db.flush()
    return user


def _client_for(db, user):
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=user.id, auth_provider="test"
    )
    connection = db.connection()

    def override_db():
        request_session = Session(
            bind=connection, join_transaction_mode="create_savepoint"
        )
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _settings_payload(**overrides):
    payload = {
        "status": "ENABLED",
        "cadence_minutes": 60,
        "target_opportunities": 10,
        "max_job_age_days": 14,
        "verification_required": True,
        "evaluation_depth": "preliminary",
        "source_filters": {"source_types": ["EMPLOYER"]},
        "expected_revision": 1,
    }
    payload.update(overrides)
    return payload


def test_settings_api_defaults_revision_checks_and_validation(db):
    owner = _user(db, "Discovery API owner")
    client = _client_for(db, owner)
    try:
        response = client.get("/api/v1/candidate/discovery/settings")
        assert response.status_code == 200
        defaults = response.json()["data"]
        assert defaults["status"] == "DISABLED"
        assert defaults["target_opportunities"] == 20
        assert defaults["max_job_age_days"] == 21
        assert defaults["schedule_type"] == "INTERVAL"
        assert defaults["schedule_time"] == "09:00"
        assert defaults["schedule_timezone"] == "UTC"
        assert defaults["schedule_weekday"] is None
        assert defaults["revision"] == 1

        updated = client.put(
            "/api/v1/candidate/discovery/settings",
            json=_settings_payload(
                schedule_type="WEEKLY",
                schedule_time="09:30",
                schedule_timezone="Africa/Lagos",
                schedule_weekday=0,
            ),
        )
        assert updated.status_code == 200
        settings = updated.json()["data"]
        assert settings["status"] == "ENABLED"
        assert settings["target_opportunities"] == 10
        assert settings["schedule_type"] == "WEEKLY"
        assert settings["schedule_time"] == "09:30"
        assert settings["schedule_timezone"] == "Africa/Lagos"
        assert settings["schedule_weekday"] == 0
        assert settings["revision"] == 2
        assert settings["next_run_at"] is not None

        stale = client.put(
            "/api/v1/candidate/discovery/settings",
            json=_settings_payload(expected_revision=1, target_opportunities=5),
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"

        invalid = client.put(
            "/api/v1/candidate/discovery/settings",
            json=_settings_payload(expected_revision=2, cadence_minutes=5),
        )
        assert invalid.status_code == 422

        missing_weekday = client.put(
            "/api/v1/candidate/discovery/settings",
            json=_settings_payload(
                expected_revision=2,
                schedule_type="WEEKLY",
                schedule_weekday=None,
            ),
        )
        assert missing_weekday.status_code == 422

        candidate_context = client.put(
            "/api/v1/candidate/discovery/settings",
            json=_settings_payload(
                expected_revision=2,
                source_filters={"acquisition_context": {"board_token": "candidate-secret"}},
            ),
        )
        assert candidate_context.status_code == 422
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize(
    ("outcomes", "expected_status"),
    [
        (["fail", "fail"], "PENDING"),
        (["success", "fail"], "WAITING_FOR_CANONICALIZATION"),
    ],
)
def test_mission_api_reports_non_successful_source_outcomes_without_empty_success(
    db, monkeypatch, outcomes, expected_status
):
    from app.workers import discovery_mission_worker as mission_worker

    owner = _user(db, "Discovery failure API owner")
    client = _client_for(db, owner)
    try:
        created = client.post(
            "/api/v1/candidate/discovery/missions",
            json={"idempotency_key": f"api-source-outcomes-{uuid4()}"},
        )
        assert created.status_code == 201
        mission_id = UUID(created.json()["data"]["id"])

        now = datetime.now(timezone.utc)
        sources = []
        source_outcomes = {}
        for index in range(len(outcomes)):
            source = Source(
                name=f"API outcome source {uuid4()}",
                source_type="EMPLOYER",
                status="ACTIVE",
                permission_review_status="APPROVED",
                permission_review_reference="test-policy-review",
                permission_reviewed_at=now,
            )
            db.add(source)
            db.flush()
            connector = SourceConnector(
                source_id=source.id,
                connector_type="TEST",
                connector_version="1",
                status="ACTIVE",
            )
            db.add(connector)
            db.flush()
            sources.append((source, connector))
            source_outcomes[source.id] = outcomes[index]

        monkeypatch.setattr(
            mission_worker, "_eligible_sources", lambda _db, _wave, _filters: sources
        )

        class ControlledAcquisition:
            def __init__(self, repository, registry=None):
                self.db = repository.db

            def run(self, source_id, connector_id, run_key, _context):
                outcome = source_outcomes[source_id]
                if outcome == "fail":
                    raise RuntimeError("connector unavailable")
                run = AcquisitionRun(
                    source_id=source_id,
                    source_connector_id=connector_id,
                    run_key=run_key,
                    status="SUCCEEDED",
                    started_at=now,
                    completed_at=now,
                    metrics={"acquired_count": 1, "persisted_count": 1},
                )
                self.db.add(run)
                self.db.flush()
                return SimpleNamespace(
                    run_id=run.id,
                    status="SUCCEEDED",
                    acquired_count=1,
                    persisted_count=1,
                    error_category=None,
                    failed_item_count=0,
                    retry_after_seconds=None,
                )

        monkeypatch.setattr(
            mission_worker, "RegisteredAcquisitionService", ControlledAcquisition
        )
        processed_status = mission_worker.process_mission_step(db, mission_id)
        assert processed_status == expected_status

        listed = client.get("/api/v1/candidate/discovery/missions")
        assert listed.status_code == 200
        api_mission = next(
            item for item in listed.json()["data"] if item["id"] == str(mission_id)
        )
        assert api_mission["status"] == expected_status
        assert api_mission["status"] not in {"SUCCEEDED", "SUCCESS", "EMPTY_SUCCESS"}
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)


def test_manual_mission_api_is_idempotent_and_candidate_scoped(db):
    owner = _user(db, "Discovery mission owner")
    other = _user(db, "Other discovery candidate")
    client = _client_for(db, owner)
    try:
        first = client.post(
            "/api/v1/candidate/discovery/missions",
            json={"idempotency_key": "api-manual-mission-1"},
        )
        replay = client.post(
            "/api/v1/candidate/discovery/missions",
            json={"idempotency_key": "api-manual-mission-1"},
        )
        assert first.status_code == 201
        assert replay.status_code == 201
        mission_id = first.json()["data"]["id"]
        assert replay.json()["data"]["id"] == mission_id

        listed = client.get("/api/v1/candidate/discovery/missions")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["data"]] == [mission_id]

        persisted = db.scalar(select(DiscoveryMission).where(
            DiscoveryMission.id == mission_id
        ))
        assert persisted is not None
        work_items = db.scalars(select(WorkItem).where(
            WorkItem.work_type == "DISCOVERY_MISSION",
            WorkItem.resource_id == persisted.id,
        )).all()
        assert len(work_items) == 1

        app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
            user_id=other.id, auth_provider="test"
        )
        foreign_list = client.get("/api/v1/candidate/discovery/missions")
        assert foreign_list.status_code == 200
        assert foreign_list.json()["data"] == []
        foreign_settings = client.get("/api/v1/candidate/discovery/settings")
        assert foreign_settings.status_code == 200
        assert foreign_settings.json()["data"]["candidate_profile_id"] != (
            first.json()["data"]["candidate_profile_id"]
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
