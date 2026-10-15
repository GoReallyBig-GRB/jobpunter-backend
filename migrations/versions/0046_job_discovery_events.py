"""Persist source observations for every acquisition result.

Revision ID: 0046_job_discovery_events
Revises: 0045_career_pathways
"""
from alembic import op
import sqlalchemy as sa

revision = "0046_job_discovery_events"
down_revision = "0045_career_pathways"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_discovery_events",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("acquisition_run_id", sa.Uuid(), nullable=False),
        sa.Column("raw_source_record_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_job_id", sa.Uuid(), nullable=True),
        sa.Column("external_record_id", sa.String(length=500), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_outcome", sa.String(length=32), nullable=False),
        sa.Column("processing_status", sa.String(length=32), nullable=False),
        sa.Column("processing_error", sa.String(length=64), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "observation_outcome IN ('NEW', 'CHANGED', 'UNCHANGED')",
            name="ck_job_discovery_events_outcome",
        ),
        sa.CheckConstraint(
            "processing_status IN ('PENDING', 'CANONICALIZED', 'REQUIRES_REVIEW', 'FAILED')",
            name="ck_job_discovery_events_processing_status",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["acquisition_run_id"], ["acquisition_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["raw_source_record_id"], ["raw_source_records.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["canonical_job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_job_discovery_events"),
    )
    op.create_index(
        "ix_job_discovery_events_source_observed",
        "job_discovery_events", ["source_id", "observed_at"], unique=False,
    )
    op.create_index(
        "ix_job_discovery_events_run",
        "job_discovery_events", ["acquisition_run_id", "created_at"], unique=False,
    )
    op.create_index(
        "ix_job_discovery_events_raw_observed",
        "job_discovery_events", ["raw_source_record_id", "observed_at"], unique=False,
    )
    op.create_index(
        "ix_job_discovery_events_job_observed",
        "job_discovery_events", ["canonical_job_id", "observed_at"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_job_discovery_events_job_observed", table_name="job_discovery_events")
    op.drop_index("ix_job_discovery_events_raw_observed", table_name="job_discovery_events")
    op.drop_index("ix_job_discovery_events_run", table_name="job_discovery_events")
    op.drop_index("ix_job_discovery_events_source_observed", table_name="job_discovery_events")
    op.drop_table("job_discovery_events")
