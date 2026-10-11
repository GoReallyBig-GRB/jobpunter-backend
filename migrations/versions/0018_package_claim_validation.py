"""Persist semantic claim validation reports for application package versions.

Revision ID: 0018_package_claim_validation
Revises: 0017_app_package_versions
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_package_claim_validation"
down_revision = "0017_app_package_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_package_claim_validations",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "package_id", sa.Uuid(),
            sa.ForeignKey("application_packages.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ai_execution_id", sa.Uuid(),
            sa.ForeignKey("ai_executions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("report_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_package_claim_validations_package_created",
        "application_package_claim_validations",
        ["package_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_package_claim_validations_package_created",
        table_name="application_package_claim_validations",
    )
    op.drop_table("application_package_claim_validations")
