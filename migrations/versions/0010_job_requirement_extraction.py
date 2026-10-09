"""Add auditable job-requirement extraction state and source provenance.

Revision ID: 0010_job_requirement_extraction
Revises: 0009_job_verification_attempts
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_job_requirement_extraction"
down_revision = "0009_job_verification_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_description_versions",
        sa.Column(
            "requirement_extraction_status",
            sa.String(32),
            nullable=False,
            server_default="PENDING",
        ),
    )
    op.alter_column(
        "job_description_versions",
        "requirement_extraction_status",
        server_default=None,
    )
    op.add_column(
        "job_description_versions",
        sa.Column(
            "requirement_extraction_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.alter_column(
        "job_description_versions",
        "requirement_extraction_attempts",
        server_default=None,
    )
    op.add_column(
        "job_description_versions",
        sa.Column("requirement_extraction_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_description_versions",
        sa.Column("requirement_extraction_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_description_versions",
        sa.Column("requirement_extraction_error", sa.String(64), nullable=True),
    )
    op.add_column(
        "job_description_versions",
        sa.Column("requirement_extraction_version", sa.String(64), nullable=True),
    )
    op.add_column(
        "job_description_versions",
        sa.Column("requirements_extracted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_job_description_requirement_extraction",
        "job_description_versions",
        ["requirement_extraction_status", "requirement_extraction_retry_at"],
    )

    op.add_column("job_requirements", sa.Column("source_excerpt", sa.Text(), nullable=True))
    op.add_column("job_requirements", sa.Column("extraction_confidence", sa.Float(), nullable=True))
    op.add_column("job_requirements", sa.Column("extraction_version", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("job_requirements", "extraction_version")
    op.drop_column("job_requirements", "extraction_confidence")
    op.drop_column("job_requirements", "source_excerpt")
    op.drop_index(
        "ix_job_description_requirement_extraction",
        table_name="job_description_versions",
    )
    op.drop_column("job_description_versions", "requirements_extracted_at")
    op.drop_column("job_description_versions", "requirement_extraction_version")
    op.drop_column("job_description_versions", "requirement_extraction_error")
    op.drop_column("job_description_versions", "requirement_extraction_retry_at")
    op.drop_column("job_description_versions", "requirement_extraction_started_at")
    op.drop_column("job_description_versions", "requirement_extraction_attempts")
    op.drop_column("job_description_versions", "requirement_extraction_status")
