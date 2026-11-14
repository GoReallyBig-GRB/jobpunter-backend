"""The single system owner controls grants and cannot lose application ownership."""
import os

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError

from app.infrastructure.db.models import AdminGrant, User
from tests.integration.test_admin_governance_postgres import db  # noqa: F401
from tests.integration.test_admin_roles_postgres import _call, _staff

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="PostgreSQL required")


def test_owner_controls_grants_but_cannot_be_revoked_or_disabled(db):  # noqa: F811
    owner, grant = _staff(db, "SUPER_ADMIN")
    administrator, _ = _staff(db, "ADMIN")
    assert _call(db, owner, "GET", "/configs").status_code == 200
    assert _call(db, owner, "GET", "/grants").status_code == 200
    assert _call(db, administrator, "GET", "/grants").status_code == 403
    assert _call(db, administrator, "POST", "/grants", json={
        "user_id": str(administrator.id), "role": "OPERATIONS",
    }).status_code == 403
    assert _call(db, owner, "POST", f"/grants/{grant.id}/revoke").status_code == 409
    for actor in (owner, administrator):
        for status in ("SUSPENDED", "DEACTIVATED"):
            result = _call(db, actor, "PATCH", f"/users/{owner.id}/status", json={
                "target_status": status, "expected_revision": owner.revision,
                "reason": "Attempt to disable protected owner",
            })
            assert result.status_code == 409, result.text
    db.refresh(owner)
    db.refresh(grant)
    assert owner.status == "ACTIVE" and grant.active
    # Bootstrap is out of band; even the owner cannot manufacture a second owner via API.
    assert _call(db, owner, "POST", "/grants", json={
        "user_id": str(administrator.id), "role": "SUPER_ADMIN",
    }).status_code == 422


def test_database_rejects_owner_removal_reassignment_and_truncation(db):  # noqa: F811
    owner, grant = _staff(db, "SUPER_ADMIN")
    other = User(display_name="Other owner target")
    db.add(other)
    db.flush()
    for statement in (
        update(AdminGrant).where(AdminGrant.id == grant.id).values(active=False),
        update(AdminGrant).where(AdminGrant.id == grant.id).values(role="ADMIN"),
        update(AdminGrant).where(AdminGrant.id == grant.id).values(user_id=other.id),
        delete(AdminGrant).where(AdminGrant.id == grant.id),
        update(User).where(User.id == owner.id).values(status="SUSPENDED"),
        update(User).where(User.id == owner.id).values(status="DEACTIVATED"),
        delete(User).where(User.id == owner.id),
        text("TRUNCATE TABLE admin_grants"),
    ):
        with pytest.raises(DBAPIError), db.begin_nested():
            db.execute(statement)
    with pytest.raises(DBAPIError), db.begin_nested():
        db.add(AdminGrant(user_id=other.id, role="SUPER_ADMIN", active=True))
        db.flush()
    assert db.scalar(select(AdminGrant.user_id).where(AdminGrant.role == "SUPER_ADMIN")) == owner.id


def test_database_refuses_inactive_owner_bootstrap(db):  # noqa: F811
    user = User(display_name="Inactive owner", status="SUSPENDED")
    db.add(user)
    db.flush()
    with pytest.raises(DBAPIError), db.begin_nested():
        db.add(AdminGrant(user_id=user.id, role="SUPER_ADMIN", active=True))
        db.flush()


def test_downgrade_cannot_remove_existing_owner_protection(db):  # noqa: F811
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    owner, grant = _staff(db, "SUPER_ADMIN")
    path = Path(__file__).resolve().parents[2] / "migrations/versions/0083_protected_system_owner.py"
    spec = importlib.util.spec_from_file_location("owner_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with (
        pytest.raises(DBAPIError), db.begin_nested(),
        Operations.context(MigrationContext.configure(db.connection())),
    ):
        migration.downgrade()
    db.refresh(grant)
    assert grant.active and grant.user_id == owner.id
    with pytest.raises(DBAPIError), db.begin_nested():
        db.execute(update(AdminGrant).where(AdminGrant.id == grant.id).values(active=False))
