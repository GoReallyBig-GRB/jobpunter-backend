"""Record source and confirmation metadata for candidate facts.

Revision ID: 0032_candidate_profile_fact_provenance
Revises: 0031_candidate_profile_data
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0032_candidate_profile_fact_provenance"
down_revision = "0031_candidate_profile_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_profile_fact_provenance",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("candidate_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section", sa.String(length=64), nullable=False),
        sa.Column("fact_path", sa.Text(), nullable=False),
        sa.Column("fact_value", sa.JSON(), nullable=False),
        sa.Column("profile_revision", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=False),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("document_import_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_excerpt", sa.Text(), nullable=True),
        sa.Column("confidence", sa.JSON(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_profile_id"], ["candidate_profiles.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["document_import_id"], ["document_imports.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "source_type IN ('CANDIDATE_INPUT', 'DOCUMENT_IMPORT', 'EVIDENCE')",
            name="ck_candidate_profile_fact_provenance_source_type",
        ),
        sa.CheckConstraint("profile_revision >= 1", name="ck_candidate_fact_provenance_revision"),
    )
    op.create_index(
        "ix_candidate_profile_fact_provenance_lookup",
        "candidate_profile_fact_provenance",
        ["candidate_profile_id", "section", "fact_path", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_profile_fact_provenance_lookup",
        table_name="candidate_profile_fact_provenance",
    )
    op.drop_table("candidate_profile_fact_provenance")
