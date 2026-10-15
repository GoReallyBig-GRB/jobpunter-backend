"""Persist candidate-owned career pathways and review history.

Revision ID: 0045_career_pathways
Revises: 0044_user_account_fields
"""
from alembic import op
import sqlalchemy as sa

revision = "0045_career_pathways"
down_revision = "0044_user_account_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "career_pathways",
        sa.Column("candidate_profile_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("role_family", sa.String(length=255), nullable=False),
        sa.Column("target_roles", sa.JSON(), nullable=False),
        sa.Column("target_seniority", sa.JSON(), nullable=False),
        sa.Column("domains", sa.JSON(), nullable=False),
        sa.Column("supporting_evidence_refs", sa.JSON(), nullable=False),
        sa.Column("gaps", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("approval_status", sa.String(length=32), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('USER_DEFINED', 'AI_SUGGESTED')",
            name="ck_career_pathways_source_type",
        ),
        sa.CheckConstraint(
            "approval_status IN ('DRAFT', 'PENDING_REVIEW', 'APPROVED', 'REJECTED', 'RETIRED')",
            name="ck_career_pathways_approval_status",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_career_pathways_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["users.id"],
            name="fk_career_pathways_approved_by_user_id_users", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_profile_id"], ["candidate_profiles.id"],
            name="fk_career_pathways_candidate_profile_id_candidate_profiles", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_career_pathways"),
    )
    op.create_index(
        "ix_career_pathways_candidate_status",
        "career_pathways",
        ["candidate_profile_id", "approval_status", "updated_at"],
        unique=False,
    )
    op.create_table(
        "career_pathway_events",
        sa.Column("career_pathway_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_profile_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("previous_status", sa.String(length=32), nullable=True),
        sa.Column("new_status", sa.String(length=32), nullable=True),
        sa.Column("pathway_revision", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"],
            name="fk_pathway_events_actor", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_profile_id"], ["candidate_profiles.id"],
            name="fk_pathway_events_profile", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["career_pathway_id"], ["career_pathways.id"],
            name="fk_pathway_events_pathway", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_career_pathway_events"),
    )
    op.create_index(
        "ix_career_pathway_events_pathway_created",
        "career_pathway_events", ["career_pathway_id", "created_at", "id"], unique=False,
    )
    op.create_index(
        "ix_career_pathway_events_candidate_created",
        "career_pathway_events", ["candidate_profile_id", "created_at"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_career_pathway_events_candidate_created", table_name="career_pathway_events")
    op.drop_index("ix_career_pathway_events_pathway_created", table_name="career_pathway_events")
    op.drop_table("career_pathway_events")
    op.drop_index("ix_career_pathways_candidate_status", table_name="career_pathways")
    op.drop_table("career_pathways")
