"""Allow AI execution telemetry for system-owned background tasks.

Revision ID: 0025_ai_system_execution_actor
Revises: 0024_ai_config_fingerprint
"""
from alembic import op
import sqlalchemy as sa

revision = "0025_ai_system_execution_actor"
down_revision = "0024_ai_config_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "ai_executions",
        "user_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )


def downgrade() -> None:
    connection = op.get_bind()
    system_executions = connection.scalar(
        sa.text("SELECT COUNT(*) FROM ai_executions WHERE user_id IS NULL")
    )
    if system_executions:
        raise RuntimeError(
            "Cannot make AI execution actor non-null while system-owned execution traces exist."
        )
    op.alter_column(
        "ai_executions",
        "user_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
