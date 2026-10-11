"""Persist effective AI task configuration fingerprints.

Revision ID: 0024_ai_config_fingerprint
Revises: 0023_document_imports
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_ai_config_fingerprint"
down_revision = "0023_document_imports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_executions",
        sa.Column("config_fingerprint", sa.String(64), nullable=False, server_default="unversioned"),
    )


def downgrade() -> None:
    op.drop_column("ai_executions", "config_fingerprint")
