import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.application.support.notifications import support_ticket_notification_handler
from app.application.support.service import (
    add_message,
    assign_ticket,
    change_ticket_status,
)
from app.infrastructure.db.models import (
    AdminGrant,
    Notification,
    OutboxEvent,
    SupportMessage,
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
    connection.begin()
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


def _user(db, name, *, status="ACTIVE", admin=False):
    user = User(
        display_name=name, email=f"{uuid4()}@example.test", email_verified=True, status=status
    )
    db.add(user)
    db.flush()
    if admin:
        db.add(AdminGrant(
            user_id=user.id, role="ADMIN", active=True, created_at=datetime.now(timezone.utc)
        ))
        db.flush()
    return user


def _ticket(db, candidate, *, status="OPEN"):
    now = datetime.now(timezone.utc)
    ticket = SupportTicket(
        candidate_user_id=candidate.id,
        idempotency_key=f"events-{uuid4()}",
        title="Event ticket",
        issue_details="Exercise notification events.",
        status=status,
        candidate_unread=False,
        admin_unread=True,
        created_at=now,
        updated_at=now,
    )
    db.add(ticket)
    db.commit()
    return ticket


def _events(db, ticket_id, event_type):
    return db.scalars(select(OutboxEvent).where(
        OutboxEvent.aggregate_type == "SupportTicket",
        OutboxEvent.aggregate_id == ticket_id,
        OutboxEvent.event_type == event_type,
    ).order_by(OutboxEvent.created_at)).all()


def _notifications(db, ticket_id, event_type=None):
    query = select(Notification).where(Notification.resource_id == ticket_id)
    if event_type:
        query = query.where(Notification.event_type == event_type)
    return db.scalars(query).all()


def test_admin_message_notifies_only_the_candidate_and_is_idempotent(session_factory):
    with session_factory() as db:
        candidate = _user(db, "Msg candidate")
        admin = _user(db, "Msg admin", admin=True)
        other_admin = _user(db, "Other admin", admin=True)
        ticket = _ticket(db, candidate)
        message, created = add_message(
            db, ticket, sender_id=admin.id, sender_role="ADMIN",
            idempotency_key="reply-1", body="We are looking into it.",
        )
        assert created
        # A lost-response retry returns the original and must not emit a second event.
        _again, created_again = add_message(
            db, ticket, sender_id=admin.id, sender_role="ADMIN",
            idempotency_key="reply-1", body="We are looking into it.",
        )
        assert not created_again
        events = _events(db, ticket.id, "SUPPORT_TICKET_MESSAGE_ADDED")
        assert len(events) == 1
        payload = events[0].payload
        assert payload["message_id"] == str(message.id)

    handler = support_ticket_notification_handler(session_factory)
    handler(payload)
    handler(payload)
    with session_factory() as db:
        rows = _notifications(db, ticket.id, "SUPPORT_TICKET_MESSAGE_ADDED")
        assert [row.user_id for row in rows] == [candidate.id]
        assert other_admin.id not in {row.user_id for row in rows}


def test_candidate_message_notifies_only_the_assigned_active_admin(session_factory):
    with session_factory() as db:
        candidate = _user(db, "Reply candidate")
        admin = _user(db, "Assigned admin", admin=True)
        _user(db, "Bystander admin", admin=True)
        ticket = _ticket(db, candidate)
        assign_ticket(db, ticket, actor_id=_user(db, "Assigner admin", admin=True).id,
                      admin_user_id=admin.id)
        message, _ = add_message(
            db, ticket, sender_id=candidate.id, sender_role="CANDIDATE",
            idempotency_key="cand-1", body="Any update?",
        )
        payload = _events(db, ticket.id, "SUPPORT_TICKET_MESSAGE_ADDED")[0].payload
        assert payload["message_id"] == str(message.id)

    handler = support_ticket_notification_handler(session_factory)
    handler(payload)
    handler(payload)
    with session_factory() as db:
        rows = _notifications(db, ticket.id, "SUPPORT_TICKET_MESSAGE_ADDED")
        assert [row.user_id for row in rows] == [admin.id]


def test_candidate_message_on_unassigned_ticket_sends_no_notification(session_factory):
    with session_factory() as db:
        candidate = _user(db, "Unassigned candidate")
        _user(db, "Unassigned admin", admin=True)
        ticket = _ticket(db, candidate)
        add_message(
            db, ticket, sender_id=candidate.id, sender_role="CANDIDATE",
            idempotency_key="cand-unassigned", body="Hello?",
        )
        payload = _events(db, ticket.id, "SUPPORT_TICKET_MESSAGE_ADDED")[0].payload

    support_ticket_notification_handler(session_factory)(payload)
    with session_factory() as db:
        assert _notifications(db, ticket.id, "SUPPORT_TICKET_MESSAGE_ADDED") == []
        assert db.get(SupportTicket, ticket.id).admin_unread is True


def test_candidate_message_to_a_suspended_assignee_sends_no_notification(session_factory):
    with session_factory() as db:
        candidate = _user(db, "Suspended assignee candidate")
        admin = _user(db, "Soon suspended admin", admin=True)
        ticket = _ticket(db, candidate)
        assign_ticket(db, ticket, actor_id=_user(db, "Assigner", admin=True).id,
                      admin_user_id=admin.id)
        add_message(
            db, ticket, sender_id=candidate.id, sender_role="CANDIDATE",
            idempotency_key="cand-suspended", body="Still there?",
        )
        payload = _events(db, ticket.id, "SUPPORT_TICKET_MESSAGE_ADDED")[0].payload
        admin.status = "SUSPENDED"
        db.commit()

    support_ticket_notification_handler(session_factory)(payload)
    with session_factory() as db:
        assert _notifications(db, ticket.id, "SUPPORT_TICKET_MESSAGE_ADDED") == []


def test_assignment_notifies_only_the_new_assignee_and_reassignment_is_distinct(session_factory):
    with session_factory() as db:
        candidate = _user(db, "Assign candidate")
        actor = _user(db, "Assigning admin", admin=True)
        first = _user(db, "First assignee", admin=True)
        second = _user(db, "Second assignee", admin=True)
        ticket = _ticket(db, candidate)
        assert assign_ticket(db, ticket, actor_id=actor.id, admin_user_id=first.id)
        assert not assign_ticket(db, ticket, actor_id=actor.id, admin_user_id=first.id)
        assert assign_ticket(db, ticket, actor_id=actor.id, admin_user_id=second.id)
        assert assign_ticket(db, ticket, actor_id=actor.id, admin_user_id=first.id)
        events = _events(db, ticket.id, "SUPPORT_TICKET_ASSIGNED")
        # The no-op repeat emitted nothing; A -> B -> A produced three distinct events.
        assert len(events) == 3
        payloads = [event.payload for event in events]

    handler = support_ticket_notification_handler(session_factory)
    for payload in payloads:
        handler(payload)
        handler(payload)
    with session_factory() as db:
        rows = _notifications(db, ticket.id, "SUPPORT_TICKET_ASSIGNED")
        assert sorted(str(row.user_id) for row in rows) == sorted(
            [str(first.id), str(second.id), str(first.id)]
        )
        assert candidate.id not in {row.user_id for row in rows}


def test_self_assignment_and_unassignment_send_nothing(session_factory):
    with session_factory() as db:
        candidate = _user(db, "Self assign candidate")
        admin = _user(db, "Self assigning admin", admin=True)
        ticket = _ticket(db, candidate)
        assert assign_ticket(db, ticket, actor_id=admin.id, admin_user_id=admin.id)
        assert assign_ticket(db, ticket, actor_id=admin.id, admin_user_id=None)
        assert _events(db, ticket.id, "SUPPORT_TICKET_ASSIGNED") == []


def test_notification_event_policy_accepts_the_delivered_support_event_types():
    from app.api.v1.admin import SMTPProviderDraftRequest

    fields = SMTPProviderDraftRequest.model_fields
    assert "event_policy" in fields
    policy = {
        "SUPPORT_TICKET_RESOLVED": False,
        "SUPPORT_TICKET_CLOSED": False,
        "SUPPORT_TICKET_MESSAGE_ADDED": True,
        "SUPPORT_TICKET_ASSIGNED": True,
    }
    validated = SMTPProviderDraftRequest.model_validate({
        "provider_name": "Policy provider",
        "host": "smtp.example.test",
        "port": 587,
        "username": "user",
        "password": "not-a-real-secret",
        "from_email": "noreply@example.test",
        "use_tls": True,
        "use_ssl": False,
        "event_policy": policy,
    })
    assert validated.event_policy == policy


def test_concurrent_resolve_requests_apply_once_with_one_event():
    """Two real sessions racing to resolve one ticket must serialize on the row lock."""
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=6)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    barrier = Barrier(2)
    created = {}
    try:
        with SessionLocal() as db:
            admin = _user(db, "Race admin")  # no grant: the service does not read it, and committed grants would leak
            candidate = _user(db, "Race candidate")
            ticket = _ticket(db, candidate)
            created.update(ticket_id=ticket.id, admin_id=admin.id, candidate_id=candidate.id)

        def resolve():
            with SessionLocal() as session:
                stale = session.get(SupportTicket, created["ticket_id"])
                barrier.wait(timeout=10)
                return change_ticket_status(
                    session, stale, actor_id=created["admin_id"], actor_role="ADMIN",
                    target_status="RESOLVED",
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [future.result(timeout=30) for future in [
                executor.submit(resolve), executor.submit(resolve)
            ]]

        assert sorted(results) == [False, True]
        with SessionLocal() as verify:
            ticket = verify.get(SupportTicket, created["ticket_id"])
            assert ticket.status == "RESOLVED" and ticket.resolved_at is not None
            assert len(_events(verify, ticket.id, "SUPPORT_TICKET_STATUS_CHANGED")) == 1
    finally:
        # Best-effort cleanup; append-only audit rows may legitimately refuse deletion.
        try:
            with SessionLocal() as cleanup:
                ticket_id = created.get("ticket_id")
                if ticket_id:
                    cleanup.execute(delete(OutboxEvent).where(OutboxEvent.aggregate_id == ticket_id))
                    cleanup.execute(delete(SupportMessage).where(SupportMessage.ticket_id == ticket_id))
                    cleanup.execute(delete(SupportTicket).where(SupportTicket.id == ticket_id))
                    cleanup.commit()
        except Exception:
            pass
        engine.dispose()


def test_concurrent_close_cannot_precede_resolution():
    """A close racing a resolve must never leave a CLOSED ticket without a resolution."""
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=6)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    barrier = Barrier(2)
    created = {}
    try:
        with SessionLocal() as db:
            admin = _user(db, "Race close admin")
            candidate = _user(db, "Race close candidate")
            ticket = _ticket(db, candidate)
            created.update(ticket_id=ticket.id, admin_id=admin.id)

        def transition(target):
            from app.application.support.service import SupportConflict

            with SessionLocal() as session:
                stale = session.get(SupportTicket, created["ticket_id"])
                barrier.wait(timeout=10)
                try:
                    return change_ticket_status(
                        session, stale, actor_id=created["admin_id"], actor_role="ADMIN",
                        target_status=target,
                    )
                except SupportConflict as exc:
                    return str(exc)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(transition, "CLOSED"), executor.submit(transition, "RESOLVED")]
            outcomes = [future.result(timeout=30) for future in futures]

        with SessionLocal() as verify:
            ticket = verify.get(SupportTicket, created["ticket_id"])
            if ticket.status == "CLOSED":
                # Only valid if the resolve was serialized first.
                assert ticket.resolved_at is not None
                assert ticket.closed_at is not None and ticket.closed_at >= ticket.resolved_at
            else:
                assert ticket.status == "RESOLVED"
                assert "ticket_must_be_resolved_before_closure" in outcomes
    finally:
        try:
            with SessionLocal() as cleanup:
                ticket_id = created.get("ticket_id")
                if ticket_id:
                    cleanup.execute(delete(OutboxEvent).where(OutboxEvent.aggregate_id == ticket_id))
                    cleanup.execute(delete(SupportTicket).where(SupportTicket.id == ticket_id))
                    cleanup.commit()
        except Exception:
            pass
        engine.dispose()
