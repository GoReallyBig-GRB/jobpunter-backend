"""Persist the input contract version on each AI execution.

Revision ID: 0038_ai_input_contract_trace
Revises: 0037_ai_fallback_attempt
"""
from alembic import op
import sqlalchemy as sa

revision = "0038_ai_input_contract_trace"
down_revision = "0037_ai_fallback_attempt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_executions",
        sa.Column("input_contract_version", sa.String(length=40), nullable=False, server_default="v2"),
    )


def downgrade() -> None:
    op.drop_column("ai_executions", "input_contract_version")
