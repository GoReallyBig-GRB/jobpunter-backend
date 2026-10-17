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
    NotificationEmailPreference,
    OutboxEvent,
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
        db.add_all([
            NotificationEmailPreference(user_id=candidate.id, enabled=True, updated_at=datetime.now(timezone.utc)),
            NotificationEmailPreference(user_id=admin.id, enabled=True, updated_at=datetime.now(timezone.utc)),
        ])
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
        assert all(row.status == "PENDING" for row in deliveries)
        email_events = db.scalars(select(OutboxEvent).where(
            OutboxEvent.event_type == "NOTIFICATION_EMAIL_REQUESTED",
            OutboxEvent.aggregate_type == "Notification",
            OutboxEvent.aggregate_id.in_([row.id for row in notifications]),
        )).all()
        assert len(email_events) == 2


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



def test_ticket_and_message_idempotency_and_linked_follow_up(session_factory):
    from app.application.support.service import (
        SupportConflict, add_message, change_ticket_status, create_ticket,
    )

    with session_factory() as db:
        candidate = User(
            display_name="Idempotency candidate",
            email=f"{uuid4()}@example.test",
            email_verified=True,
            status="ACTIVE",
        )
        db.add(candidate)
        db.commit()
        candidate_id = candidate.id

        ticket, created = create_ticket(
            db, candidate_id, idempotency_key="ticket-create-0001",
            title="Account access", issue_details="Cannot sign in.",
        )
        assert created is True
        ticket_id = ticket.id

        replay, created_again = create_ticket(
            db, candidate_id, idempotency_key="ticket-create-0001",
            title="Account access", issue_details="Cannot sign in.",
        )
        assert created_again is False
        assert replay.id == ticket_id

        message, message_created = add_message(
            db, ticket, sender_id=candidate_id, sender_role="CANDIDATE",
            idempotency_key="candidate-reply-0001", body="Still cannot sign in.",
        )
        assert message_created is True
        duplicate, duplicate_created = add_message(
            db, ticket, sender_id=candidate_id, sender_role="CANDIDATE",
            idempotency_key="candidate-reply-0001", body="Still cannot sign in.",
        )
        assert duplicate_created is False
        assert duplicate.id == message.id

        with pytest.raises(SupportConflict, match="idempotency_key_reused_with_different_message"):
            add_message(
                db, ticket, sender_id=candidate_id, sender_role="CANDIDATE",
                idempotency_key="candidate-reply-0001", body="Different body.",
            )

        change_ticket_status(
            db, ticket, actor_id=candidate_id, actor_role="CANDIDATE",
            target_status="RESOLVED",
        )
        follow_up, follow_up_created = create_ticket(
            db, candidate_id, idempotency_key="ticket-create-0002",
            title="Account access again", issue_details="The issue returned.",
            follow_up_to_ticket_id=ticket_id,
        )
        assert follow_up_created is True
        assert follow_up.follow_up_to_ticket_id == ticket_id
        assert follow_up.id != ticket_id
        ticket_events = db.scalars(select(OutboxEvent).where(
            OutboxEvent.aggregate_type == "SupportTicket",
            OutboxEvent.aggregate_id == ticket_id,
        )).all()
        assert [event.event_type for event in ticket_events].count("SUPPORT_TICKET_CREATED") == 1
        assert [event.event_type for event in ticket_events].count("SUPPORT_TICKET_STATUS_CHANGED") == 1
        follow_up_events = db.scalars(select(OutboxEvent).where(
            OutboxEvent.aggregate_type == "SupportTicket",
            OutboxEvent.aggregate_id == follow_up.id,
        )).all()
        assert [event.event_type for event in follow_up_events].count("SUPPORT_TICKET_CREATED") == 1


def test_notification_retry_after_partial_fanout_does_not_duplicate(monkeypatch, session_factory):
    import app.application.support.notifications as notifications_module

    with session_factory() as db:
        candidate = User(
            display_name="Retry candidate",
            email=f"{uuid4()}@example.test",
            email_verified=True,
            status="ACTIVE",
        )
        admin = User(
            display_name="Retry admin",
            email=f"{uuid4()}@example.test",
            email_verified=True,
            status="ACTIVE",
        )
        db.add_all([candidate, admin])
        db.flush()
        db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True, created_at=datetime.now(timezone.utc)))
        ticket = SupportTicket(
            candidate_user_id=candidate.id,
            idempotency_key=f"retry-{uuid4()}",
            title="Partial notification retry",
            issue_details="Verify idempotent fanout.",
            status="OPEN",
            candidate_unread=False,
            admin_unread=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(ticket)
        db.commit()
        ticket_id = ticket.id

    real_insert = notifications_module._insert_notification
    state = {"calls": 0, "failed_once": False}

    def fail_once(db, values):
        state["calls"] += 1
        if state["calls"] == 2 and not state["failed_once"]:
            state["failed_once"] = True
            raise RuntimeError("simulated_partial_fanout_failure")
        return real_insert(db, values)

    monkeypatch.setattr(notifications_module, "_insert_notification", fail_once)
    handler = notifications_module.support_ticket_notification_handler(session_factory)
    payload = {"ticket_id": str(ticket_id), "kind": "CREATED"}
    with pytest.raises(RuntimeError, match="simulated_partial_fanout_failure"):
        handler(payload)
    handler(payload)

    with session_factory() as db:
        notifications = db.scalars(select(Notification).where(
            Notification.resource_id == ticket_id,
        )).all()
        assert len(notifications) == 2
        assert len({row.user_id for row in notifications}) == 2
