"""A0R-03: other parameterised admin detail routes are admin-only, 404 on unknown ids,
never cross candidate boundaries, and never echo private payloads."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.api.deps import get_principal
from app.infrastructure.db.models import User, WorkItem
from app.infrastructure.db.models.governance import AdminGrant, AdminImpersonationSession
from app.infrastructure.db.session import get_db
from app.main import app
from tests.integration.test_admin_governance_postgres import _admin_test_client, db  # noqa: F401
from tests.integration.test_admin_user_detail_isolation_postgres import _candidate

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)

PARAM_ROUTES = [
    "/api/v1/admin/job-resolutions/{id}",
    "/api/v1/admin/operations/work-items/{id}",
    "/api/v1/admin/discovery-targets/{id}",
    "/api/v1/admin/impersonation-sessions/{id}/workspace",
    "/api/v1/admin/users/{id}",
]


def _admin(db, name="detail admin"):
    admin = User(display_name=name)
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.flush()
    return admin


def _get(db, principal_id, path):
    client = _admin_test_client(db, principal_id)
    try:
        return client.get(path)
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize("route", PARAM_ROUTES)
def test_non_admin_is_denied_before_any_lookup(db, route):  # noqa: F811
    outsider = User(display_name="plain user")
    db.add(outsider)
    db.flush()
    response = _get(db, outsider.id, route.format(id=uuid4()))
    assert response.status_code in {401, 403}, response.text


@pytest.mark.parametrize("route", PARAM_ROUTES)
def test_unknown_id_is_404_for_admin(db, route):  # noqa: F811
    admin = _admin(db)
    response = _get(db, admin.id, route.format(id=uuid4()))
    assert response.status_code == 404, response.text


def test_work_item_detail_never_returns_payload_or_result_contents(db):  # noqa: F811
    admin = _admin(db)
    item = WorkItem(
        work_type="PROBE", resource_type="probe", resource_id=uuid4(), status="FAILED",
        idempotency_key=f"probe-{uuid4()}",
        result={"secret": "alpha result marker"},
        error_details={"category": "TEST", "raw": "alpha error marker"}, error_code="X",
    )
    db.add(item)
    db.flush()
    response = _get(db, admin.id, f"/api/v1/admin/operations/work-items/{item.id}")
    assert response.status_code == 200, response.text
    assert "marker" not in response.text


def _session(db, admin, target, **overrides):
    values = dict(
        admin_user_id=admin.id, target_user_id=target.id, reason="isolation test",
        status="ACTIVE", started_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    values.update(overrides)
    row = AdminImpersonationSession(**values)
    db.add(row)
    db.flush()
    return row


def test_impersonation_workspace_is_scoped_to_target_and_owner_admin(db):  # noqa: F811
    owner, other_admin = _admin(db, "owner"), _admin(db, "other")
    alpha, _ = _candidate(db, "alpha")
    beta, _ = _candidate(db, "beta")
    session = _session(db, owner, alpha)
    path = f"/api/v1/admin/impersonation-sessions/{session.id}/workspace"

    ok = _get(db, owner.id, path)
    assert ok.status_code == 200, ok.text
    for leaked in (str(beta.id), "beta"):
        assert leaked not in ok.text
    for private in ("alpha-cv.pdf", "private/alpha/key", "alpha ticket body", "alpha private artifact"):
        assert private not in ok.text

    # another administrator cannot read this admin's session
    assert _get(db, other_admin.id, path).status_code == 404


@pytest.mark.parametrize("status,code", [("ENDED", 410), ("REVOKED", 410)])
def test_ended_session_no_longer_authorizes_workspace_reads(db, status, code):  # noqa: F811
    admin = _admin(db)
    alpha, _ = _candidate(db, "alpha")
    session = _session(db, admin, alpha, status=status, ended_at=datetime.now(timezone.utc),
                       end_reason="ADMIN_ENDED")
    response = _get(db, admin.id, f"/api/v1/admin/impersonation-sessions/{session.id}/workspace")
    assert response.status_code == code
    assert "alpha" not in response.text
