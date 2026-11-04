"""Add administrative source refresh schedules and schedule-owned source runs.

Revision ID: 0078_source_refresh_schedules
Revises: 0077_discovery_source_limits
"""
from alembic import op
import sqlalchemy as sa

revision = "0078_source_refresh_schedules"
down_revision = "0077_discovery_source_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_refresh_schedules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_reason", sa.String(500), nullable=True),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", name="uq_source_refresh_schedules_source_id"),
        sa.CheckConstraint("status IN ('ACTIVE','PAUSED')", name="ck_source_refresh_schedule_status"),
        sa.CheckConstraint("interval_minutes BETWEEN 15 AND 10080", name="ck_source_refresh_schedule_interval"),
    )
    op.alter_column("discovery_mission_source_runs", "mission_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column(
        "discovery_mission_source_runs",
        sa.Column("schedule_id", sa.Uuid(), sa.ForeignKey("source_refresh_schedules.id", ondelete="RESTRICT"), nullable=True),
    )
    op.create_index("ix_mission_source_runs_schedule_id", "discovery_mission_source_runs", ["schedule_id"])
    op.create_check_constraint(
        "ck_mission_source_run_single_owner", "discovery_mission_source_runs",
        "(mission_id IS NOT NULL) <> (schedule_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_mission_source_run_single_owner", "discovery_mission_source_runs", type_="check")
    op.drop_index("ix_mission_source_runs_schedule_id", table_name="discovery_mission_source_runs")
    op.drop_column("discovery_mission_source_runs", "schedule_id")
    op.alter_column("discovery_mission_source_runs", "mission_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_table("source_refresh_schedules")
