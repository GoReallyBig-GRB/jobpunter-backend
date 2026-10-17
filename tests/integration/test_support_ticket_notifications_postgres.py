import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.application.support.notifications import support_ticket_notification_handler
from app.infrastructure.db.models import (
    AdminGrant,
    Notification,
    NotificationEmailDelivery,
    SupportTicket,
    User,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


@pytest.fixture
def session_factory():
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(
        bind=connection, class_=Session, join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    try:
        yield factory
    finally:
        connection.rollback()
        connection.close()
        engine.dispose()


def test_support_ticket_notification_fanout_is_idempotent_on_retry(session_factory):
    with session_factory() as db:
        candidate = User(
            display_name="Support candidate",
            email=f"{uuid4()}@example.test",
            email_verified=True,
            status="ACTIVE",
        )
        admin = User(
            display_name="Support admin",
            email=f"{uuid4()}@example.test",
            email_verified=True,
            status="ACTIVE",
        )
        db.add_all([candidate, admin])
        db.flush()
        db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True, created_at=datetime.now(timezone.utc)))
        ticket = SupportTicket(
            candidate_user_id=candidate.id,
            idempotency_key=f"support-{uuid4()}",
            title="Cannot access account",
            issue_details="Sign-in fails after verification.",
            status="OPEN",
            candidate_unread=False,
            admin_unread=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(ticket)
        db.commit()
        ticket_id = ticket.id
        candidate_id = candidate.id
        admin_id = admin.id

    handler = support_ticket_notification_handler(session_factory)
    payload = {"ticket_id": str(ticket_id), "kind": "CREATED"}
    handler(payload)
    handler(payload)

    with session_factory() as db:
        notifications = db.scalars(select(Notification).where(
            Notification.resource_id == ticket_id,
        )).all()
        assert len(notifications) == 2
        assert {row.user_id for row in notifications} == {candidate_id, admin_id}
        assert len({row.event_key for row in notifications}) == 2
        deliveries = db.scalars(select(NotificationEmailDelivery).where(
            NotificationEmailDelivery.notification_id.in_([row.id for row in notifications])
        )).all()
        assert len(deliveries) == 2
        assert all(row.status == "DISABLED" for row in deliveries)


def test_support_ticket_resolution_and_closure_have_distinct_idempotency_keys(session_factory):
    with session_factory() as db:
        candidate = User(
            display_name="Support candidate",
            email=f"{uuid4()}@example.test",
            email_verified=True,
            status="ACTIVE",
        )
        admin = User(
            display_name="Support admin",
            email=f"{uuid4()}@example.test",
            email_verified=True,
            status="ACTIVE",
        )
        db.add_all([candidate, admin])
        db.flush()
        db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True, created_at=datetime.now(timezone.utc)))
        ticket = SupportTicket(
            candidate_user_id=candidate.id,
            idempotency_key=f"support-{uuid4()}",
            title="Billing question",
            issue_details="Please explain a charge.",
            status="CLOSED",
            candidate_unread=True,
            admin_unread=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(ticket)
        db.commit()
        ticket_id = ticket.id
        candidate_id = candidate.id
        admin_id = admin.id

    handler = support_ticket_notification_handler(session_factory)
    handler({"ticket_id": str(ticket_id), "kind": "RESOLVED"})
    handler({"ticket_id": str(ticket_id), "kind": "CLOSED"})
    handler({"ticket_id": str(ticket_id), "kind": "RESOLVED"})
    handler({"ticket_id": str(ticket_id), "kind": "CLOSED"})

    with session_factory() as db:
        notifications = db.scalars(select(Notification).where(
            Notification.resource_id == ticket_id,
        )).all()
        assert len(notifications) == 4
        assert {row.user_id for row in notifications} == {candidate_id, admin_id}
        assert {row.event_type for row in notifications} == {
            "SUPPORT_TICKET_RESOLVED", "SUPPORT_TICKET_CLOSED"
        }
