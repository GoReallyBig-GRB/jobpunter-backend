"""Persist discovery-target validation outcome.

Revision ID: 0066_discovery_target_validation_outcome
Revises: 0065_discovery_target_block_history
"""
from alembic import op
import sqlalchemy as sa

revision = "0066_discovery_target_validation_outcome"
down_revision = "0065_discovery_target_block_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "discovery_targets",
        sa.Column("validation_status", sa.String(length=24), server_default="VALIDATED", nullable=False),
    )
    op.add_column(
        "discovery_targets",
        sa.Column("validation_error_category", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "discovery_targets",
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE discovery_targets SET validated_at = created_at WHERE validated_at IS NULL")
    op.alter_column("discovery_targets", "validated_at", nullable=False)
    op.create_check_constraint(
        "ck_discovery_target_validation_status",
        "discovery_targets",
        "validation_status IN ('VALIDATED', 'BLOCKED')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_discovery_target_validation_status",
        "discovery_targets",
        type_="check",
    )
    op.drop_column("discovery_targets", "validated_at")
    op.drop_column("discovery_targets", "validation_error_category")
    op.drop_column("discovery_targets", "validation_status")
