"""Record failed as well as successful attempts to use an AI fallback.

Revision ID: 0037_ai_fallback_attempt
Revises: 0036_immutable_governance_config
"""
from alembic import op
import sqlalchemy as sa

revision = "0037_ai_fallback_attempt"
down_revision = "0036_immutable_governance_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_executions",
        sa.Column("fallback_attempted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("ai_executions", "fallback_attempted")
