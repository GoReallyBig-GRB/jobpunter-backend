from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.application.notifications.service import _insert_notification
from app.infrastructure.db.models import AdminGrant, SupportTicket, User


def support_ticket_notification_handler(session_factory: sessionmaker):
    """Fan out persisted ticket lifecycle events idempotently to the candidate and active admins."""
    def handle(payload: dict) -> None:
        try:
            ticket_id = UUID(str(payload["ticket_id"]))
            kind = str(payload["kind"]).upper()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid_support_ticket_notification_payload") from exc
        if kind not in {"CREATED", "RESOLVED", "CLOSED", "MESSAGE", "ASSIGNED"}:
            raise ValueError("unsupported_support_ticket_notification_kind")
        if kind in {"MESSAGE", "ASSIGNED"}:
            _notify_targeted(session_factory, ticket_id, kind, payload)
            return

        with session_factory() as db:
            ticket = db.get(SupportTicket, ticket_id)
            if ticket is None:
                raise ValueError("support_ticket_not_found")
            candidate_id = ticket.candidate_user_id
            admin_ids = list(db.scalars(
                select(AdminGrant.user_id)
                .join(User, User.id == AdminGrant.user_id)
                .where(
                    AdminGrant.role == "ADMIN",
                    AdminGrant.active.is_(True),
                    User.status == "ACTIVE",
                )
                .order_by(AdminGrant.user_id)
            ).all())

        if kind == "CREATED":
            event_type = "SUPPORT_TICKET_CREATED"
            title = "New support ticket"
            message = "A new support ticket is available in JobPunter."
            event_key_suffix = "created"
            occurred_at = ticket.created_at
        elif kind == "RESOLVED":
            event_type = "SUPPORT_TICKET_RESOLVED"
            title = "Support ticket resolved"
            message = "A support ticket has been marked resolved."
            event_key_suffix = "resolved"
            occurred_at = ticket.resolved_at or ticket.updated_at
        else:
            event_type = "SUPPORT_TICKET_CLOSED"
            title = "Support ticket closed"
            message = "A support ticket has been closed."
            event_key_suffix = "closed"
            occurred_at = ticket.closed_at or ticket.updated_at

        recipients = list(dict.fromkeys([candidate_id, *admin_ids]))
        with session_factory() as db:
            for user_id in recipients:
                _insert_notification(db, {
                    "user_id": user_id,
                    "event_key": f"support-ticket:{ticket_id}:{event_key_suffix}:{user_id}",
                    "event_type": event_type,
                    "title": title,
                    "message": message,
                    "resource_type": "SUPPORT_TICKET",
                    "resource_id": ticket_id,
                    "created_at": occurred_at,
                    "read_at": None,
                })
    return handle


def _notify_targeted(session_factory: sessionmaker, ticket_id: UUID, kind: str, payload: dict) -> None:
    """Notify one named recipient for a message or assignment; never fan out."""
    from app.infrastructure.db.models import SupportMessage

    with session_factory() as db:
        ticket = db.get(SupportTicket, ticket_id)
        if ticket is None:
            raise ValueError("support_ticket_not_found")
        if kind == "ASSIGNED":
            try:
                recipient_id = UUID(str(payload["assigned_admin_user_id"]))
                change_key = str(UUID(str(payload["change_key"])))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid_support_ticket_notification_payload") from exc
            event_type, title = "SUPPORT_TICKET_ASSIGNED", "Support ticket assigned to you"
            message = "A support ticket has been assigned to you."
            event_key = f"support-ticket:{ticket_id}:assigned:{change_key}:{recipient_id}"
            occurred_at = ticket.updated_at
            must_be_active_admin = True
        else:
            try:
                message_id = UUID(str(payload["message_id"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid_support_ticket_notification_payload") from exc
            row = db.get(SupportMessage, message_id)
            if row is None or row.ticket_id != ticket_id:
                raise ValueError("support_message_not_found")
            if row.sender_role == "CANDIDATE":
                # Unassigned tickets rely on the admin unread queue, not a broadcast.
                recipient_id = ticket.assigned_admin_user_id
                must_be_active_admin = True
                title, message = "New candidate message", "A candidate replied on a support ticket."
            else:
                recipient_id = ticket.candidate_user_id
                must_be_active_admin = False
                title, message = "New support reply", "Support has replied to your ticket."
            event_type = "SUPPORT_TICKET_MESSAGE_ADDED"
            event_key = f"support-ticket:{ticket_id}:message:{message_id}:{recipient_id}"
            occurred_at = row.created_at
        if recipient_id is None:
            return
        if must_be_active_admin:
            admin_ok = db.scalar(select(AdminGrant.id).join(
                User, User.id == AdminGrant.user_id
            ).where(
                AdminGrant.user_id == recipient_id,
                AdminGrant.role == "ADMIN",
                AdminGrant.active.is_(True),
                User.status == "ACTIVE",
            ))
            if admin_ok is None:
                return

    with session_factory() as db:
        _insert_notification(db, {
            "user_id": recipient_id,
            "event_key": event_key,
            "event_type": event_type,
            "title": title,
            "message": message,
            "resource_type": "SUPPORT_TICKET",
            "resource_id": ticket_id,
            "created_at": occurred_at,
            "read_at": None,
        })
