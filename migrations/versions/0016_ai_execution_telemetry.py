"""Persist privacy-minimal AI execution telemetry.
Revision ID: 0016_ai_execution_telemetry
Revises: 0015_admin_governance_foundation
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_ai_execution_telemetry"
down_revision = "0015_admin_governance_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_executions",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("task", sa.String(80), nullable=False),
        sa.Column("config_version_id", sa.Uuid(), sa.ForeignKey("governance_config_versions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="RUNNING"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ai_executions_user_created", "ai_executions", ["user_id", "created_at"])
    op.create_index("ix_ai_executions_task_created", "ai_executions", ["task", "created_at"])
    op.create_index("ix_ai_executions_status_created", "ai_executions", ["status", "created_at"])
    op.alter_column("ai_executions", "status", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_ai_executions_status_created", table_name="ai_executions")
    op.drop_index("ix_ai_executions_task_created", table_name="ai_executions")
    op.drop_index("ix_ai_executions_user_created", table_name="ai_executions")
    op.drop_table("ai_executions")
