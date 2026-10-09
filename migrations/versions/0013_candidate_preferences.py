"""Add candidate-owned preferences separate from factual candidate evidence.

Revision ID: 0013_candidate_preferences
Revises: 0012_evidence_draft_default
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_candidate_preferences"
down_revision = "0012_evidence_draft_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_preferences",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "candidate_profile_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("preferences", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("candidate_profile_id", name="uq_candidate_preferences_profile"),
    )


def downgrade() -> None:
    op.drop_table("candidate_preferences")
