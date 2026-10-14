"""Persist candidate recommendation overrides as immutable history.

Revision ID: 0033_evaluation_overrides
Revises: 0032_candidate_fact_provenance
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0033_evaluation_overrides"
down_revision = "0032_candidate_fact_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_evaluation_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("evaluation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("original_decision", sa.String(length=32), nullable=False),
        sa.Column("new_disposition", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["evaluation_id"], ["candidate_evaluations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["candidate_profile_id"], ["candidate_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("evaluation_id", "revision", name="uq_evaluation_override_revision"),
        sa.CheckConstraint("revision >= 1", name="ck_candidate_evaluation_override_revision"),
        sa.CheckConstraint(
            "new_disposition IN ('APPLY', 'REVIEW', 'WATCH', 'REJECT')",
            name="ck_candidate_evaluation_override_disposition",
        ),
        sa.CheckConstraint("length(trim(reason)) >= 3", name="ck_candidate_evaluation_override_reason"),
    )
    op.create_index(
        "ix_candidate_evaluation_overrides_profile_evaluation_revision",
        "candidate_evaluation_overrides",
        ["candidate_profile_id", "evaluation_id", "revision"],
    )
    op.execute("""
        CREATE FUNCTION prevent_candidate_evaluation_override_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'candidate evaluation override history is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_candidate_evaluation_override_immutable
        BEFORE UPDATE OR DELETE ON candidate_evaluation_overrides
        FOR EACH ROW EXECUTE FUNCTION prevent_candidate_evaluation_override_mutation()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_candidate_evaluation_override_immutable ON candidate_evaluation_overrides")
    op.execute("DROP FUNCTION IF EXISTS prevent_candidate_evaluation_override_mutation()")
    op.drop_index(
        "ix_candidate_evaluation_overrides_profile_evaluation_revision",
        table_name="candidate_evaluation_overrides",
    )
    op.drop_table("candidate_evaluation_overrides")
