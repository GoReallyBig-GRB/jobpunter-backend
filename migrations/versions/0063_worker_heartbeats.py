"""Persist worker liveness and safe cycle metrics.

Revision ID: 0063_worker_heartbeats
Revises: 0062_integration_health_observations
"""
from alembic import op
import sqlalchemy as sa

revision = "0063_worker_heartbeats"
down_revision = "0062_integration_health_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("worker_name", sa.String(length=64), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_category", sa.String(length=96), nullable=True),
        sa.Column("metrics", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'IDLE', 'STOPPED', 'FAILED')",
            name="ck_worker_heartbeat_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_worker_heartbeats"),
        sa.UniqueConstraint("worker_name", "instance_id", name="uq_worker_heartbeat_instance"),
    )
    op.create_index(
        "ix_worker_heartbeats_name_seen", "worker_heartbeats", ["worker_name", "last_seen_at"]
    )
    op.create_index("ix_worker_heartbeats_seen", "worker_heartbeats", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_worker_heartbeats_seen", table_name="worker_heartbeats")
    op.drop_index("ix_worker_heartbeats_name_seen", table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")
