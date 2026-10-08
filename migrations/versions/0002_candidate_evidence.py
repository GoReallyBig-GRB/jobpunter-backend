"""candidate evidence
Revision ID: 0002_candidate_evidence
Revises: 0001_identity_candidate
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_candidate_evidence"
down_revision = "0001_identity_candidate"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("candidate_claims",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("candidate_profile_id", sa.Uuid(), sa.ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("claim_type", sa.String(64), nullable=False),
        sa.Column("claim_content", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("effective_from", sa.DateTime(timezone=True)),
        sa.Column("effective_to", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
    )
    op.create_table("evidence_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table("evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("candidate_profile_id", sa.Uuid(), sa.ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("evidence_source_id", sa.Uuid(), sa.ForeignKey("evidence_sources.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING_REVIEW"),
        sa.Column("evidence_type", sa.String(64), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True)),
        sa.Column("effective_to", sa.DateTime(timezone=True)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
    )
    op.create_table("candidate_claim_evidence",
        sa.Column("candidate_claim_id", sa.Uuid(), sa.ForeignKey("candidate_claims.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("evidence_id", sa.Uuid(), sa.ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("relationship_type", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

def downgrade() -> None:
    op.drop_table("candidate_claim_evidence")
    op.drop_table("evidence")
    op.drop_table("evidence_sources")
    op.drop_table("candidate_claims")
