"""Add structured candidate Master Career Profile sections.

Revision ID: 0031_candidate_profile_data
Revises: 0030_punt_qualification_history
"""
from alembic import op
import sqlalchemy as sa

revision = "0031_candidate_profile_data"
down_revision = "0030_punt_qualification_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "profile_data",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.alter_column("candidate_profiles", "profile_data", server_default=None)


def downgrade() -> None:
    op.drop_column("candidate_profiles", "profile_data")
