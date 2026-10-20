"""Persist safe integration health-check history.

Revision ID: 0062_integration_health_observations
Revises: 0061_immutable_audit_events
"""
from alembic import op
import sqlalchemy as sa

revision = "0062_integration_health_observations"
down_revision = "0061_immutable_audit_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_health_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("integration_key", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("check_type", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("checked_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("error_category", sa.String(length=96), nullable=True),
        sa.Column("details", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.CheckConstraint(
            "status IN ('HEALTHY', 'DEGRADED', 'FAILED', 'UNCONFIGURED', 'UNSUPPORTED')",
            name="ck_integration_health_observation_status",
        ),
        sa.CheckConstraint("duration_ms >= 0", name="ck_integration_health_observation_duration"),
        sa.ForeignKeyConstraint(["checked_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_integration_health_observations"),
    )
    op.create_index(
        "ix_integration_health_observation_key_time",
        "integration_health_observations",
        ["integration_key", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_integration_health_observation_key_time",
        table_name="integration_health_observations",
    )
    op.drop_table("integration_health_observations")
