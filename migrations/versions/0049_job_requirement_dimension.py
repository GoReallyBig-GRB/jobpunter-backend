"""Persist the PRD requirement fit dimension separately from extraction type.

Revision ID: 0049_job_requirement_dimension
Revises: 0048_job_requirement_severity
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0049_job_requirement_dimension"
down_revision = "0048_job_requirement_severity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_requirements", sa.Column("dimension", sa.String(length=32), nullable=True))
    op.create_check_constraint(
        "ck_job_requirements_dimension",
        "job_requirements",
        "dimension IS NULL OR dimension IN ('CORE_ROLE', 'DOMAIN', 'TECHNICAL', 'SENIORITY', 'EDUCATION', 'LOCATION', 'OTHER')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_job_requirements_dimension", "job_requirements", type_="check")
    op.drop_column("job_requirements", "dimension")
