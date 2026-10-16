"""Add explicit daily and weekly candidate discovery schedules.

Revision ID: 0051_discovery_schedule_contract
Revises: 0050_normalized_job_provenance_url
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0051_discovery_schedule_contract"
down_revision = "0050_normalized_job_provenance_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_discovery_settings",
        sa.Column(
            "schedule_type",
            sa.String(length=16),
            nullable=False,
            server_default="INTERVAL",
        ),
    )
    op.add_column(
        "candidate_discovery_settings",
        sa.Column(
            "schedule_time",
            sa.String(length=5),
            nullable=False,
            server_default="09:00",
        ),
    )
    op.add_column(
        "candidate_discovery_settings",
        sa.Column(
            "schedule_timezone",
            sa.String(length=64),
            nullable=False,
            server_default="UTC",
        ),
    )
    op.add_column(
        "candidate_discovery_settings",
        sa.Column("schedule_weekday", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_discovery_settings_schedule_type",
        "candidate_discovery_settings",
        "schedule_type IN ('INTERVAL', 'DAILY', 'WEEKLY')",
    )
    op.create_check_constraint(
        "ck_discovery_settings_schedule_weekday",
        "candidate_discovery_settings",
        "(schedule_type = 'WEEKLY' AND schedule_weekday IS NOT NULL "
        "AND schedule_weekday BETWEEN 0 AND 6) "
        "OR (schedule_type IN ('INTERVAL', 'DAILY') AND schedule_weekday IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_discovery_settings_schedule_weekday",
        "candidate_discovery_settings",
        type_="check",
    )
    op.drop_constraint(
        "ck_discovery_settings_schedule_type",
        "candidate_discovery_settings",
        type_="check",
    )
    op.drop_column("candidate_discovery_settings", "schedule_weekday")
    op.drop_column("candidate_discovery_settings", "schedule_timezone")
    op.drop_column("candidate_discovery_settings", "schedule_time")
    op.drop_column("candidate_discovery_settings", "schedule_type")
