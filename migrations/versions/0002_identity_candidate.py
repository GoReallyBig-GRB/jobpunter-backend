"""identity and candidate evidence

Revision ID: 0002_identity_candidate
Revises: 0001_foundation_outbox
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_identity_candidate"
down_revision = "0001_foundation_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("users", sa.Column("id", sa.UUID(), primary_key=True), sa.Column("status", sa.String(32), nullable=False), sa.Column("display_name", sa.String(255)), sa.Column("revision", sa.BigInteger(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("identities", sa.Column("id", sa.UUID(), primary_key=True), sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False), sa.Column("provider", sa.String(64), nullable=False), sa.Column("provider_subject", sa.String(255), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("provider", "provider_subject", name="uq_identity_provider_subject"))
    op.create_table("candidate_profiles", sa.Column("id", sa.UUID(), primary_key=True), sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("revision", sa.BigInteger(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("user_id", name="uq_candidate_profile_user"))
    op.create_table("evidence_sources", sa.Column("id", sa.UUID(), primary_key=True), sa.Column("source_type", sa.String(64), nullable=False), sa.Column("source_reference", sa.Text(), nullable=False), sa.Column("metadata", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("evidence", sa.Column("id", sa.UUID(), primary_key=True), sa.Column("candidate_profile_id", sa.UUID(), sa.ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False), sa.Column("evidence_source_id", sa.UUID(), sa.ForeignKey("evidence_sources.id", ondelete="RESTRICT"), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("evidence_type", sa.String(64), nullable=False), sa.Column("content", sa.JSON(), nullable=False), sa.Column("effective_from", sa.DateTime(timezone=True)), sa.Column("effective_to", sa.DateTime(timezone=True)), sa.Column("verified_at", sa.DateTime(timezone=True)), sa.Column("expires_at", sa.DateTime(timezone=True)), sa.Column("revision", sa.BigInteger(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))


def downgrade() -> None:
    op.drop_table("evidence")
    op.drop_table("evidence_sources")
    op.drop_table("candidate_profiles")
    op.drop_table("identities")
    op.drop_table("users")
