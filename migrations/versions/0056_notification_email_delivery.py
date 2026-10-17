"""Persist opt-in email preferences and notification delivery state.

Revision ID: 0056_notification_email_delivery
Revises: 0055_candidate_notifications
"""
from alembic import op
import sqlalchemy as sa

revision = "0056_notification_email_delivery"
down_revision = "0055_candidate_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_email_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", name="pk_notification_email_preferences"),
    )
    op.create_table(
        "notification_email_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("notification_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(length=64), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_notification_email_deliveries"),
        sa.UniqueConstraint(
            "notification_id",
            name="uq_notification_email_delivery_notification",
        ),
    )
    op.create_index(
        "ix_notification_email_deliveries_status_updated",
        "notification_email_deliveries",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_email_deliveries_status_updated",
        table_name="notification_email_deliveries",
    )
    op.drop_table("notification_email_deliveries")
    op.drop_table("notification_email_preferences")
