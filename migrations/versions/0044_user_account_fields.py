"""Add account contact, verification, onboarding, and last-login fields.

Revision ID: 0044_user_account_fields
Revises: 0043_document_ai_execution_ref
"""
from alembic import op
import sqlalchemy as sa

revision = "0044_user_account_fields"
down_revision = "0043_document_ai_execution_ref"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(320), nullable=True))
    op.add_column(
        "users",
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("users", sa.Column("photo_url", sa.String(2048), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "onboarding_status",
            sa.String(32),
            nullable=False,
            server_default="NOT_STARTED",
        ),
    )
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "onboarding_status")
    op.drop_column("users", "photo_url")
    op.drop_column("users", "email_verified")
    op.drop_column("users", "email")
