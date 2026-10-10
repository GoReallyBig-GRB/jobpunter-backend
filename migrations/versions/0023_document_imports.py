"""Persist candidate-controlled document import review state.

Revision ID: 0023_document_imports
Revises: 0022_document_storage
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_document_imports"
down_revision = "0022_document_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_imports",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "candidate_profile_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("extracted_fields", sa.JSON(), nullable=False),
        sa.Column("conflicts", sa.JSON(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "confirmed_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "saved_evidence_id",
            sa.Uuid(),
            sa.ForeignKey("evidence.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", name="uq_document_import_document"),
    )
    op.create_index(
        "ix_document_imports_candidate_status",
        "document_imports",
        ["candidate_profile_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_imports_candidate_status", table_name="document_imports")
    op.drop_table("document_imports")
