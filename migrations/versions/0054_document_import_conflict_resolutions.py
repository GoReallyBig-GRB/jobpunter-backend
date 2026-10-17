"""Persist candidate decisions for document-import conflicts.

Revision ID: 0054_document_import_conflict_resolutions
Revises: 0053_application_package_cv_source
"""
from alembic import op
import sqlalchemy as sa

revision = "0054_document_import_conflict_resolutions"
down_revision = "0053_application_package_cv_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_imports",
        sa.Column("conflict_resolutions", sa.JSON(), nullable=True),
    )
    op.execute("UPDATE document_imports SET conflict_resolutions = '[]'::json WHERE conflict_resolutions IS NULL")
    op.alter_column("document_imports", "conflict_resolutions", nullable=False)


def downgrade() -> None:
    op.drop_column("document_imports", "conflict_resolutions")
