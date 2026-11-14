"""Role policy is exhaustive and enforced through the real grant query and denial audit."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.v1 import admin, admin_discovery, support
from app.infrastructure.db.models.governance import AdminAuditEvent, AdminGrant
from app.infrastructure.db.models.identity import User
from app.ports.identity import AuthenticatedPrincipal
from app.security.admin_permissions import ADMIN_ROLES, ROUTE_ROLES


@pytest.fixture(scope="module")
def engine():
    engine = create_engine("sqlite://")
    for table in (User.__table__, AdminGrant.__table__, AdminAuditEvent.__table__):
        table.create(engine)
    yield engine
    engine.dispose()


def _request(method, path):
    return Request({"type": "http", "method": method, "path": path,
                    "route": SimpleNamespace(path=path),
                    "headers": [(b"x-request-id", str(uuid4()).encode()), (b"x-role", b"ADMIN")]})


def test_policy_covers_every_admin_route_and_no_stale_routes():
    routes = {
        (method, "/api/v1" + route.path)
        for router in (admin.router, admin_discovery.router, support.admin_router)
        for route in router.routes
        for method in route.methods
    }
    assert routes == set(ROUTE_ROLES)
    for (method, path), roles in ROUTE_ROLES.items():
        assert "SUPER_ADMIN" in roles and roles <= ADMIN_ROLES
        if "SUPPORT_AGENT" in roles:
            assert path.startswith("/api/v1/admin/support/tickets") or (
                method == "GET" and path in {
                    "/api/v1/admin/users", "/api/v1/admin/users/{user_id}/support-summary",
                }
            )
        if "OPERATIONS" in roles:
            assert not any(part in path for part in (
                "/grants", "/users", "/support", "/smtp/", "/configs", "/impersonation", "/audit",
            ))


@pytest.mark.parametrize("method,path", sorted(ROUTE_ROLES))
@pytest.mark.parametrize("role,active,user_status", [
    ("SUPER_ADMIN", True, "ACTIVE"), ("ADMIN", True, "ACTIVE"), ("SUPPORT_AGENT", True, "ACTIVE"),
    ("OPERATIONS", True, "ACTIVE"), ("AUDITOR", True, "ACTIVE"),
    ("ADMIN", False, "ACTIVE"), ("ADMIN", True, "SUSPENDED"),
])
def test_every_route_enforces_roles_and_audits_denial(engine, method, path, role, active, user_status):
    with Session(engine) as db:
        existing_owner = db.scalar(select(AdminGrant).where(AdminGrant.role == "SUPER_ADMIN")) if role == "SUPER_ADMIN" else None
        if existing_owner is not None:
            user = db.get(User, existing_owner.user_id)
        else:
            user = User(status=user_status)
            db.add(user)
            db.flush()
            db.add(AdminGrant(user_id=user.id, role=role, active=active))
            db.commit()
        principal = AuthenticatedPrincipal(user_id=user.id, auth_provider="test")
        request = _request(method, path)
        allowed = active and user_status == "ACTIVE" and role in ROUTE_ROLES[(method, path)]
        if allowed:
            admin._require_admin(db, principal, request)
        else:
            with pytest.raises(HTTPException) as exc:
                admin._require_admin(db, principal, request)
            assert exc.value.status_code == 403
            event = db.scalar(select(AdminAuditEvent).where(
                AdminAuditEvent.request_id == request.headers["x-request-id"],
            ))
            assert event is not None and event.outcome == "DENIED"
            assert event.actor_user_id == user.id


def test_unclassified_route_denies_even_full_administrator(engine):
    with Session(engine) as db:
        user = User()
        db.add(user)
        db.flush()
        db.add(AdminGrant(user_id=user.id, role="ADMIN", active=True))
        db.commit()
        with pytest.raises(HTTPException) as exc:
            admin._require_admin(db, AuthenticatedPrincipal(user_id=user.id, auth_provider="test"),
                                 _request("POST", "/api/v1/admin/unclassified"))
        assert exc.value.status_code == 403


@pytest.mark.parametrize("value,valid", [("60", True), ("900", True), ("901", False),
                                         ("3600", False), ("59", False), ("bad", False)])
def test_approved_inspection_limit(monkeypatch, value, valid):
    monkeypatch.setenv("JOBPUNTER_ADMIN_IMPERSONATION_TTL_SECONDS", value)
    if valid:
        assert admin._impersonation_ttl_seconds() == int(value)
    else:
        with pytest.raises(HTTPException):
            admin._impersonation_ttl_seconds()


@pytest.mark.parametrize("template", ["/admin/support/tickets", "/api/v1/admin/support/tickets"])
def test_included_and_flattened_router_templates_share_policy(template):
    from app.security.admin_permissions import SUPPORT_ROLES, permitted_roles
    assert permitted_roles(_request("GET", template)) == SUPPORT_ROLES
