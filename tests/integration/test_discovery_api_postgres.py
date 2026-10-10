"""Authenticated API integration tests for candidate discovery settings and missions."""
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.infrastructure.db.session import get_db
from app.infrastructure.db.models import CandidateDiscoverySettings, DiscoveryMission, User, WorkItem
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
        assert defaults["revision"] == 1

        updated = client.put(
            "/api/v1/candidate/discovery/settings",
            json=_settings_payload(),
        )
        assert updated.status_code == 200
        settings = updated.json()["data"]
        assert settings["status"] == "ENABLED"
        assert settings["target_opportunities"] == 10
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
