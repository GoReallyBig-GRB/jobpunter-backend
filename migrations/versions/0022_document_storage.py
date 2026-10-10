"""Store private document metadata and object references.

Revision ID: 0022_document_storage
Revises: 0021_package_review_decision
"""
from alembic import op
import sqlalchemy as sa

revision = "0022_document_storage"
down_revision = "0021_package_review_decision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "candidate_profile_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "application_package_id",
            sa.Uuid(),
            sa.ForeignKey("application_packages.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "application_package_item_id",
            sa.Uuid(),
            sa.ForeignKey("application_package_items.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("document_type", sa.String(40), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(127), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("storage_key", name="uq_documents_storage_key"),
    )
    op.create_index(
        "ix_documents_candidate_created",
        "documents",
        ["candidate_profile_id", "created_at"],
    )
    op.create_index(
        "ix_documents_package_created",
        "documents",
        ["application_package_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_documents_package_created", table_name="documents")
    op.drop_index("ix_documents_candidate_created", table_name="documents")
    op.drop_table("documents")
