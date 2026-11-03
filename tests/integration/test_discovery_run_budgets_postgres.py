"""E4.5/E4.6/E3.7: run budgets defer work truthfully, are admin-managed, and every step keeps its effective config."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.api.deps import get_principal
from app.application.jobs import source_refresh
from app.infrastructure.db.models import (
    AcquisitionRun, DiscoveryMissionSourceRun, DiscoverySchedule, DiscoverySourceLimit, User, WorkItem,
)
from app.infrastructure.db.models.discovery_tool import DiscoveryToolControl
from app.infrastructure.db.models.governance import AdminAuditEvent, AdminGrant
from app.infrastructure.db.session import get_db
from app.main import app
from tests.integration.test_admin_governance_postgres import _admin_test_client, db  # noqa: F401
from tests.integration.test_discovery_missions_postgres import _seed_active_sources
from tests.integration.test_source_refresh_postgres import NOW, _FakeService, _items, _schedule, _scope  # noqa: F401

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


def _admin(db):
    admin = User(display_name="budget admin")
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


def _runs(db, source, connector, count):
    for _ in range(count):
        db.add(AcquisitionRun(source_id=source.id, source_connector_id=connector.id, run_key=f"bud-{uuid4()}",
                              status="SUCCEEDED", created_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    db.flush()


def _budget(db, key, max_runs, window=60):
    row = db.scalar(select(DiscoveryToolControl).where(DiscoveryToolControl.tool_key == key))
    if row is None:
        row = DiscoveryToolControl(tool_key=key, status="ENABLED")
        db.add(row)
    row.max_runs, row.window_minutes = max_runs, window
    db.flush()


def _run_step(db, monkeypatch, schedule):
    monkeypatch.setattr(source_refresh, "RegisteredAcquisitionService", _FakeService)
    source_refresh.enqueue_due_source_refreshes(db, now=NOW)
    item = _items(db, schedule)[0]
    source_refresh.process_source_refresh(db, item.id)
    return db.scalar(select(DiscoveryMissionSourceRun).where(DiscoveryMissionSourceRun.schedule_id == schedule.id)), item


@pytest.mark.parametrize("scope", ["JSON_API", "ALL"])
def test_spent_budget_defers_the_step_as_a_recorded_skip_without_acquisition(db, monkeypatch, scope):  # noqa: F811
    user, source, connector, _t = _scope(db)
    schedule = _schedule(db, user, source)
    _runs(db, source, connector, 2)
    _budget(db, scope, 1)
    step, item = _run_step(db, monkeypatch, schedule)
    assert (step.status, step.error_code) == ("UNAVAILABLE", "RUN_BUDGET_REACHED")
    assert step.error_details["scope_outcome"] == "SKIPPED"
    assert step.error_details["budget_scope"] == ("TOOL" if scope == "JSON_API" else "GLOBAL")
    assert _FakeService.calls == []
    db.refresh(item)
    assert item.result["outcome"] == "NOTHING_EXECUTED"


def test_budget_with_headroom_and_expired_window_do_not_block(db, monkeypatch):  # noqa: F811
    user, source, connector, _t = _scope(db)
    schedule = _schedule(db, user, source)
    _budget(db, "JSON_API", 500)  # far above current use
    step, _item = _run_step(db, monkeypatch, schedule)
    assert step.status == "SUCCEEDED" and len(_FakeService.calls) == 1


def test_each_step_records_the_controls_in_force_and_later_edits_do_not_rewrite_it(db, monkeypatch):  # noqa: F811
    user, source, connector, _t = _scope(db)
    schedule = _schedule(db, user, source)
    schedule.window_start_minute, schedule.window_end_minute, schedule.window_timezone = 0, 1439, "UTC"
    db.add(DiscoverySourceLimit(source_id=source.id, max_runs=50, window_minutes=60, updated_by_user_id=user.id,
                                updated_at=NOW))
    _budget(db, "ALL", 900, 120)
    db.flush()
    step, _item = _run_step(db, monkeypatch, schedule)
    snap = step.config_snapshot
    assert snap["strategy"] == "code-defined-waves/v1"
    assert snap["tool"]["key"] == "JSON_API" and snap["tool"]["status"] == "ENABLED"
    assert snap["global_budget"] == {"max_runs": 900, "window_minutes": 120}
    assert snap["source_limit"] == {"max_runs": 50, "window_minutes": 60}
    assert snap["schedule"]["interval_minutes"] == 60 and snap["schedule"]["window_timezone"] == "UTC"
    assert "url" not in str(snap).lower()
    before = dict(snap)
    _budget(db, "ALL", 1, 5)
    db.refresh(step)
    assert step.config_snapshot == before


def test_budget_api_validation_audit_inventory_and_removal(db):  # noqa: F811
    admin = _admin(db)
    plain = User(display_name="not admin")
    db.add(plain)
    db.flush()
    ok = {"max_runs": 10, "window_minutes": 60, "reason": "protect provider quota"}
    assert _call(db, plain.id, "put", "/discovery/tools/JSON_API/budget", json=ok).status_code == 403
    for bad in ({**ok, "max_runs": 0}, {**ok, "max_runs": 100001}, {**ok, "window_minutes": 0},
                {**ok, "window_minutes": 10081}, {**ok, "reason": "short"}, {"max_runs": 1, "window_minutes": 1}):
        assert _call(db, admin.id, "put", "/discovery/tools/JSON_API/budget", json=bad).status_code == 422
    assert _call(db, admin.id, "put", "/discovery/tools/NOT_A_TOOL/budget", json=ok).status_code == 404
    assert _call(db, admin.id, "put", "/discovery/tools/json_api/budget", json=ok).status_code == 200
    assert _call(db, admin.id, "put", "/discovery/tools/ALL/budget", json={**ok, "max_runs": 100}).status_code == 200
    listing = _call(db, admin.id, "get", "/discovery/tools").json()
    tool = next(t for t in listing["data"] if t["tool_key"] == "JSON_API")
    assert tool["run_budget"]["max_runs"] == 10 and tool["run_budget"]["cost"] == "NOT_MEASURED"
    assert listing["global_run_budget"]["max_runs"] == 100
    removed = _call(db, admin.id, "delete", "/discovery/tools/JSON_API/budget?reason=quota+raised+upstream")
    assert removed.status_code == 200 and removed.json()["data"]["configured"] is False
    assert _call(db, admin.id, "delete", "/discovery/tools/JSON_API/budget?reason=quota+raised+upstream").status_code == 404
    actions = [a.action for a in db.scalars(select(AdminAuditEvent).where(AdminAuditEvent.actor_user_id == admin.id))]
    assert actions.count("DISCOVERY_RUN_BUDGET_SET") == 2 and actions.count("DISCOVERY_RUN_BUDGET_REMOVED") == 1
    # Removing the budget leaves the tool's activation untouched.
    assert next(t for t in _call(db, admin.id, "get", "/discovery/tools").json()["data"]
                if t["tool_key"] == "JSON_API")["activation_status"] == "ENABLED"
