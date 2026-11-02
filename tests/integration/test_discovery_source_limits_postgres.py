"""E4.4/E4.6: per-source run-rate limit is admin-configurable, enforced before acquisition, and visible."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.api.deps import get_principal
from app.application.jobs.discovery_missions import create_discovery_mission
from app.application.jobs.source_limits import source_run_limit_reached
from app.infrastructure.db.models import AcquisitionRun, DiscoveryMissionSourceRun, User
from app.infrastructure.db.models.discovery_source_limit import DiscoverySourceLimit
from app.infrastructure.db.models.governance import AdminAuditEvent, AdminGrant
from app.infrastructure.db.session import get_db
from app.main import app
from tests.integration.test_admin_governance_postgres import _admin_test_client, db  # noqa: F401
from tests.integration.test_discovery_missions_postgres import _candidate, _seed_active_sources

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


def _admin(db):
    admin = User(display_name="limits admin")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()
    return admin


def _call(db, principal_id, method, source_id, **kwargs):
    client = _admin_test_client(db, principal_id)
    try:
        return getattr(client, method)(f"/api/v1/admin/discovery/sources/{source_id}/limit", **kwargs)
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)


def _source(db):
    user, profile = _candidate(db)
    source, connector, target = _seed_active_sources(db, 1, created_by_user_id=user.id)[0]
    return user, profile, source, connector


def _runs(db, source, connector, count, *, age=timedelta(minutes=1)):
    for _ in range(count):
        db.add(AcquisitionRun(
            source_id=source.id, source_connector_id=connector.id, run_key=f"lim-{uuid4()}",
            status="SUCCEEDED", created_at=datetime.now(timezone.utc) - age,
        ))
    db.flush()


def test_non_admin_is_denied(db):  # noqa: F811
    user, _p, source, _c = _source(db)
    assert _call(db, user.id, "get", source.id).status_code in {401, 403}
    assert _call(db, user.id, "put", source.id, json={"max_runs": 1, "window_minutes": 60, "reason": "x" * 10}).status_code in {401, 403}


def test_unconfigured_limit_is_reported_as_such_and_validation_is_bounded(db):  # noqa: F811
    admin = _admin(db)
    _u, _p, source, _c = _source(db)
    body = _call(db, admin.id, "get", source.id).json()["data"]
    assert body["configured"] is False and body["max_runs"] is None
    ok = {"max_runs": 3, "window_minutes": 60, "reason": "provider rate guidance"}
    for bad in ({**ok, "max_runs": 0}, {**ok, "max_runs": 1001}, {**ok, "window_minutes": 0},
                {**ok, "window_minutes": 10081}, {**ok, "reason": "short"}, {"max_runs": 3, "window_minutes": 60}):
        assert _call(db, admin.id, "put", source.id, json=bad).status_code == 422
    assert _call(db, admin.id, "put", uuid4(), json=ok).status_code == 404


def test_set_update_and_remove_are_audited(db):  # noqa: F811
    admin = _admin(db)
    _u, _p, source, _c = _source(db)
    first = _call(db, admin.id, "put", source.id, json={"max_runs": 3, "window_minutes": 60, "reason": "provider rate guidance"})
    assert first.status_code == 200, first.text
    assert first.json()["data"]["max_runs"] == 3 and first.json()["data"]["configured"] is True
    second = _call(db, admin.id, "put", source.id, json={"max_runs": 5, "window_minutes": 120, "reason": "relaxed after review"})
    assert second.json()["data"]["max_runs"] == 5
    removed = _call(db, admin.id, "delete", source.id, params={"reason": "provider lifted the limit"})
    assert removed.status_code == 200 and removed.json()["data"]["configured"] is False
    assert _call(db, admin.id, "delete", source.id, params={"reason": "nothing to remove here"}).status_code == 404
    events = db.scalars(select(AdminAuditEvent).where(
        AdminAuditEvent.resource_id == source.id,
        AdminAuditEvent.action.in_(["DISCOVERY_SOURCE_LIMIT_SET", "DISCOVERY_SOURCE_LIMIT_REMOVED"]),
    ).order_by(AdminAuditEvent.created_at)).all()
    assert [e.action for e in events] == ["DISCOVERY_SOURCE_LIMIT_SET", "DISCOVERY_SOURCE_LIMIT_SET", "DISCOVERY_SOURCE_LIMIT_REMOVED"]
    assert events[1].details["previous"] == {"max_runs": 3, "window_minutes": 60}
    assert events[0].details["reason"] == "provider rate guidance"


def test_limit_counts_only_runs_inside_the_window(db):  # noqa: F811
    user, _p, source, connector = _source(db)
    db.add(DiscoverySourceLimit(source_id=source.id, max_runs=2, window_minutes=60, updated_by_user_id=user.id))
    db.flush()
    assert source_run_limit_reached(db, source.id) is None
    _runs(db, source, connector, 5, age=timedelta(hours=3))  # outside the window
    assert source_run_limit_reached(db, source.id) is None
    _runs(db, source, connector, 1)
    assert source_run_limit_reached(db, source.id) is None
    _runs(db, source, connector, 1)
    reached = source_run_limit_reached(db, source.id)
    assert reached == {"max_runs": 2, "window_minutes": 60, "runs_in_window": 2}


def test_mission_skips_a_rate_limited_source_without_starting_acquisition(db, monkeypatch):  # noqa: F811
    from app.workers import discovery_mission_worker as mission_worker

    user, profile, source, connector = _source(db)
    mission = create_discovery_mission(db, candidate_profile_id=profile.id, idempotency_key=f"limit-{uuid4()}")
    db.add(DiscoverySourceLimit(source_id=source.id, max_runs=1, window_minutes=60, updated_by_user_id=user.id))
    _runs(db, source, connector, 1)

    class ForbiddenNetwork:
        def __init__(self, *_a, **_k):
            raise AssertionError("a rate-limited source must not start acquisition")

    monkeypatch.setattr(mission_worker, "RegisteredAcquisitionService", ForbiddenNetwork)
    mission_worker.process_mission_step(db, mission.id)
    row = db.scalar(select(DiscoveryMissionSourceRun).where(
        DiscoveryMissionSourceRun.mission_id == mission.id,
        DiscoveryMissionSourceRun.source_id == source.id,
    ))
    assert (row.status, row.error_code) == ("UNAVAILABLE", "SOURCE_RATE_LIMIT_REACHED")
    assert row.error_details["scope_outcome"] == "SKIPPED"
    assert row.error_details["max_runs"] == 1 and row.error_details["window_minutes"] == 60
    assert db.scalar(select(func.count()).select_from(AcquisitionRun).where(
        AcquisitionRun.source_id == source.id, AcquisitionRun.created_at >= datetime.now(timezone.utc) - timedelta(minutes=30),
    )) == 1  # only the seeded run; none was started
