from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKeyMixin


class SupportTicket(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "support_tickets"
    __table_args__ = (
        UniqueConstraint("candidate_user_id", "idempotency_key", name="uq_support_ticket_candidate_idempotency"),
        Index("ix_support_tickets_status_updated", "status", "updated_at"),
        Index("ix_support_tickets_candidate_created", "candidate_user_id", "created_at"),
        Index("ix_support_tickets_admin_unread", "admin_unread", "created_at"),
    )

    candidate_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    assigned_admin_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    follow_up_to_ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="RESTRICT")
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    issue_details: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="OPEN")
    candidate_unread: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    admin_unread: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupportMessage(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "support_messages"
    __table_args__ = (
        UniqueConstraint(
            "ticket_id", "sender_user_id", "idempotency_key",
            name="uq_support_message_sender_idempotency",
        ),
        Index("ix_support_messages_ticket_created", "ticket_id", "created_at"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False
    )
    sender_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    sender_role: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_status: Mapped[str] = mapped_column(String(16), nullable=False, default="SENT")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
