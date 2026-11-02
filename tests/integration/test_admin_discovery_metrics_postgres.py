"""E5.5 (metrics): observed discovery metrics from persisted runs; absence of data is never green."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.api.deps import get_principal
from app.application.jobs.discovery_missions import create_discovery_mission
from app.infrastructure.db.models import AcquisitionRun, User
from app.infrastructure.db.models.governance import AdminGrant
from app.infrastructure.db.session import get_db
from app.main import app
from tests.integration.test_admin_governance_postgres import _admin_test_client, db  # noqa: F401
from tests.integration.test_discovery_missions_postgres import _candidate, _seed_active_sources

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)
URL = "/api/v1/admin/discovery/metrics"


def _admin(db):
    admin = User(display_name="metrics admin")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()
    return admin


def _get(db, principal_id, **params):
    client = _admin_test_client(db, principal_id)
    try:
        return client.get(URL, params=params)
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)


def _source(db):
    user, _profile = _candidate(db)
    return _seed_active_sources(db, 1, created_by_user_id=user.id)[0]


def _run(db, source, connector, status, completed_at, acquired=0):
    row = AcquisitionRun(
        source_id=source.id, source_connector_id=connector.id, run_key=f"m-{uuid4()}",
        status=status, completed_at=completed_at, metrics={"acquired": acquired},
    )
    db.add(row)
    db.flush()
    return row


def test_non_admin_is_denied(db):  # noqa: F811
    user = User(display_name="plain")
    db.add(user)
    db.flush()
    assert _get(db, user.id).status_code in {401, 403}


def test_source_without_any_success_is_reported_stale_not_healthy(db):  # noqa: F811
    admin = _admin(db)
    before = _get(db, admin.id).json()["data"]
    source, connector, _target = _source(db)
    data = _get(db, admin.id).json()["data"]
    assert data["stale_sources_total"] == before["stale_sources_total"] + 1
    assert data["basis"] == "PERSISTED_RUNS"
    # A source that has never succeeded has no last_success_at; it is never shown as healthy.
    assert all(s["last_success_at"] is None for s in data["stale_sources"] if s["source_id"] == str(source.id))


def test_recent_success_clears_staleness_and_old_success_does_not(db):  # noqa: F811
    admin = _admin(db)
    now = datetime.now(timezone.utc)
    before = _get(db, admin.id, stale_after_hours=72).json()["data"]["stale_sources_total"]
    fresh_source, fresh_conn, _t = _source(db)
    old_source, old_conn, _t2 = _source(db)
    # Both sources start stale (never succeeded); a recent success must clear only the fresh one.
    assert _get(db, admin.id, stale_after_hours=72).json()["data"]["stale_sources_total"] == before + 2
    _run(db, fresh_source, fresh_conn, "SUCCEEDED", now - timedelta(hours=1))
    _run(db, old_source, old_conn, "SUCCEEDED", now - timedelta(hours=200))
    data = _get(db, admin.id, stale_after_hours=72).json()["data"]
    assert data["stale_sources_total"] == before + 1
    assert str(fresh_source.id) not in {s["source_id"] for s in data["stale_sources"]}
    assert data["last_successful_acquisition_at"] is not None
    assert data["hours_since_last_successful_acquisition"] is not None
    assert _get(db, admin.id, stale_after_hours=300).json()["data"]["stale_sources_total"] == before


def test_tool_outcomes_count_failures_and_skips_separately_from_success(db):  # noqa: F811
    from app.workers import discovery_mission_worker as mission_worker

    admin = _admin(db)
    before = {t["tool_key"]: t for t in _get(db, admin.id).json()["data"]["by_tool"]}
    user, profile = _candidate(db)
    mission = create_discovery_mission(db, candidate_profile_id=profile.id, idempotency_key=f"metrics-{uuid4()}")
    source, connector, target = _seed_active_sources(db, 1, created_by_user_id=user.id)[0]
    target.status = "PAUSED"
    db.flush()
    mission_worker.process_mission_step(db, mission.id)
    tool = connector.connector_type.strip().upper()
    after = {t["tool_key"]: t for t in _get(db, admin.id).json()["data"]["by_tool"]}
    assert after[tool]["steps"] - before[tool]["steps"] == 1
    assert after[tool]["skipped"] - before[tool]["skipped"] == 1
    assert after[tool]["succeeded"] == before[tool]["succeeded"]


def test_window_parameters_are_bounded(db):  # noqa: F811
    admin = _admin(db)
    assert _get(db, admin.id, window_hours=0).status_code == 422
    assert _get(db, admin.id, window_hours=721).status_code == 422
    assert _get(db, admin.id, stale_after_hours=0).status_code == 422
