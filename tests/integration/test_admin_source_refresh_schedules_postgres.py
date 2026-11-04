"""E4.1/E4.7: administrators manage source refresh schedules with validation, audit and honest history."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError

from app.api.deps import get_principal
from app.infrastructure.db.models import DiscoveryMissionSourceRun, User
from app.infrastructure.db.models.discovery_schedule import SourceRefreshSchedule
from app.infrastructure.db.models.discovery_schedule import SourceRefreshScheduleEvent
from app.infrastructure.db.models.governance import AdminAuditEvent, AdminGrant
from app.infrastructure.db.session import get_db
from app.main import app
from tests.integration.test_admin_governance_postgres import _admin_test_client, db  # noqa: F401
from tests.integration.test_discovery_missions_postgres import _seed_active_sources

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


def _admin(db):
    admin = User(display_name="schedule admin")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()
    return admin


def _call(db, principal_id, method, path, **kwargs):
    client = _admin_test_client(db, principal_id)
    try:
        return getattr(client, method)(f"/api/v1/admin{path}", **kwargs)
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)


def _source(db):
    user = User(display_name="schedule creator")
    db.add(user)
    db.flush()
    source, connector, _t = _seed_active_sources(db, 1, created_by_user_id=user.id)[0]
    return user, source, connector


def test_non_admin_is_denied(db):  # noqa: F811
    user, source, _c = _source(db)
    path = f"/discovery/sources/{source.id}/schedule"
    assert _call(db, user.id, "get", path).status_code == 403
    assert _call(db, user.id, "put", path, json={"interval_minutes": 60, "reason": "keep fresh daily"}).status_code == 403
    assert _call(db, user.id, "patch", path + "/status", json={"status": "PAUSED", "reason": "provider incident"}).status_code == 403


def test_set_schedule_validates_audits_and_updates_in_place(db):  # noqa: F811
    admin = _admin(db)
    _u, source, _c = _source(db)
    path = f"/discovery/sources/{source.id}/schedule"
    assert _call(db, admin.id, "get", path).json()["data"]["configured"] is False
    for bad in ({"interval_minutes": 14, "reason": "keep fresh daily"},
                {"interval_minutes": 10081, "reason": "keep fresh daily"},
                {"interval_minutes": 60, "reason": "short"},
                {"interval_minutes": 60, "reason": "keep fresh daily", "extra": 1}):
        assert _call(db, admin.id, "put", path, json=bad).status_code == 422
    before = datetime.now(timezone.utc)
    created = _call(db, admin.id, "put", path, json={"interval_minutes": 60, "reason": "keep fresh daily"})
    assert created.status_code == 200
    data = created.json()["data"]
    assert data["configured"] and data["status"] == "ACTIVE" and data["interval_minutes"] == 60
    assert datetime.fromisoformat(data["next_run_at"]) >= before + timedelta(minutes=59)
    changed = _call(db, admin.id, "put", path, json={"interval_minutes": 120, "reason": "reduce provider load"})
    assert changed.json()["data"]["schedule_id"] == data["schedule_id"]
    assert changed.json()["data"]["interval_minutes"] == 120
    audits = db.scalars(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "DISCOVERY_SOURCE_SCHEDULE_SET", AdminAuditEvent.resource_id == source.id,
    ).order_by(AdminAuditEvent.created_at)).all()
    assert len(audits) == 2 and audits[1].details["previous"]["interval_minutes"] == 60


def test_unknown_source_and_unconfigured_schedule_are_404(db):  # noqa: F811
    admin = _admin(db)
    _u, source, _c = _source(db)
    from uuid import uuid4
    assert _call(db, admin.id, "put", f"/discovery/sources/{uuid4()}/schedule",
                 json={"interval_minutes": 60, "reason": "keep fresh daily"}).status_code == 404
    assert _call(db, admin.id, "patch", f"/discovery/sources/{source.id}/schedule/status",
                 json={"status": "PAUSED", "reason": "provider incident"}).status_code == 404


def test_pause_and_resume_are_audited_and_resume_makes_it_due(db):  # noqa: F811
    admin = _admin(db)
    _u, source, _c = _source(db)
    path = f"/discovery/sources/{source.id}/schedule"
    _call(db, admin.id, "put", path, json={"interval_minutes": 600, "reason": "keep fresh daily"})
    paused = _call(db, admin.id, "patch", path + "/status", json={"status": "PAUSED", "reason": "provider incident"})
    assert paused.json()["data"]["status"] == "PAUSED"
    resumed = _call(db, admin.id, "patch", path + "/status", json={"status": "ACTIVE", "reason": "provider recovered"})
    assert resumed.json()["data"]["status"] == "ACTIVE"
    assert datetime.fromisoformat(resumed.json()["data"]["next_run_at"]) <= datetime.now(timezone.utc)
    assert _call(db, admin.id, "patch", path + "/status", json={"status": "BOGUS", "reason": "provider recovered"}).status_code == 422
    actions = [a.action for a in db.scalars(select(AdminAuditEvent).where(AdminAuditEvent.resource_id == source.id))]
    assert actions.count("DISCOVERY_SOURCE_SCHEDULE_STATUS_CHANGED") == 2


def test_source_runs_list_shows_schedule_owned_steps_with_null_mission(db):  # noqa: F811
    admin = _admin(db)
    user, source, connector = _source(db)
    schedule = SourceRefreshSchedule(
        source_id=source.id, status="ACTIVE", interval_minutes=60,
        next_run_at=datetime.now(timezone.utc) + timedelta(hours=1),
        created_by_user_id=user.id, updated_by_user_id=user.id,
    )
    db.add(schedule)
    db.flush()
    now = datetime.now(timezone.utc)
    db.add(DiscoveryMissionSourceRun(
        schedule_id=schedule.id, source_id=source.id, source_connector_id=connector.id,
        wave_key="scheduled", attempt_number=1, run_key=f"schedule:test:{schedule.id}",
        status="UNAVAILABLE", error_code="DISCOVERY_TOOL_DISABLED",
        error_details={"scope_outcome": "SKIPPED", "tool_key": "JSON_API"}, created_at=now,
    ))
    db.flush()
    body = _call(db, admin.id, "get", f"/discovery/source-runs?schedule_id={schedule.id}").json()
    assert body["pagination"]["total"] == 1
    row = body["data"][0]
    assert row["trigger"] == "SCHEDULE" and row["mission_id"] is None and row["schedule_id"] == str(schedule.id)
    assert row["scope_outcome"] == "SKIPPED"


def test_window_validation_and_round_trip(db):  # noqa: F811
    admin = _admin(db)
    _u, source, _c = _source(db)
    path = f"/discovery/sources/{source.id}/schedule"
    base = {"interval_minutes": 60, "reason": "off-peak refresh only"}
    for bad in ({"window_start": "22:00"},  # partial window
                {"window_start": "22:00", "window_end": "02:00"},
                {"window_start": "22:00", "window_end": "22:00", "window_timezone": "UTC"},
                {"window_start": "25:00", "window_end": "02:00", "window_timezone": "UTC"},
                {"window_start": "22:00", "window_end": "02:00", "window_timezone": "Not/AZone"},
                {"window_start": "9:00", "window_end": "02:00", "window_timezone": "UTC"}):
        assert _call(db, admin.id, "put", path, json={**base, **bad}).status_code == 422, bad
    ok = _call(db, admin.id, "put", path, json={**base, "window_start": "22:00", "window_end": "02:00", "window_timezone": "UTC"})
    assert ok.status_code == 200
    data = ok.json()["data"]
    assert (data["window_start"], data["window_end"], data["window_timezone"]) == ("22:00", "02:00", "UTC")
    nxt = datetime.fromisoformat(data["next_run_at"])
    assert nxt.hour in {22, 23, 0, 1}  # the first run lands inside the window
    cleared = _call(db, admin.id, "put", path, json=base).json()["data"]
    assert cleared["window_start"] is None and cleared["window_timezone"] is None


def test_history_is_append_only_ordered_and_records_before_after_and_actor(db):  # noqa: F811
    admin = _admin(db)
    _u, source, _c = _source(db)
    path = f"/discovery/sources/{source.id}/schedule"
    assert _call(db, admin.id, "get", path + "/history").json()["data"] == []
    _call(db, admin.id, "put", path, json={"interval_minutes": 60, "reason": "initial cadence"})
    _call(db, admin.id, "put", path, json={"interval_minutes": 120, "reason": "reduce provider load"})
    _call(db, admin.id, "patch", path + "/status", json={"status": "PAUSED", "reason": "provider incident"})
    _call(db, admin.id, "patch", path + "/status", json={"status": "ACTIVE", "reason": "provider recovered"})
    body = _call(db, admin.id, "get", path + "/history").json()
    assert [e["action"] for e in body["data"]] == ["RESUMED", "PAUSED", "CHANGED", "CREATED"]
    assert body["pagination"]["total"] == 4
    created, changed = body["data"][3], body["data"][2]
    assert created["before"] is None and created["after"]["interval_minutes"] == 60
    assert changed["before"]["interval_minutes"] == 60 and changed["after"]["interval_minutes"] == 120
    assert changed["reason"] == "reduce provider load" and changed["actor_user_id"] == str(admin.id)
    assert [e["schedule_revision"] for e in reversed(body["data"])] == sorted(e["schedule_revision"] for e in body["data"])
    assert _call(db, admin.id, "get", path + "/history?limit=2").json()["data"][0]["action"] == "RESUMED"

    schedule = db.scalar(select(SourceRefreshSchedule).where(
        SourceRefreshSchedule.source_id == source.id
    ))
    event_id = db.scalar(select(SourceRefreshScheduleEvent.id).where(
        SourceRefreshScheduleEvent.schedule_id == schedule.id
    ))
    with pytest.raises(DBAPIError), db.begin_nested():
        db.execute(update(SourceRefreshScheduleEvent).where(
            SourceRefreshScheduleEvent.id == event_id
        ).values(reason="tampered"))
    with pytest.raises(DBAPIError), db.begin_nested():
        db.execute(delete(SourceRefreshScheduleEvent).where(
            SourceRefreshScheduleEvent.id == event_id
        ))
    with pytest.raises(DBAPIError), db.begin_nested():
        db.execute(text("TRUNCATE TABLE source_refresh_schedule_events"))


def test_history_requires_admin_and_a_real_source(db):  # noqa: F811
    from uuid import uuid4
    user, source, _c = _source(db)
    assert _call(db, user.id, "get", f"/discovery/sources/{source.id}/schedule/history").status_code == 403
    admin = _admin(db)
    assert _call(db, admin.id, "get", f"/discovery/sources/{uuid4()}/schedule/history").status_code == 404
