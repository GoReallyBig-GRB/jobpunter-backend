"""Add candidate saved-job bookmarks.

Revision ID: 0072_saved_jobs
Revises: 0071_smtp_provider_configurations
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0072_saved_jobs"
down_revision = "0071_smtp_provider_configurations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("candidate_profile_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("candidate_profile_id", "job_id", name="uq_saved_job_candidate_job"),
    )
    op.create_index(
        "ix_saved_jobs_candidate_saved_at", "saved_jobs", ["candidate_profile_id", "saved_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_saved_jobs_candidate_saved_at", table_name="saved_jobs")
    op.drop_table("saved_jobs")
