from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.infrastructure.db.models import (
    AdminGrant,
    AuditEvent,
    OutboxEvent,
    SupportMessage,
    SupportTicket,
    User,
)


class SupportConflict(ValueError):
    pass


def _audit(
    db: Session, *, actor_id: UUID, actor_role: str, action: str,
    ticket_id: UUID, details: dict | None = None,
    from_status: str | None = None, to_status: str | None = None,
) -> None:
    db.add(AuditEvent(
        event_type=action,
        resource_type="SUPPORT_TICKET",
        resource_id=ticket_id,
        actor_type=actor_role,
        actor_id=actor_id,
        from_status=from_status,
        to_status=to_status,
        details=details or {},
        occurred_at=datetime.now(timezone.utc),
    ))


def _outbox(db: Session, event_type: str, ticket_id: UUID, payload: dict) -> None:
    now = datetime.now(timezone.utc)
    common = {
        "aggregate_type": "SupportTicket",
        "aggregate_id": ticket_id,
        "payload": payload,
        "status": "PENDING",
        "attempt_count": 0,
        "available_at": now,
        "created_at": now,
    }
    if event_type == "SUPPORT_TICKET_CREATED":
        event = OutboxEvent(event_type="SUPPORT_TICKET_CREATED", **common)
    elif event_type == "SUPPORT_TICKET_STATUS_CHANGED":
        event = OutboxEvent(event_type="SUPPORT_TICKET_STATUS_CHANGED", **common)
    else:
        raise ValueError("unsupported_support_outbox_event_type")
    db.add(event)


def create_ticket(
    db: Session, candidate_user_id: UUID, *, idempotency_key: str, title: str,
    issue_details: str, follow_up_to_ticket_id: UUID | None = None,
) -> tuple[SupportTicket, bool]:
    existing = db.scalar(select(SupportTicket).where(
        SupportTicket.candidate_user_id == candidate_user_id,
        SupportTicket.idempotency_key == idempotency_key,
    ))
    if existing is not None:
        if (
            existing.title != title
            or existing.issue_details != issue_details
            or existing.follow_up_to_ticket_id != follow_up_to_ticket_id
        ):
            raise SupportConflict("idempotency_key_reused_with_different_ticket")
        return existing, False

    if follow_up_to_ticket_id is not None:
        previous = db.scalar(select(SupportTicket).where(
            SupportTicket.id == follow_up_to_ticket_id,
            SupportTicket.candidate_user_id == candidate_user_id,
        ))
        if previous is None:
            raise SupportConflict("follow_up_ticket_not_found")
        if previous.status not in {"RESOLVED", "CLOSED"}:
            raise SupportConflict("follow_up_requires_resolved_or_closed_ticket")

    now = datetime.now(timezone.utc)
    ticket = SupportTicket(
        candidate_user_id=candidate_user_id,
        idempotency_key=idempotency_key,
        title=title,
        issue_details=issue_details,
        status="OPEN",
        follow_up_to_ticket_id=follow_up_to_ticket_id,
        candidate_unread=False,
        admin_unread=True,
        created_at=now,
        updated_at=now,
    )
    db.add(ticket)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(SupportTicket).where(
            SupportTicket.candidate_user_id == candidate_user_id,
            SupportTicket.idempotency_key == idempotency_key,
        ))
        if existing is not None and existing.title == title and existing.issue_details == issue_details and existing.follow_up_to_ticket_id == follow_up_to_ticket_id:
            return existing, False
        raise SupportConflict("ticket_creation_conflict")

    db.add(SupportMessage(
        ticket_id=ticket.id,
        sender_user_id=candidate_user_id,
        sender_role="CANDIDATE",
        idempotency_key="initial",
        body=issue_details,
        delivery_status="SENT",
        created_at=now,
    ))
    _audit(db, actor_id=candidate_user_id, actor_role="CANDIDATE",
           action="SUPPORT_TICKET_CREATED", ticket_id=ticket.id,
           details={"follow_up_to_ticket_id": str(follow_up_to_ticket_id) if follow_up_to_ticket_id else None})
    _outbox(db, "SUPPORT_TICKET_CREATED", ticket.id, {"ticket_id": str(ticket.id), "kind": "CREATED"})
    db.commit()
    db.refresh(ticket)
    return ticket, True


def _lock_ticket(db: Session, ticket_id: UUID) -> SupportTicket:
    """Reload and lock a ticket before a lifecycle-sensitive mutation.

    populate_existing is important because callers commonly pass an ORM instance
    loaded before another transaction changed the ticket.
    """
    ticket = db.scalar(
        select(SupportTicket)
        .where(SupportTicket.id == ticket_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if ticket is None:
        raise SupportConflict("ticket_not_found")
    return ticket


def add_message(
    db: Session, ticket: SupportTicket, *, sender_id: UUID, sender_role: str,
    idempotency_key: str, body: str,
) -> tuple[SupportMessage, bool]:
    existing = db.scalar(select(SupportMessage).where(
        SupportMessage.ticket_id == ticket.id,
        SupportMessage.sender_user_id == sender_id,
        SupportMessage.idempotency_key == idempotency_key,
    ))
    if existing is not None:
        if existing.body != body:
            raise SupportConflict("idempotency_key_reused_with_different_message")
        # A retry after a lost response must return the original persisted message,
        # even if the ticket has since been resolved or closed.
        return existing, False
    ticket = _lock_ticket(db, ticket.id)
    if ticket.status in {"RESOLVED", "CLOSED"}:
        raise SupportConflict("ticket_is_resolved_or_closed")

    now = datetime.now(timezone.utc)
    message = SupportMessage(
        ticket_id=ticket.id,
        sender_user_id=sender_id,
        sender_role=sender_role,
        idempotency_key=idempotency_key,
        body=body,
        delivery_status="SENT",
        created_at=now,
    )
    db.add(message)
    ticket.updated_at = now
    if sender_role == "CANDIDATE":
        ticket.admin_unread = True
        ticket.candidate_unread = False
    else:
        ticket.candidate_unread = True
        ticket.admin_unread = False
    _audit(db, actor_id=sender_id, actor_role=sender_role,
           action="SUPPORT_MESSAGE_SENT", ticket_id=ticket.id,
           details={"message_idempotency_key": idempotency_key})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(SupportMessage).where(
            SupportMessage.ticket_id == ticket.id,
            SupportMessage.sender_user_id == sender_id,
            SupportMessage.idempotency_key == idempotency_key,
        ))
        if existing is not None and existing.body == body:
            return existing, False
        raise SupportConflict("message_creation_conflict")
    db.refresh(message)
    return message, True


def change_ticket_status(
    db: Session, ticket: SupportTicket, *, actor_id: UUID, actor_role: str, target_status: str,
) -> bool:
    ticket = _lock_ticket(db, ticket.id)
    if target_status not in {"OPEN", "RESOLVED", "CLOSED"}:
        raise SupportConflict("unsupported_ticket_status")
    if ticket.status == target_status:
        return False
    if ticket.status == "CLOSED":
        raise SupportConflict("closed_ticket_is_terminal")
    if target_status == "OPEN":
        raise SupportConflict("create_a_follow_up_ticket_instead_of_reopening")
    if target_status == "CLOSED" and ticket.status != "RESOLVED":
        raise SupportConflict("ticket_must_be_resolved_before_closure")

    previous = ticket.status
    now = datetime.now(timezone.utc)
    ticket.status = target_status
    ticket.updated_at = now
    if target_status == "RESOLVED":
        ticket.resolved_at = now
    if target_status == "CLOSED":
        ticket.closed_at = now
        if ticket.resolved_at is None:
            ticket.resolved_at = now
    if actor_role == "ADMIN":
        ticket.candidate_unread = True
        ticket.admin_unread = False
    else:
        ticket.admin_unread = True
        ticket.candidate_unread = False
    _audit(db, actor_id=actor_id, actor_role=actor_role,
           action="SUPPORT_TICKET_STATUS_CHANGED", ticket_id=ticket.id,
           from_status=previous, to_status=target_status)
    _outbox(db, "SUPPORT_TICKET_STATUS_CHANGED", ticket.id, {
        "ticket_id": str(ticket.id), "kind": target_status,
    })
    db.commit()
    db.refresh(ticket)
    return True


def mark_ticket_read(db: Session, ticket: SupportTicket, *, reader_role: str) -> None:
    now = datetime.now(timezone.utc)
    if reader_role == "ADMIN":
        ticket.admin_unread = False
        unread_role = "CANDIDATE"
    else:
        ticket.candidate_unread = False
        unread_role = "ADMIN"
    db.query(SupportMessage).filter(
        SupportMessage.ticket_id == ticket.id,
        SupportMessage.sender_role == unread_role,
        SupportMessage.read_at.is_(None),
    ).update({SupportMessage.read_at: now}, synchronize_session=False)
    db.commit()


def ticket_messages(db: Session, ticket_id: UUID) -> list[SupportMessage]:
    return list(db.scalars(select(SupportMessage).where(
        SupportMessage.ticket_id == ticket_id,
    ).order_by(SupportMessage.created_at, SupportMessage.id)).all())


def ticket_dict(ticket: SupportTicket, messages: list[SupportMessage] | None = None) -> dict:
    return {
        "id": str(ticket.id),
        "candidate_user_id": str(ticket.candidate_user_id),
        "assigned_admin_user_id": str(ticket.assigned_admin_user_id) if ticket.assigned_admin_user_id else None,
        "follow_up_to_ticket_id": str(ticket.follow_up_to_ticket_id) if ticket.follow_up_to_ticket_id else None,
        "title": ticket.title,
        "issue_details": ticket.issue_details,
        "status": ticket.status,
        "candidate_unread": ticket.candidate_unread,
        "admin_unread": ticket.admin_unread,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
        "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        "closed_at": ticket.closed_at.isoformat() if ticket.closed_at else None,
        "messages": [{
            "id": str(m.id),
            "sender_user_id": str(m.sender_user_id),
            "sender_role": m.sender_role,
            "body": m.body,
            "delivery_status": m.delivery_status,
            "created_at": m.created_at.isoformat(),
            "read_at": m.read_at.isoformat() if m.read_at else None,
        } for m in (messages or [])],
    }


def assign_ticket(
    db: Session, ticket: SupportTicket, *, actor_id: UUID,
    admin_user_id: UUID | None,
) -> bool:
    ticket = _lock_ticket(db, ticket.id)
    if admin_user_id is not None:
        grant = db.scalar(select(AdminGrant).join(
            User, User.id == AdminGrant.user_id
        ).where(
            AdminGrant.user_id == admin_user_id,
            AdminGrant.role == "ADMIN",
            AdminGrant.active.is_(True),
            User.status == "ACTIVE",
        ))
        if grant is None:
            raise SupportConflict("assigned_user_must_be_active_admin")
    if ticket.assigned_admin_user_id == admin_user_id:
        return False

    previous_admin_id = ticket.assigned_admin_user_id
    ticket.assigned_admin_user_id = admin_user_id
    ticket.updated_at = datetime.now(timezone.utc)
    ticket.admin_unread = True
    _audit(
        db, actor_id=actor_id, actor_role="ADMIN",
        action="SUPPORT_TICKET_ASSIGNED", ticket_id=ticket.id,
        details={
            "previous_admin_user_id": str(previous_admin_id) if previous_admin_id else None,
            "assigned_admin_user_id": str(admin_user_id) if admin_user_id else None,
        },
    )
    db.commit()
    db.refresh(ticket)
    return True
