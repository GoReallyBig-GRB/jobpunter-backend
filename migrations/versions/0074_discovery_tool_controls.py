"""Add administrative activation controls for discovery tools.

Revision ID: 0074_discovery_tool_controls
Revises: 0073_smtp_delivery_provenance
"""
from alembic import op
import sqlalchemy as sa

revision = "0074_discovery_tool_controls"
down_revision = "0073_smtp_delivery_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_tool_controls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("tool_key", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tool_key", name="uq_discovery_tool_controls_tool_key"),
        sa.CheckConstraint("status IN ('ENABLED', 'DISABLED')", name="ck_discovery_tool_control_status"),
    )


def downgrade() -> None:
    op.drop_table("discovery_tool_controls")
