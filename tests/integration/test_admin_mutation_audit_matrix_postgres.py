"""Material-write x outcome audit matrix for every mutating Admin route.

For each registered mutating Admin/support-admin route this drives a real request as an
active administrator with an empty body and unknown identifiers, then requires that the
request ends cleanly (never a 5xx) and leaves a request-correlated audit record that does
not echo submitted values. Denial for non-administrators is covered separately in
tests/unit/test_admin_route_authorization.py.
"""
import os
import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.api.v1 import admin as admin_api
from app.api.v1 import support as support_api
from app.infrastructure.db.models import AdminAuditEvent, AdminGrant, AuditEvent, User
from app.infrastructure.db.session import get_db
from app.main import app
from app.ports.identity import AuthenticatedPrincipal

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
SENTINEL = "PRIVATE_MATRIX_SENTINEL_9c41"


def _mutating_admin_routes():
    routes = []
    for router in (admin_api.router, support_api.admin_router):
        for route in router.routes:
            for method in sorted((getattr(route, "methods", None) or set()) & MUTATING):
                path = route.path if route.path.startswith(router.prefix) else router.prefix + route.path
                routes.append((method, "/api/v1" + path))
    return sorted(set(routes), key=lambda item: (item[1], item[0]))


ROUTES = _mutating_admin_routes()


@pytest.fixture
def db():
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_matrix_covers_the_full_mutating_admin_surface():
    assert len(ROUTES) >= 30, ROUTES
    assert all(path.startswith("/api/v1/") for _method, path in ROUTES)


@pytest.mark.parametrize(("method", "template"), ROUTES, ids=[f"{m} {p}" for m, p in ROUTES])
def test_mutating_admin_route_ends_cleanly_with_a_correlated_audit_record(db, method, template):
    admin = User(display_name=f"Audit matrix admin {uuid4()}")
    db.add(admin)
    db.flush()
    db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
    db.commit()  # failure handlers roll back first; persisted principals survive that
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=admin.id, auth_provider="test"
    )
    app.dependency_overrides[get_db] = lambda: db
    request_id = f"matrix-{uuid4()}"
    url = re.sub(r"\{[^}]+\}", lambda _m: str(uuid4()), template)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = client.request(
            method, url, headers={"x-request-id": request_id},
            json={"unexpected_field": SENTINEL},
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()

    assert response.status_code < 500, (response.status_code, response.text)
    assert SENTINEL not in response.text

    admin_events = db.scalars(select(AdminAuditEvent).where(
        AdminAuditEvent.request_id == request_id
    )).all()
    domain_events = db.scalars(select(AuditEvent).where(
        AuditEvent.actor_id == admin.id
    )).all()
    assert admin_events or domain_events, (
        f"{method} {template} answered {response.status_code} without an audit record"
    )
    for event in admin_events:
        assert SENTINEL not in str(event.details)
        assert event.actor_user_id == admin.id
        if response.status_code >= 400:
            assert event.outcome in {"VALIDATION_FAILED", "REJECTED", "DENIED", "FAILED"}, (
                event.outcome, response.status_code
            )
