"""Persist candidate set for unresolved job identity decisions.

Revision ID: 0005_job_resolution_candidates
Revises: 0004_raw_source_deduplication
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_job_resolution_candidates"
down_revision = "0004_raw_source_deduplication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("job_resolutions", "job_id", existing_type=sa.Uuid(), nullable=True)
    op.create_table(
        "job_resolution_candidates",
        sa.Column("resolution_id", sa.Uuid(), sa.ForeignKey("job_resolutions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("candidate_job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("method", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("job_resolution_candidates")
    op.alter_column("job_resolutions", "job_id", existing_type=sa.Uuid(), nullable=False)
