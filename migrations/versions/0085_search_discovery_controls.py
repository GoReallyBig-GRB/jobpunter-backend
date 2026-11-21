"""Add administrator controls for search discovery: schedule overrides and added terms.

Revision ID: 0085_search_discovery_controls
Revises: 0084_tavily_discovery
"""

import sqlalchemy as sa
from alembic import op

revision = "0085_search_discovery_controls"
down_revision = "0084_tavily_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_discovery_settings",
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("daily_search_cap", sa.Integer(), nullable=True),
        sa.Column("min_minutes_between_searches", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "daily_search_cap IS NULL OR daily_search_cap BETWEEN 1 AND 48", name="ck_search_discovery_daily_cap",
        ),
        sa.CheckConstraint(
            "min_minutes_between_searches IS NULL OR min_minutes_between_searches BETWEEN 15 AND 1440",
            name="ck_search_discovery_spacing",
        ),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", name="uq_search_discovery_settings_provider"),
    )
    op.create_table(
        "search_discovery_terms",
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("text", sa.String(120), nullable=False),
        sa.Column("normalized_text", sa.String(120), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("kind IN ('COMPANY', 'QUERY', 'SECTOR', 'MARKET')", name="ck_search_discovery_term_kind"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "kind", "normalized_text", name="uq_search_discovery_term"),
    )


def downgrade() -> None:
    op.drop_table("search_discovery_terms")
    op.drop_table("search_discovery_settings")
