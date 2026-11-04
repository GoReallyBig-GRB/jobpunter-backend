"""Add durable discovery alerts.

Revision ID: 0079_discovery_alerts
Revises: 0078_source_refresh_schedules
"""
from alembic import op
import sqlalchemy as sa

revision = "0079_discovery_alerts"
down_revision = "0078_source_refresh_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_alerts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("alert_key", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=True),
        sa.Column("tool_key", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledgement_reason", sa.String(1000), nullable=True),
        sa.CheckConstraint("kind IN ('STALE_SOURCE','TOOL_FAILURE_RATE')", name="ck_discovery_alert_kind"),
        sa.CheckConstraint("status IN ('OPEN','ACKNOWLEDGED','RESOLVED')", name="ck_discovery_alert_status"),
    )
    op.create_index(
        "uq_discovery_alerts_unresolved_key", "discovery_alerts", ["alert_key"], unique=True,
        postgresql_where=sa.text("status <> 'RESOLVED'"),
    )
    op.create_index("ix_discovery_alerts_status_opened", "discovery_alerts", ["status", "opened_at"])


def downgrade() -> None:
    op.drop_index("ix_discovery_alerts_status_opened", table_name="discovery_alerts")
    op.drop_index("uq_discovery_alerts_unresolved_key", table_name="discovery_alerts")
    op.drop_table("discovery_alerts")
