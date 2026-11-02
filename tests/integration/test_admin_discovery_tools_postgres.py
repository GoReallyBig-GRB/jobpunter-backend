"""E3.1/E3.4: code-owned tool inventory, audited pause/enable, and enforcement before acquisition."""
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.api.deps import get_principal
from app.infrastructure.acquisition.registry import AcquisitionConnectorRegistry
from app.infrastructure.db.models import AcquisitionRun, DiscoveryMissionSourceRun, User
from app.infrastructure.db.models.governance import AdminAuditEvent, AdminGrant
from app.infrastructure.db.session import get_db
from app.main import app
from tests.integration.test_admin_governance_postgres import _admin_test_client, db  # noqa: F401

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)
BASE = "/api/v1/admin/discovery/tools"


def _admin(db):
    admin = User(display_name="tools admin")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()
    return admin


def _call(db, principal_id, method, path="", **kwargs):
    client = _admin_test_client(db, principal_id)
    try:
        return getattr(client, method)(BASE + path, **kwargs)
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)


def test_non_admin_is_denied_for_inventory_and_status_change(db):  # noqa: F811
    user = User(display_name="plain")
    db.add(user)
    db.flush()
    assert _call(db, user.id, "get").status_code in {401, 403}
    assert _call(db, user.id, "patch", "/JSON_API/status", json={"status": "DISABLED", "reason": "x" * 10}).status_code in {401, 403}


def test_inventory_lists_exactly_the_registered_tools_with_truthful_state(db):  # noqa: F811
    admin = _admin(db)
    response = _call(db, admin.id, "get")
    assert response.status_code == 200, response.text
    items = response.json()["data"]
    registered = {kind for kind, _version in AcquisitionConnectorRegistry().supported_connectors}
    assert {item["tool_key"] for item in items} == registered
    for item in items:
        assert item["activation_status"] == "ENABLED"
        assert item["health_status"] == "NOT_CHECKED"  # never inferred from configuration
        assert isinstance(item["supported_target_kinds"], list)
        assert "configuration" not in item and "secret" not in str(item).lower()


def test_disable_requires_reason_known_tool_and_a_real_change(db):  # noqa: F811
    admin = _admin(db)
    assert _call(db, admin.id, "patch", "/JSON_API/status", json={"status": "DISABLED"}).status_code == 422
    assert _call(db, admin.id, "patch", "/JSON_API/status", json={"status": "DISABLED", "reason": "short"}).status_code == 422
    unknown = _call(db, admin.id, "patch", "/MADE_UP_TOOL/status", json={"status": "DISABLED", "reason": "not a real tool"})
    assert unknown.status_code == 404
    same = _call(db, admin.id, "patch", "/JSON_API/status", json={"status": "ENABLED", "reason": "already enabled here"})
    assert same.status_code == 409


def test_disable_and_reenable_are_audited_and_visible_in_inventory(db):  # noqa: F811
    admin = _admin(db)
    disabled = _call(db, admin.id, "patch", "/RSS_ATOM/status", json={"status": "DISABLED", "reason": "provider incident"})
    assert disabled.status_code == 200, disabled.text
    listed = {i["tool_key"]: i for i in _call(db, admin.id, "get").json()["data"]}
    assert listed["RSS_ATOM"]["activation_status"] == "DISABLED"
    assert listed["RSS_ATOM"]["status_reason"] == "provider incident"
    assert listed["JSON_API"]["activation_status"] == "ENABLED"
    reenabled = _call(db, admin.id, "patch", "/RSS_ATOM/status", json={"status": "ENABLED", "reason": "incident resolved"})
    assert reenabled.status_code == 200
    events = db.scalars(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "DISCOVERY_TOOL_STATUS_CHANGED",
        AdminAuditEvent.actor_user_id == admin.id,
    ).order_by(AdminAuditEvent.created_at)).all()
    assert [e.details["status"] for e in events] == ["DISABLED", "ENABLED"]
    assert events[0].details["previous_status"] == "ENABLED"
    assert events[0].details["reason"] == "provider incident"
    assert events[0].outcome == "SUCCESS"
