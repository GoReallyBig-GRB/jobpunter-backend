"""Restore the PRD source operational-state vocabulary.

Revision ID: 0070_source_operational_state_contract
Revises: 0069_discovery_target_event_immutability
"""
from alembic import op
import sqlalchemy as sa

revision = "0070_source_operational_state_contract"
down_revision = "0069_discovery_target_event_immutability"
branch_labels = None
depends_on = None

_PRD_STATES = "'AVAILABLE', 'DEGRADED', 'RESTRICTED', 'FAILED', 'UNAVAILABLE'"
_EXTENDED_STATES = (
    "'CONFIGURED', 'CHECKED', 'HEALTHY', 'DEGRADED', "
    "'RESTRICTED', 'FAILED', 'UNAVAILABLE'"
)


def upgrade() -> None:
    op.drop_constraint("ck_sources_operational_state", "sources", type_="check")
    op.execute(
        "UPDATE sources SET operational_state = 'AVAILABLE' "
        "WHERE operational_state = 'HEALTHY'"
    )
    op.execute(
        "UPDATE sources SET operational_state = 'UNAVAILABLE' "
        "WHERE operational_state IN ('CONFIGURED', 'CHECKED')"
    )
    op.alter_column(
        "sources",
        "operational_state",
        existing_type=sa.String(length=32),
        server_default="UNAVAILABLE",
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_sources_operational_state",
        "sources",
        f"operational_state IN ({_PRD_STATES})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sources_operational_state", "sources", type_="check")
    op.execute(
        "UPDATE sources SET operational_state = 'HEALTHY' "
        "WHERE operational_state = 'AVAILABLE'"
    )
    op.execute(
        "UPDATE sources SET operational_state = 'CONFIGURED' "
        "WHERE operational_state = 'UNAVAILABLE' "
        "AND last_observed_at IS NULL AND last_error_category IS NULL"
    )
    op.alter_column(
        "sources",
        "operational_state",
        existing_type=sa.String(length=32),
        server_default="CONFIGURED",
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_sources_operational_state",
        "sources",
        f"operational_state IN ({_EXTENDED_STATES})",
    )
