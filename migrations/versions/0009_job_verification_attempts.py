"""Persist auditable job application verification attempts.

Revision ID: 0009_job_verification_attempts
Revises: 0008_discovery_missions
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_job_verification_attempts"
down_revision = "0008_discovery_missions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_verification_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("checked_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("http_status_code", sa.Integer(), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("verifier_version", sa.String(64), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_job_verification_attempts_job_checked",
        "job_verification_attempts",
        ["job_id", "checked_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_job_verification_attempts_job_checked",
        table_name="job_verification_attempts",
    )
    op.drop_table("job_verification_attempts")
