"""Add tool/global run budgets and per-step effective configuration snapshots.

Revision ID: 0079_run_budgets_snapshots
Revises: 0078_schedule_windows_history
"""
from alembic import op
import sqlalchemy as sa

revision = "0079_run_budgets_snapshots"
down_revision = "0078_schedule_windows_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("discovery_tool_controls", sa.Column("max_runs", sa.Integer(), nullable=True))
    op.add_column("discovery_tool_controls", sa.Column("window_minutes", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_discovery_tool_control_budget", "discovery_tool_controls",
        "(max_runs IS NULL AND window_minutes IS NULL) OR "
        "(max_runs BETWEEN 1 AND 100000 AND window_minutes BETWEEN 1 AND 10080)",
    )
    op.add_column("discovery_mission_source_runs", sa.Column("config_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("discovery_mission_source_runs", "config_snapshot")
    op.drop_constraint("ck_discovery_tool_control_budget", "discovery_tool_controls", type_="check")
    op.drop_column("discovery_tool_controls", "window_minutes")
    op.drop_column("discovery_tool_controls", "max_runs")
