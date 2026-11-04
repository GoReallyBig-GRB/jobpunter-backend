"""D3: audit export is bounded, allowlisted, redacted, rate-limited and itself audited."""
import csv
import io
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.api.deps import get_principal
from app.api.v1 import admin as admin_module
from app.infrastructure.db.models import User
from app.infrastructure.db.models.governance import AdminAuditEvent, AdminGrant
from app.infrastructure.db.session import get_db
from app.main import app
from tests.integration.test_admin_governance_postgres import _admin_test_client, db  # noqa: F401

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)

START = datetime(2020, 1, 1, tzinfo=timezone.utc)
END = datetime(2020, 1, 10, tzinfo=timezone.utc)
URL = "/api/v1/admin/audit/export"


def _admin(db, name="export admin"):
    admin = User(display_name=name)
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()
    return admin


def _event(db, actor, when, **kw):
    values = dict(actor_user_id=actor.id, action="TEST_ACTION", resource_type="probe",
                  resource_id=uuid4(), outcome="SUCCESS", details={}, request_id="req-1", created_at=when)
    values.update(kw)
    row = AdminAuditEvent(**values)
    db.add(row)
    db.flush()
    return row


def _get(db, principal_id, params):
    client = _admin_test_client(db, principal_id)
    try:
        return client.get(URL, params=params)
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)


def _range(**extra):
    return {"created_after": START.isoformat(), "created_before": END.isoformat(), **extra}


def test_non_admin_cannot_export(db):  # noqa: F811
    user = User(display_name="plain")
    db.add(user)
    db.flush()
    assert _get(db, user.id, _range()).status_code in {401, 403}


@pytest.mark.parametrize("params", [
    {},
    {"created_after": START.isoformat()},
    {"created_before": END.isoformat()},
    {"created_after": END.isoformat(), "created_before": START.isoformat()},
    {"created_after": START.isoformat(), "created_before": (START + timedelta(days=91)).isoformat()},
    {"created_after": START.isoformat(), "created_before": END.isoformat(), "format": "xml"},
])
def test_range_is_required_ordered_and_at_most_90_days(db, params):  # noqa: F811
    admin = _admin(db)
    assert _get(db, admin.id, params).status_code == 422


def test_exactly_90_days_is_accepted(db):  # noqa: F811
    admin = _admin(db)
    response = _get(db, admin.id, {"created_after": START.isoformat(),
                                   "created_before": (START + timedelta(days=90)).isoformat()})
    assert response.status_code == 200, response.text


def test_jsonl_export_has_only_allowlisted_fields_in_range_order_and_redacts_secrets(db):  # noqa: F811
    admin = _admin(db)
    inside_late = _event(db, admin, START + timedelta(days=5), action="B")
    inside_early = _event(db, admin, START + timedelta(days=2), action="A", details={
        "api_key": "sk-live-123", "nested": {"Password": "hunter2", "ok": "kept"},
        "message_body": "private ticket text", "note": "fine",
    })
    _event(db, admin, START - timedelta(days=1), action="BEFORE")
    _event(db, admin, END + timedelta(days=1), action="AFTER")

    response = _get(db, admin.id, _range())
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    rows = [json.loads(line) for line in response.text.splitlines()]
    assert [r["action"] for r in rows] == ["A", "B"]
    assert set(rows[0]) == {"id", "created_at", "actor_user_id", "action", "resource_type",
                            "resource_id", "outcome", "request_id", "details"}
    assert rows[0]["id"] == str(inside_early.id) and rows[1]["id"] == str(inside_late.id)
    for leaked in ("sk-live-123", "hunter2", "private ticket text"):
        assert leaked not in response.text
    assert rows[0]["details"]["nested"]["ok"] == "kept" and rows[0]["details"]["note"] == "fine"


def test_export_is_itself_audited_without_exported_content(db):  # noqa: F811
    admin = _admin(db)
    _event(db, admin, START + timedelta(days=1), details={"note": "secret-marker-xyz"})
    _event(db, admin, START + timedelta(days=2))
    assert _get(db, admin.id, _range()).status_code == 200
    audit = db.scalars(select(AdminAuditEvent).where(
        AdminAuditEvent.actor_user_id == admin.id, AdminAuditEvent.action == "AUDIT_EXPORTED")).one()
    assert audit.outcome == "SUCCESS"
    assert audit.details["row_count"] == 2 and audit.details["format"] == "jsonl"
    assert "created_after" in audit.details and "created_before" in audit.details
    assert "secret-marker-xyz" not in json.dumps(audit.details)


def test_csv_export_neutralises_spreadsheet_formulas(db):  # noqa: F811
    admin = _admin(db)
    _event(db, admin, START + timedelta(days=1), action="=HYPERLINK(1)", request_id="+cmd")
    response = _get(db, admin.id, _range(format="csv"))
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows[0]["action"] == "'=HYPERLINK(1)" and rows[0]["request_id"] == "'+cmd"


def test_export_over_row_cap_is_refused_and_not_counted_as_exported(db, monkeypatch):  # noqa: F811
    admin = _admin(db)
    for day in range(3):
        _event(db, admin, START + timedelta(days=day))
    monkeypatch.setattr(admin_module, "AUDIT_EXPORT_MAX_ROWS", 2)
    assert _get(db, admin.id, _range()).status_code == 422
    assert db.scalars(select(AdminAuditEvent).where(
        AdminAuditEvent.actor_user_id == admin.id, AdminAuditEvent.action == "AUDIT_EXPORTED")).first() is None


def test_export_is_rate_limited_per_admin_and_denial_is_audited(db):  # noqa: F811
    admin, other = _admin(db), _admin(db, "other export admin")
    for _ in range(admin_module.AUDIT_EXPORT_MAX_PER_HOUR):
        assert _get(db, admin.id, _range()).status_code == 200
    limited = _get(db, admin.id, _range())
    assert limited.status_code == 429
    assert db.scalars(select(AdminAuditEvent).where(
        AdminAuditEvent.actor_user_id == admin.id, AdminAuditEvent.action == "AUDIT_EXPORT_RATE_LIMITED",
        AdminAuditEvent.outcome == "DENIED")).first() is not None
    assert _get(db, other.id, _range()).status_code == 200
