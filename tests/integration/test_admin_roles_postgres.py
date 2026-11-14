"""Approved support/operations access, grant lifecycle and delivery integration."""
import os
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.api.deps import get_principal
from app.infrastructure.db.models import AdminAuditEvent, AdminGrant, Notification, User
from app.infrastructure.db.session import get_db
from app.main import app
from tests.integration.test_admin_governance_postgres import _admin_test_client, db  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="PostgreSQL required")


def _staff(db, role):  # noqa: F811
    user = User(display_name=f"Role {role}", email=f"{uuid4()}@example.test", email_verified=True)
    db.add(user)
    db.flush()
    grant = AdminGrant(user_id=user.id, role=role, active=True)
    db.add(grant)
    db.commit()
    return user, grant


def _call(db, actor, method, path, **kwargs):  # noqa: F811
    client = _admin_test_client(db, actor.id)
    try:
        return client.request(method, "/api/v1/admin" + path, **kwargs)
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)
        client.close()


@pytest.mark.parametrize("role", ["SUPPORT_AGENT", "OPERATIONS"])
def test_staff_http_access_is_scoped_and_revocable(db, role):  # noqa: F811
    actor, grant = _staff(db, role)
    candidate = User(display_name="Candidate", email="candidate@example.test")
    db.add(candidate)
    db.commit()
    allowed = "/users" if role == "SUPPORT_AGENT" else "/operations/summary"
    assert _call(db, actor, "GET", allowed).status_code == 200
    for path in ("/grants", "/configs", "/smtp/configurations", "/audit", f"/users/{candidate.id}"):
        assert _call(db, actor, "GET", path).status_code == 403, path
    denied = "/operations/summary" if role == "SUPPORT_AGENT" else "/support/tickets"
    assert _call(db, actor, "GET", denied).status_code == 403
    summary = _call(db, actor, "GET", f"/users/{candidate.id}/support-summary")
    if role == "SUPPORT_AGENT":
        assert summary.status_code == 200
        assert set(summary.json()["data"]) == {"id", "display_name", "email", "status", "email_verified"}
        assert db.scalar(select(AdminAuditEvent.id).where(
            AdminAuditEvent.actor_user_id == actor.id,
            AdminAuditEvent.resource_id == candidate.id,
            AdminAuditEvent.action == "ADMIN_USER_SUPPORT_SUMMARY_VIEWED",
        )) is not None
    else:
        assert summary.status_code == 403
    grant.active = False
    db.commit()
    assert _call(db, actor, "GET", allowed).status_code == 403


def test_only_owner_grants_roles_and_can_revoke_staff_without_lockout(db):  # noqa: F811
    administrator, _ = _staff(db, "SUPER_ADMIN")
    target = User(display_name="New staff")
    db.add(target)
    db.commit()
    for role in ("SUPPORT_AGENT", "OPERATIONS"):
        created = _call(db, administrator, "POST", "/grants", json={"user_id": str(target.id), "role": role})
        assert created.status_code == 201, created.text
        grant_id = created.json()["id"]
        assert _call(db, target, "POST", "/grants", json={"user_id": str(target.id), "role": "ADMIN"}).status_code == 403
        revoked = _call(db, administrator, "POST", f"/grants/{grant_id}/revoke")
        assert revoked.status_code == 200, revoked.text
        reactivated = _call(db, administrator, "POST", "/grants", json={"user_id": str(target.id), "role": role})
        assert reactivated.status_code == 201
        assert reactivated.json()["id"] == grant_id


def test_support_agent_assignment_and_ticket_notification_follow_role(db):  # noqa: F811
    from app.application.support.notifications import support_ticket_notification_handler
    from app.application.support.service import assign_ticket
    from tests.integration.test_notification_email_delivery_postgres import factory
    from tests.integration.test_support_notification_events_postgres import _ticket

    agent, _ = _staff(db, "SUPPORT_AGENT")
    operations, _ = _staff(db, "OPERATIONS")
    candidate = User(display_name="Ticket candidate")
    db.add(candidate)
    db.flush()
    ticket = _ticket(db, candidate)
    assert assign_ticket(db, ticket, actor_id=agent.id, admin_user_id=agent.id)
    payload = {"ticket_id": str(ticket.id), "kind": "CREATED"}
    handler = support_ticket_notification_handler(factory(db))
    handler(payload)
    handler(payload)
    recipients = list(db.scalars(select(Notification.user_id).where(
        Notification.resource_id == ticket.id, Notification.event_type == "SUPPORT_TICKET_CREATED",
    )))
    assert set(recipients) == {candidate.id, agent.id}
    assert operations.id not in recipients
    response = _call(db, agent, "POST", f"/support/tickets/{ticket.id}/messages", json={
        "idempotency_key": f"role-{uuid4()}", "body": "Support role response",
    })
    assert response.status_code == 201, response.text
    from app.application.support.service import SupportConflict, add_message
    with pytest.raises(SupportConflict):
        assign_ticket(db, ticket, actor_id=agent.id, admin_user_id=operations.id)
    db.rollback()
    # Candidate messages notify an assigned support agent, then stop after revocation.
    first, _ = add_message(db, ticket, sender_id=candidate.id, sender_role="CANDIDATE",
                           idempotency_key=f"candidate-{uuid4()}", body="First candidate reply")
    handler({"ticket_id": str(ticket.id), "kind": "MESSAGE", "message_id": str(first.id)})
    before = len(list(db.scalars(select(Notification.id).where(
        Notification.user_id == agent.id, Notification.event_type == "SUPPORT_TICKET_MESSAGE_ADDED",
    ))))
    assert before == 1
    db.scalar(select(AdminGrant).where(AdminGrant.user_id == agent.id, AdminGrant.role == "SUPPORT_AGENT")).active = False
    db.commit()
    second, _ = add_message(db, ticket, sender_id=candidate.id, sender_role="CANDIDATE",
                            idempotency_key=f"candidate-{uuid4()}", body="Second candidate reply")
    handler({"ticket_id": str(ticket.id), "kind": "MESSAGE", "message_id": str(second.id)})
    assert len(list(db.scalars(select(Notification.id).where(
        Notification.user_id == agent.id, Notification.event_type == "SUPPORT_TICKET_MESSAGE_ADDED",
    )))) == before


def test_operations_alert_is_delivered_once_for_multiple_grants(db, monkeypatch):  # noqa: F811
    from app.application.jobs import discovery_alerts as rules
    from app.application.notifications import service
    from tests.integration.test_discovery_alerts_postgres import NOW, _scope, _success
    from tests.integration.test_notification_email_delivery_postgres import (
        FakeSMTP,
        factory,
        settings,
    )

    operations, _ = _staff(db, "OPERATIONS")
    db.add(AdminGrant(user_id=operations.id, role="ADMIN", active=True))
    agent, _ = _staff(db, "SUPPORT_AGENT")
    source, connector, _ = _scope(db)
    _success(db, source, connector, at=NOW)
    from datetime import timedelta
    rules.evaluate_discovery_alerts(db, now=NOW + timedelta(hours=100))
    rows = list(db.scalars(select(Notification).where(
        Notification.event_type == "ADMIN_DISCOVERY_ALERT", Notification.user_id == operations.id,
    )))
    assert len(rows) == 1
    assert db.scalar(select(Notification.id).where(
        Notification.event_type == "ADMIN_DISCOVERY_ALERT", Notification.user_id == agent.id,
    )) is None
    # Retain only OPERATIONS before sending: delivery rechecks that grant independently.
    db.scalar(select(AdminGrant).where(AdminGrant.user_id == operations.id, AdminGrant.role == "ADMIN")).active = False
    db.commit()
    smtp = FakeSMTP()
    monkeypatch.setattr(service, "get_settings", settings)
    monkeypatch.setattr(service.smtplib, "SMTP", lambda *args, **kwargs: smtp)
    service.notification_email_delivery_handler(factory(db))({"notification_id": str(rows[0].id)})
    assert len(smtp.messages) == 1


@pytest.mark.parametrize("target_status", ["SUSPENDED", "DEACTIVATED"])
def test_final_active_admin_account_cannot_be_disabled(db, target_status):  # noqa: F811
    administrator, _ = _staff(db, "ADMIN")
    # A suspended full administrator and active staff are not recovery administrators.
    other, _ = _staff(db, "ADMIN")
    other.status = "SUSPENDED"
    _staff(db, "OPERATIONS")
    db.commit()
    response = _call(db, administrator, "PATCH", f"/users/{administrator.id}/status", json={
        "target_status": target_status, "expected_revision": administrator.revision,
        "reason": "Exercise final administrator protection",
    })
    assert response.status_code == 409, response.text
    db.refresh(administrator)
    assert administrator.status == "ACTIVE"
    grant = db.scalar(select(AdminGrant).where(AdminGrant.user_id == administrator.id, AdminGrant.role == "ADMIN"))
    assert _call(db, administrator, "POST", f"/grants/{grant.id}/revoke").status_code == 403


def test_concurrent_account_suspensions_preserve_one_active_administrator(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from types import SimpleNamespace

    from fastapi import HTTPException, Request
    from sqlalchemy import create_engine, update
    from sqlalchemy.orm import Session

    from app.api.v1 import admin as api
    from app.ports.identity import AuthenticatedPrincipal

    engine = create_engine(os.environ["DATABASE_URL"])
    ids = []
    try:
        with Session(engine) as seed:
            users = [User(display_name=f"Concurrent role admin {uuid4()}") for _ in range(2)]
            seed.add_all(users)
            seed.flush()
            ids = [user.id for user in users]
            seed.add_all([AdminGrant(user_id=user_id, role="ADMIN", active=True) for user_id in ids])
            seed.commit()
        barrier = Barrier(2)
        guard = api._require_admin

        def synchronized_guard(session, principal, request):
            guard(session, principal, request)
            barrier.wait(timeout=10)

        monkeypatch.setattr(api, "_require_admin", synchronized_guard)

        def suspend(actor_id, target_id):
            with Session(engine) as session:
                request = Request({"type": "http", "method": "PATCH",
                                   "path": f"/api/v1/admin/users/{target_id}/status",
                                   "route": SimpleNamespace(path="/api/v1/admin/users/{user_id}/status"),
                                   "headers": [(b"x-request-id", str(uuid4()).encode())]})
                try:
                    principal = AuthenticatedPrincipal(user_id=actor_id, auth_provider="test")
                    api.update_user_status(
                        target_id, api.UserStatusRequest(target_status="SUSPENDED", expected_revision=1,
                                                       reason="Concurrent administrator protection"),
                        request, principal, session,
                    )
                    return 200
                except HTTPException as exc:
                    return exc.status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(suspend, ids[0], ids[1]), executor.submit(suspend, ids[1], ids[0])]
            results = sorted(f.result(timeout=30) for f in futures)
            assert results[0] == 200 and results[1] in {403, 409}
        with Session(engine) as verify:
            assert len(list(verify.scalars(select(User.id).join(AdminGrant, AdminGrant.user_id == User.id).where(
                User.id.in_(ids), User.status == "ACTIVE", AdminGrant.role == "ADMIN", AdminGrant.active.is_(True),
            )))) == 1
    finally:
        with Session(engine) as cleanup:
            if ids:
                cleanup.execute(update(AdminGrant).where(AdminGrant.user_id.in_(ids)).values(active=False))
                cleanup.commit()
        engine.dispose()
