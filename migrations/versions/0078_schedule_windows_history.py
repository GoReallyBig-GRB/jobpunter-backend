"""Add schedule run windows and append-only schedule history.

Revision ID: 0078_schedule_windows_history
Revises: 0077_discovery_alerts
"""
from alembic import op
import sqlalchemy as sa

revision = "0078_schedule_windows_history"
down_revision = "0077_discovery_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("discovery_schedules", sa.Column("window_start_minute", sa.Integer(), nullable=True))
    op.add_column("discovery_schedules", sa.Column("window_end_minute", sa.Integer(), nullable=True))
    op.add_column("discovery_schedules", sa.Column("window_timezone", sa.String(64), nullable=True))
    op.create_check_constraint(
        "ck_discovery_schedule_window", "discovery_schedules",
        "(window_start_minute IS NULL AND window_end_minute IS NULL AND window_timezone IS NULL) OR "
        "(window_start_minute BETWEEN 0 AND 1439 AND window_end_minute BETWEEN 0 AND 1439 "
        "AND window_start_minute <> window_end_minute AND window_timezone IS NOT NULL)",
    )
    op.create_table(
        "discovery_schedule_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("schedule_id", sa.Uuid(), sa.ForeignKey("discovery_schedules.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("schedule_revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_discovery_schedule_events_schedule_created", "discovery_schedule_events", ["schedule_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_discovery_schedule_events_schedule_created", table_name="discovery_schedule_events")
    op.drop_table("discovery_schedule_events")
    op.drop_constraint("ck_discovery_schedule_window", "discovery_schedules", type_="check")
    op.drop_column("discovery_schedules", "window_timezone")
    op.drop_column("discovery_schedules", "window_end_minute")
    op.drop_column("discovery_schedules", "window_start_minute")
