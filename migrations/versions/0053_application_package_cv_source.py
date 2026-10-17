"""Link application package CV artifacts to approved CV Studio versions.

Revision ID: 0053_application_package_cv_source
Revises: 0052_candidate_cv_studio
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0053_application_package_cv_source"
down_revision = "0052_candidate_cv_studio"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "application_package_items",
        sa.Column(
            "source_cv_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidate_cv_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_application_package_items_cv_source",
        "application_package_items",
        ["source_cv_version_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_application_package_items_cv_source", table_name="application_package_items")
    op.drop_column("application_package_items", "source_cv_version_id")
