"""Create persistent support tickets and messages.

Revision ID: 0057_support_tickets
Revises: 0056_notification_email_delivery
"""
from alembic import op
import sqlalchemy as sa

revision = "0057_support_tickets"
down_revision = "0056_notification_email_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "support_tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_user_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_admin_user_id", sa.Uuid(), nullable=True),
        sa.Column("follow_up_to_ticket_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("issue_details", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="OPEN", nullable=False),
        sa.Column("candidate_unread", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("admin_unread", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["candidate_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_admin_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["follow_up_to_ticket_id"], ["support_tickets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_support_tickets"),
        sa.UniqueConstraint("candidate_user_id", "idempotency_key", name="uq_support_ticket_candidate_idempotency"),
    )
    op.create_index("ix_support_tickets_status_updated", "support_tickets", ["status", "updated_at"])
    op.create_index("ix_support_tickets_candidate_created", "support_tickets", ["candidate_user_id", "created_at"])
    op.create_index("ix_support_tickets_admin_unread", "support_tickets", ["admin_unread", "created_at"])
    op.create_table(
        "support_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("sender_user_id", sa.Uuid(), nullable=False),
        sa.Column("sender_role", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("delivery_status", sa.String(length=16), server_default="SENT", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["support_tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sender_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_support_messages"),
        sa.UniqueConstraint("ticket_id", "sender_user_id", "idempotency_key", name="uq_support_message_sender_idempotency"),
    )
    op.create_index("ix_support_messages_ticket_created", "support_messages", ["ticket_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_support_messages_ticket_created", table_name="support_messages")
    op.drop_table("support_messages")
    op.drop_index("ix_support_tickets_admin_unread", table_name="support_tickets")
    op.drop_index("ix_support_tickets_candidate_created", table_name="support_tickets")
    op.drop_index("ix_support_tickets_status_updated", table_name="support_tickets")
    op.drop_table("support_tickets")
