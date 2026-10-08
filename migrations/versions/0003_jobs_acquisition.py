"""Canonical jobs and acquisition pipeline tables.

Revision ID: 0003_jobs_acquisition
Revises: 0002_candidate_evidence
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_jobs_acquisition"
down_revision = "0002_candidate_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = sa.Uuid()

    op.create_table(
        "sources",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=True),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "source_connectors",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("source_id", uuid, sa.ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("connector_type", sa.String(64), nullable=False),
        sa.Column("connector_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=True),
        sa.Column("limits", sa.JSON(), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_id", "connector_type", "connector_version",
            name="uq_source_connector_version",
        ),
    )
    op.create_table(
        "acquisition_runs",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("source_id", uuid, sa.ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "source_connector_id",
            uuid,
            sa.ForeignKey("source_connectors.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("run_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "run_key", name="uq_acquisition_run_source_key"),
    )
    op.create_table(
        "raw_source_records",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column(
            "acquisition_run_id",
            uuid,
            sa.ForeignKey("acquisition_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_id", uuid, sa.ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("external_record_id", sa.String(500), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("raw_content", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "external_record_id", name="uq_raw_source_external_id"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("identity_status", sa.String(32), nullable=False),
        sa.Column("lifecycle_status", sa.String(32), nullable=False),
        sa.Column("freshness_status", sa.String(32), nullable=False),
        sa.Column("canonical_title", sa.String(500), nullable=False),
        sa.Column("company_name", sa.String(500), nullable=True),
        sa.Column("employment_type", sa.String(64), nullable=True),
        sa.Column("salary_min", sa.Numeric(18, 2), nullable=True),
        sa.Column("salary_max", sa.Numeric(18, 2), nullable=True),
        sa.Column("salary_currency", sa.String(16), nullable=True),
        sa.Column("salary_period", sa.String(32), nullable=True),
        sa.Column("salary_type", sa.String(32), nullable=True),
        sa.Column("salary_confidence", sa.Float(), nullable=True),
        sa.Column("disclosure_status", sa.String(32), nullable=True),
        sa.Column("original_salary_text", sa.Text(), nullable=True),
        sa.Column("location_country", sa.String(128), nullable=True),
        sa.Column("location_region", sa.String(128), nullable=True),
        sa.Column("location_city", sa.String(128), nullable=True),
        sa.Column("work_mode", sa.String(32), nullable=True),
        sa.Column("remote_eligibility", sa.String(64), nullable=True),
        sa.Column("location_restrictions", sa.JSON(), nullable=False),
        sa.Column("original_location_text", sa.Text(), nullable=True),
        sa.Column("application_url", sa.Text(), nullable=True),
        sa.Column("first_discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "job_resolutions",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column(
            "raw_source_record_id",
            uuid,
            sa.ForeignKey("raw_source_records.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("job_id", uuid, sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("resolution_type", sa.String(32), nullable=False),
        sa.Column("resolution_method", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("resolver_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("related_resolution_id", uuid, sa.ForeignKey("job_resolutions.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "job_provenance",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("job_id", uuid, sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_id", uuid, sa.ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("raw_source_record_id", uuid, sa.ForeignKey("raw_source_records.id"), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("observation_type", sa.String(64), nullable=False),
        sa.Column("asserted_by", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "job_description_versions",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("job_id", uuid, sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("source_record_id", uuid, sa.ForeignKey("raw_source_records.id"), nullable=True),
        sa.Column("completeness_status", sa.String(32), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "version_number", name="uq_jd_job_version"),
    )
    op.create_table(
        "job_requirements",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column(
            "job_description_version_id",
            uuid,
            sa.ForeignKey("job_description_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("requirement_type", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("importance", sa.String(32), nullable=False),
        sa.Column("hard_constraint", sa.Boolean(), nullable=False),
        sa.Column("extraction_status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "job_requirements",
        "job_description_versions",
        "job_provenance",
        "job_resolutions",
        "jobs",
        "raw_source_records",
        "acquisition_runs",
        "source_connectors",
        "sources",
    ):
        op.drop_table(table)
