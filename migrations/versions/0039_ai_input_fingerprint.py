"""Persist a privacy-safe fingerprint of validated AI task inputs.

Revision ID: 0039_ai_input_fingerprint
Revises: 0038_ai_input_contract_trace
"""
from alembic import op
import sqlalchemy as sa

revision = "0039_ai_input_fingerprint"
down_revision = "0038_ai_input_contract_trace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_executions",
        sa.Column(
            "input_fingerprint",
            sa.String(length=64),
            nullable=False,
            server_default="legacy-unavailable",
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_executions", "input_fingerprint")
