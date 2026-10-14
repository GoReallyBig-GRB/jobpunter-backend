"""Record whether a governed AI execution used its configured fallback.

Revision ID: 0035_ai_execution_fallback
Revises: 0034_application_lifecycle_audit
"""
from alembic import op
import sqlalchemy as sa

revision = "0035_ai_execution_fallback"
down_revision = "0034_application_lifecycle_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_executions",
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("ai_executions", "fallback_used")
