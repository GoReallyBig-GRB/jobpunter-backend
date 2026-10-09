"""Add immutable audit events for human job requirement review.

Revision ID: 0011_job_requirement_review_events
Revises: 0010_job_requirement_extraction
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_req_review_events"
down_revision = "0010_job_requirement_extraction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_requirement_review_events",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "job_requirement_id",
            sa.Uuid(),
            sa.ForeignKey("job_requirements.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "reviewer_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("previous_status", sa.String(32), nullable=False),
        sa.Column("resulting_status", sa.String(32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_job_requirement_review_events_job_requirement_id",
        "job_requirement_review_events",
        ["job_requirement_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_job_requirement_review_events_job_requirement_id",
        table_name="job_requirement_review_events",
    )
    op.drop_table("job_requirement_review_events")
