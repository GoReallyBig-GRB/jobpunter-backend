"""Use explicit source health lifecycle states.

Revision ID: 0067_source_health_states
Revises: 0066_discovery_target_validation_outcome
"""
from alembic import op
import sqlalchemy as sa

revision = "0067_source_health_states"
down_revision = "0066_discovery_target_validation_outcome"
branch_labels = None
depends_on = None

_OPERATIONAL_STATES = (
    "'CONFIGURED', 'CHECKED', 'HEALTHY', 'DEGRADED', 'RESTRICTED', 'FAILED', 'UNAVAILABLE'"
)


def upgrade() -> None:
    # "AVAILABLE" was the old success label. Existing rows with no observation/error
    # were defaulted to UNAVAILABLE and must not be mistaken for a failed health check.
    op.execute("UPDATE sources SET operational_state = 'HEALTHY' WHERE operational_state = 'AVAILABLE'")
    op.execute(
        "UPDATE sources SET operational_state = 'CONFIGURED' "
        "WHERE operational_state = 'UNAVAILABLE' "
        "AND last_observed_at IS NULL AND last_error_category IS NULL"
    )
    op.alter_column(
        "sources", "operational_state",
        existing_type=sa.String(length=32),
        server_default="CONFIGURED",
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_sources_operational_state",
        "sources",
        f"operational_state IN ({_OPERATIONAL_STATES})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sources_operational_state", "sources", type_="check")
    op.execute("UPDATE sources SET operational_state = 'AVAILABLE' WHERE operational_state = 'HEALTHY'")
    op.execute(
        "UPDATE sources SET operational_state = 'UNAVAILABLE' "
        "WHERE operational_state IN ('CONFIGURED', 'CHECKED')"
    )
    op.alter_column(
        "sources", "operational_state",
        existing_type=sa.String(length=32),
        server_default="UNAVAILABLE",
        existing_nullable=False,
    )
