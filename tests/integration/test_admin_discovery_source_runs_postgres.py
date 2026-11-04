"""E5.2/E5.3/E5.6: durable per-source discovery steps are explainable to admins without private data."""
import os
from uuid import uuid4

import pytest

from app.api.deps import get_principal
from app.application.jobs.discovery_missions import create_discovery_mission
from app.infrastructure.db.models import User
from app.infrastructure.db.models.governance import AdminGrant
from app.infrastructure.db.session import get_db
from app.main import app
from tests.integration.test_admin_governance_postgres import _admin_test_client, db  # noqa: F401
from tests.integration.test_discovery_missions_postgres import _candidate, _seed_active_sources

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)
URL = "/api/v1/admin/discovery/source-runs"


def _admin(db):
    admin = User(display_name="runs admin")
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


def _skipped_mission(db):
    from app.workers import discovery_mission_worker as mission_worker

    user, profile = _candidate(db)
    mission = create_discovery_mission(
        db, candidate_profile_id=profile.id, idempotency_key=f"admin-runs-{uuid4()}",
    )
    source, connector, target = _seed_active_sources(db, 1, created_by_user_id=user.id)[0]
    target.status = "PAUSED"
    db.flush()
    mission_worker.process_mission_step(db, mission.id)
    return mission, source, connector, target, profile


def test_non_admin_is_denied(db):  # noqa: F811
    user = User(display_name="plain")
    db.add(user)
    db.flush()
    assert _get(db, user.id).status_code in {401, 403}


def test_skipped_scope_is_explained_with_tool_reason_and_no_private_data(db):  # noqa: F811
    admin = _admin(db)
    mission, source, connector, target, profile = _skipped_mission(db)
    response = _get(db, admin.id, source_id=str(source.id))
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert len(rows) == 1
    row = rows[0]
    assert row["mission_id"] == str(mission.id)
    assert row["tool_key"] == connector.connector_type.strip().upper()
    assert row["status"] == "UNAVAILABLE"
    assert row["error_code"] == "DISCOVERY_TARGET_NOT_ACTIVE_OR_VALIDATED"
    assert row["scope_outcome"] == "SKIPPED"
    assert row["details"]["target_status"] == "PAUSED"
    assert row["acquisition_run_id"] is None
    # Candidate-private identity, the target URL and its hash never reach this global view.
    for private in (str(profile.id), target.url, "target_url_sha256", "candidate_profile_id"):
        assert private not in response.text


def test_filters_summary_and_bounds(db):  # noqa: F811
    admin = _admin(db)
    _skipped_mission(db)
    matching = _get(db, admin.id, error_code="DISCOVERY_TARGET_NOT_ACTIVE_OR_VALIDATED")
    assert matching.status_code == 200
    body = matching.json()
    assert body["data"] and all(r["error_code"] == "DISCOVERY_TARGET_NOT_ACTIVE_OR_VALIDATED" for r in body["data"])
    assert body["summary"]["by_outcome"]["DISCOVERY_TARGET_NOT_ACTIVE_OR_VALIDATED"] >= 1
    assert body["pagination"]["total"] >= 1
    none = _get(db, admin.id, error_code="NO_SUCH_CODE")
    assert none.status_code == 200 and none.json()["data"] == []
    assert _get(db, admin.id, limit=101).status_code == 422
    assert _get(db, admin.id, status="NOT_A_STATUS").status_code == 422
