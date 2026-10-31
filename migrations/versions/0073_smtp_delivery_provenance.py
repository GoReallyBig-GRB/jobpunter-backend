"""Track effective SMTP provider version and fallback delivery.

Revision ID: 0073_smtp_delivery_provenance
Revises: 0072_saved_jobs
"""
from alembic import op
import sqlalchemy as sa

revision = "0073_smtp_delivery_provenance"
down_revision = "0072_saved_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notification_email_deliveries", sa.Column("smtp_config_version", sa.Integer(), nullable=True))
    op.add_column(
        "notification_email_deliveries",
        sa.Column("smtp_fallback_used", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("notification_email_deliveries", "smtp_fallback_used")
    op.drop_column("notification_email_deliveries", "smtp_config_version")
