"""Persist explicit task and system policy versions on AI executions.

Revision ID: 0027_ai_policy_version_trace
Revises: 0026_gov_config_fingerprint
"""
from alembic import op
import sqlalchemy as sa

revision = "0027_ai_policy_version_trace"
down_revision = "0026_gov_config_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_executions",
        sa.Column("task_policy_version", sa.String(length=40), nullable=False, server_default="v1"),
    )
    op.add_column(
        "ai_executions",
        sa.Column("system_policy_version", sa.String(length=40), nullable=False, server_default="v1"),
    )


def downgrade() -> None:
    op.drop_column("ai_executions", "system_policy_version")
    op.drop_column("ai_executions", "task_policy_version")
