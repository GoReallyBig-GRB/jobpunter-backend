"""Persist the PRD requirement severity separately from extraction importance.

Revision ID: 0048_job_requirement_severity
Revises: 0047_source_connector_configuration_versions
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0048_job_requirement_severity"
down_revision = "0047_source_connector_configuration_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_requirements", sa.Column("severity", sa.String(length=32), nullable=True))
    op.create_check_constraint(
        "ck_job_requirements_severity",
        "job_requirements",
        "severity IS NULL OR severity IN ('MUST_HAVE', 'IMPORTANT', 'PREFERRED', 'CONTEXTUAL')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_job_requirements_severity", "job_requirements", type_="check")
    op.drop_column("job_requirements", "severity")
