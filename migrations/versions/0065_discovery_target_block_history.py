"""Persist denied discovery-target lifecycle attempts in append-only history.

Revision ID: 0065_discovery_target_block_history
Revises: 0064_discovery_targets
"""
from alembic import op

revision = "0065_discovery_target_block_history"
down_revision = "0064_discovery_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_discovery_target_event_action",
        "discovery_target_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_discovery_target_event_action",
        "discovery_target_events",
        "action IN ('CREATED', 'ACTIVATED', 'PAUSED', 'RETIRED', 'BLOCKED')",
    )


def downgrade() -> None:
    # Never erase the meaning of already-persisted audit history during downgrade.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM discovery_target_events WHERE action = 'BLOCKED'
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade discovery-target block history while BLOCKED events exist';
            END IF;
        END
        $$;
    """)
    op.drop_constraint(
        "ck_discovery_target_event_action",
        "discovery_target_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_discovery_target_event_action",
        "discovery_target_events",
        "action IN ('CREATED', 'ACTIVATED', 'PAUSED', 'RETIRED')",
    )
