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
        if kind not in {"CREATED", "RESOLVED", "CLOSED"}:
            raise ValueError("unsupported_support_ticket_notification_kind")

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
        elif kind == "RESOLVED":
            event_type = "SUPPORT_TICKET_RESOLVED"
            title = "Support ticket resolved"
            message = "A support ticket has been marked resolved."
            event_key_suffix = "resolved"
        else:
            event_type = "SUPPORT_TICKET_CLOSED"
            title = "Support ticket closed"
            message = "A support ticket has been closed."
            event_key_suffix = "closed"

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
                    "created_at": ticket.updated_at,
                    "read_at": None,
                })
    return handle
