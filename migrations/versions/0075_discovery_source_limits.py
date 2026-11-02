"""Add per-source acquisition run-rate limits.

Revision ID: 0075_discovery_source_limits
Revises: 0074_discovery_tool_controls
"""
from alembic import op
import sqlalchemy as sa

revision = "0075_discovery_source_limits"
down_revision = "0074_discovery_tool_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_source_limits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("max_runs", sa.Integer(), nullable=False),
        sa.Column("window_minutes", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", name="uq_discovery_source_limits_source_id"),
        sa.CheckConstraint("max_runs BETWEEN 1 AND 1000", name="ck_discovery_source_limit_max_runs"),
        sa.CheckConstraint("window_minutes BETWEEN 1 AND 10080", name="ck_discovery_source_limit_window"),
    )


def downgrade() -> None:
    op.drop_table("discovery_source_limits")
